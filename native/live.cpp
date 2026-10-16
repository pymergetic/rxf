/*
 * The live layer as one native leaf: rxf_live_pump(context, area) serves the requests that wait in
 * the channel (docs/19). It is the C++ of src/pymergetic/rxftk/core/live compiled freestanding into
 * one body without relocations: everything it needs from the arena and from SHA-256 is inlined, and
 * the leaf calls nothing. The area is `area`, or, when that is 0, the address of the first
 * capability of the context (the Channel).
 */

/* The arena lives in one region that is committed whole: no commit callback to call through. */
#define PYMERGETIC_RXF_FIXED_REGION 1

/* Every function below is flattened: a leaf is one body that calls nothing. */
#pragma clang attribute push(__attribute__((flatten)), apply_to = function)
#include <pymergetic/rxftk/core/arena/arena.cpp>
#include <pymergetic/rxftk/core/live/live.cpp>
#pragma clang attribute pop

namespace {

/* The tail of rxf_runtime_context (stdlib_runtime.h): words 6 and 7 are the capabilities. */
struct Context {
    uint64_t version, size, function_count, functions, object_count, objects, capability_count,
        capabilities;
};

struct Capability {
    uint64_t id, generation, address;
};

constexpr uint64_t kChannel = 1;
constexpr uint32_t kBatch = 64;
constexpr uint64_t kPumpLeaf = 29; // RXF_LEAF_LIVE_PUMP

/* An entry of the object table of the context (stdlib_runtime.h): id at 0, payload at 24, size
 * at 32. */
struct Entry {
    uint64_t id, generation, type, payload, size, capacity, live, a, b, c;
};

#if !defined(__wasm__)
/* Instructions that were written as data must be fetched as instructions. x86_64 needs nothing. */
inline void publish_code(void *from, uint64_t size) {
#if defined(__aarch64__)
    uint64_t ctr;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    const uint64_t d = 4ULL << ((ctr >> 16) & 15), i = 4ULL << (ctr & 15);
    const uint64_t begin = reinterpret_cast<uintptr_t>(from), end = begin + size;
    for (uint64_t at = begin & ~(d - 1); at < end; at += d)
        __asm__ volatile("dc cvau, %0" ::"r"(at) : "memory");
    __asm__ volatile("dsb ish" ::: "memory");
    for (uint64_t at = begin & ~(i - 1); at < end; at += i)
        __asm__ volatile("ic ivau, %0" ::"r"(at) : "memory");
    __asm__ volatile("dsb ish\n\tisb" ::: "memory");
#else
    (void)from;
    (void)size;
#endif
}
#endif

using Leaf = uint32_t (*)(void *, void *);

/* Where the next instruction is: no symbol, so nothing that would have to be relocated. */
__attribute__((always_inline)) inline uint64_t here() {
    uint64_t pc;
#if defined(__x86_64__)
    __asm__ volatile("lea 0(%%rip), %0" : "=r"(pc));
#elif defined(__aarch64__)
    __asm__ volatile("adr %0, ." : "=r"(pc));
#else
    pc = 0;
#endif
    return pc;
}

/* Where this body starts and how long it is: the slot of the pump in the function table, and the
 * object entry of the context whose payload is there, provided that this very instruction is in it.
 * 0 when that does not hold (the pump was not called through its slot). */
uint64_t own_body(const Context *c, uint64_t *size) {
    const Capability *slots =
        reinterpret_cast<const Capability *>(static_cast<uintptr_t>(c->functions));
    const Entry *entries = reinterpret_cast<const Entry *>(static_cast<uintptr_t>(c->objects));
    const uint64_t pc = here();
    for (uint64_t k = 0; k < c->function_count; k++) {
        if (slots[k].id != kPumpLeaf)
            continue;
        for (uint64_t i = 0; i < c->object_count; i++)
            if (entries[i].payload == slots[k].address && pc >= slots[k].address &&
                pc < slots[k].address + entries[i].size) {
                *size = entries[i].size;
                return slots[k].address;
            }
    }
    return 0;
}

} // namespace

/* A compaction can move the very bytes of this body (they are an object of the arena). So the pump
 * stops before it (`relocate`), and this leaf copies itself into the log region, which no request
 * uses in the meantime, and continues there with a tail call: the original is never returned to.
 * The copy serves the one compaction and clears `relocated`; the rest of the queue waits for the
 * next call, because the log region is the copy's ground and a delta needs it. */
extern "C" __attribute__((flatten, used, visibility("default"))) uint32_t
rxf_live_pump(void *context, void *area) {
    uint64_t at = reinterpret_cast<uintptr_t>(area);
    if (at == 0 && context != nullptr) {
        const Context *c = static_cast<const Context *>(context);
        const Capability *caps =
            reinterpret_cast<const Capability *>(static_cast<uintptr_t>(c->capabilities));
        for (uint64_t i = 0; i < c->capability_count; i++)
            if (caps[i].id == kChannel)
                at = caps[i].address;
    }
    if (at == 0)
        return 13; // RXF_REFUSE_UNBOUND
    pymergetic_rxf_live_area *a =
        reinterpret_cast<pymergetic_rxf_live_area *>(static_cast<uintptr_t>(at));
#if defined(__wasm__)
    pymergetic::rxf::core::live::pump(a, kBatch, context);
    return 0;
#else
    const bool copy = a->relocated != 0;
    pymergetic::rxf::core::live::pump(a, copy ? 1 : kBatch, context, true);
    if (copy) {
        a->relocated = 0;
        return 0;
    }
    if (a->relocate != 1)
        return 0;
    uint64_t size = 0;
    const uint64_t self =
        context == nullptr ? 0 : own_body(static_cast<const Context *>(context), &size);
    const uint64_t ground = (a->log_base + 63) & ~63ULL;
    if (self == 0 || ground + size > a->log_base + a->log_capacity) {
        a->relocate =
            2; // no room, or no entry: it is served here, and refused if the code would move
        pymergetic::rxf::core::live::pump(a, kBatch, context, true);
        a->relocate = 0;
        return 0;
    }
    uint8_t *to = reinterpret_cast<uint8_t *>(static_cast<uintptr_t>(ground));
    const uint8_t *from = reinterpret_cast<const uint8_t *>(static_cast<uintptr_t>(self));
    for (uint64_t i = 0; i < size; i++)
        to[i] = from[i];
    publish_code(to, size);
    a->relocated = 1;
    a->relocate = 0;
    [[clang::musttail]] return reinterpret_cast<Leaf>(to)(context, area);
#endif
}
