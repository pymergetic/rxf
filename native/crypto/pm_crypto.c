/* The C fill of the cap.crypto card: the one ABI of include/pm_crypto.h over the words of
 * native/crypto (the .rxfs words) -- which are the ONE defining source of every primitive
 * (docs/25 §11). There is no cryptography in this file. It is the host loader of the words,
 * and it does three things:
 *
 *   1. It embeds the words of the seven programs (build/crypto_words.c, compiled from the
 *      .rxfs sources by tools/native/crypto_build.py at build time -- bytes, never a second
 *      copy of the program).
 *   2. At init it hands each program to the in-image compiler (native/compile.c, docs/18),
 *      so the calls run as the native code of the machine when the compiler takes the
 *      program. A program the compiler refuses stays on the interpreter of native/graph.c:
 *      the same words, the same answer, just slower.
 *   3. Each ABI call stages its buffers in the flat memory a run lives in and runs the
 *      program that faces it -- the same (status, value) shape the tests run, and the same
 *      words every other machine runs (docs/07, docs/25 §11).
 *
 * The module pair is the one the ABI names: pm_crypto_init compiles, pm_crypto_deinit
 * releases, and the calls in between keep no state of their own (each stages its own
 * scratch, so calls do not touch each other). The words keep the constant-time discipline
 * of the kernels; this fill adds none and takes none away.
 */
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#include "../basics.h"
#include "../compile.h"
#include "../graph.h"
#include "../stdlib_memory.h"
#include "../stdlib_runtime.h"

#include "include/pm_crypto.h"
#include "words.h"

/* The safe point of compiled code (native/safe.c): a back edge rings it with the area of the
 * Channel of the context. */
rxf_u32 rxf_safe_point(rxf_runtime_context *, void *);

/* The error the ABI names (include/pm_crypto.h), and the refusals of the words. */
#define PM_OK 0
#define PM_ERR_ARGUMENT (-1)
#define PM_ERR_TAG (-2)
#define PM_ERR_LOW_ORDER (-3)
#define PM_ERR_LENGTH (-4)

/* Where a run lives in the flat memory it is given: the frame of the run where the tests
 * keep it (tests/rxf_sim.py, tests/host/runner.c), and the buffers of a call beyond that. */
#define PM_MEM_FRAME_AT 0x100000ULL
#define PM_MEM_DATA_AT 0x200000ULL
/* The work region every program of the card asks for (the words claim at most 1280). */
#define PM_MEM_WORK 2048ULL
/* The area of the Channel capability a run's safe points ring (native/safe.c): the rings
 * start at word 30 of it and nothing rings here, so the doors are just quiet. */
#define PM_AREA_BYTES 320U

/* The faces of the words, exactly as the programs name them. */
#define PM_X25519_SECRET 1
#define PM_X25519_PUBLIC 2
#define PM_CHACHA_SEAL 4
#define PM_CHACHA_OPEN 5
#define PM_AES_SEAL 2
#define PM_AES_OPEN 3
#define PM_HKDF_EXTRACT 1
#define PM_HKDF_EXPAND 2
/* RFC 5869 §2.3: one byte of counter counts at most this much output. */
#define PM_HKDF_MAX 8160ULL

/* The words of the programs (build/crypto_words.c). */

/* The compiled shape of a program: what the interpreter is, with the handle gone
 * (native/compile.h). */
typedef rxf_u32 (*pm_entry_fn)(rxf_runtime_context *, rxf_u64, rxf_u64, rxf_u64 *);

#define PM_LEAVES 48

/* One program of the card: its words, its tables, and its code once the compiler took it.
 * Everything here is written at init and read afterwards; a run mutates nothing of it. */
typedef struct {
    const rxf_u64 *words;
    rxf_u64 words_bytes;
    rxf_native_slot functions[PM_LEAVES];
    rxf_object_entry objects[1];
    rxf_native_slot capabilities[1];
    rxf_u8 area[PM_AREA_BYTES];
    rxf_runtime_context context;
    rxf_object_handle handle;
    pm_entry_fn entry; /* the compiled program, or 0: it runs on the interpreter */
    void *code;
    rxf_u64 code_bytes;
    void *scratch; /* the two-pass room of the compiler */
    rxf_u64 scratch_bytes;
} pm_program;

static pm_program p_sha256, p_blake2s, p_chacha, p_aes, p_hkdf, p_hkdf_b2s, p_x25519;
static int pm_ready;

/* ---- the leaves the words call ---------------------------------------------------------------- */

rxf_u32 rxf_wrapping_add_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_wrapping_subtract_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_and_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_or_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_not_uint64_t(rxf_u64, rxf_u64 *);
rxf_u32 rxf_equal_uint64_t(rxf_u64, rxf_u64, rxf_u8 *);
rxf_u32 rxf_less_uint64_t(rxf_u64, rxf_u64, rxf_u8 *);
rxf_u32 rxf_shift_left_uint64_t(rxf_u64, rxf_u32, rxf_u64 *);
rxf_u32 rxf_shift_right_uint64_t(rxf_u64, rxf_u32, rxf_u64 *);
rxf_u32 rxf_checked_add_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_checked_subtract_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_checked_multiply_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_divide_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_remainder_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_divide_int32_t(rxf_i32, rxf_i32, rxf_i32 *);
rxf_u32 rxf_remainder_int32_t(rxf_i32, rxf_i32, rxf_i32 *);
rxf_u32 rxf_wrapping_multiply_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_checked_multiply_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_divide_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_remainder_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_checked_multiply_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
rxf_u32 rxf_divide_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
rxf_u32 rxf_remainder_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
rxf_u32 rxf_hash_u64(rxf_u64, rxf_u64, rxf_u64 *);

/* The table the leaves of one program find their C by. find_leaf walks it (native/graph.c),
 * so the words of a hash come first -- it is the hottest walk. */
static void leaves_of(pm_program *p) {
    static const struct {
        rxf_u64 id;
        void *address;
    } leaves[] = {
        {RXF_LEAF_WRAPPING_ADD_UINT64_T, (void *)rxf_wrapping_add_uint64_t},
        {RXF_LEAF_MEMORY_COPY, (void *)rxf_memory_copy},
        {RXF_LEAF_WRAPPING_SUBTRACT_UINT64_T, (void *)rxf_wrapping_subtract_uint64_t},
        {RXF_LEAF_BIT_AND_UINT64_T, (void *)rxf_bit_and_uint64_t},
        {RXF_LEAF_BIT_OR_UINT64_T, (void *)rxf_bit_or_uint64_t},
        {RXF_LEAF_SHIFT_LEFT_UINT64_T, (void *)rxf_shift_left_uint64_t},
        {RXF_LEAF_SHIFT_RIGHT_UINT64_T, (void *)rxf_shift_right_uint64_t},
        {RXF_LEAF_EQUAL_UINT64_T, (void *)rxf_equal_uint64_t},
        {RXF_LEAF_LESS_UINT64_T, (void *)rxf_less_uint64_t},
        {RXF_LEAF_MEMORY_FILL, (void *)rxf_memory_fill},
        {RXF_LEAF_MEMORY_COMPARE, (void *)rxf_memory_compare},
        {RXF_LEAF_BIT_NOT_UINT64_T, (void *)rxf_bit_not_uint64_t},
        {RXF_LEAF_HASH_U64, (void *)rxf_hash_u64},
        {RXF_LEAF_CHECKED_ADD_UINT32_T, (void *)rxf_checked_add_uint32_t},
        {RXF_LEAF_CHECKED_SUBTRACT_UINT32_T, (void *)rxf_checked_subtract_uint32_t},
        {RXF_LEAF_CHECKED_MULTIPLY_UINT32_T, (void *)rxf_checked_multiply_uint32_t},
        {RXF_LEAF_DIVIDE_UINT32_T, (void *)rxf_divide_uint32_t},
        {RXF_LEAF_REMAINDER_UINT32_T, (void *)rxf_remainder_uint32_t},
        {RXF_LEAF_DIVIDE_INT32_T, (void *)rxf_divide_int32_t},
        {RXF_LEAF_REMAINDER_INT32_T, (void *)rxf_remainder_int32_t},
        {RXF_LEAF_WRAPPING_MULTIPLY_UINT64_T, (void *)rxf_wrapping_multiply_uint64_t},
        {RXF_LEAF_CHECKED_MULTIPLY_UINT64_T, (void *)rxf_checked_multiply_uint64_t},
        {RXF_LEAF_CHECKED_MULTIPLY_INT64_T, (void *)rxf_checked_multiply_int64_t},
        {RXF_LEAF_DIVIDE_UINT64_T, (void *)rxf_divide_uint64_t},
        {RXF_LEAF_REMAINDER_UINT64_T, (void *)rxf_remainder_uint64_t},
        {RXF_LEAF_DIVIDE_INT64_T, (void *)rxf_divide_int64_t},
        {RXF_LEAF_REMAINDER_INT64_T, (void *)rxf_remainder_int64_t},
        {RXF_LEAF_RUNTIME_HASH_MAP_LOOKUP, (void *)rxf_runtime_hash_map_lookup},
        {RXF_LEAF_RUNTIME_HASH_SET_LOOKUP, (void *)rxf_runtime_hash_set_lookup},
        {RXF_LEAF_RUNTIME_BEGIN_PRIVATE, (void *)rxf_runtime_begin_private},
        {RXF_LEAF_RUNTIME_RESOLVE, (void *)rxf_runtime_resolve},
        {RXF_LEAF_RUNTIME_ALLOCATE_PREPARE, (void *)rxf_runtime_allocate_prepare},
        {RXF_LEAF_RUNTIME_PUBLISH, (void *)rxf_runtime_publish},
        {RXF_LEAF_RUNTIME_ROLLBACK, (void *)rxf_runtime_rollback},
        {RXF_LEAF_RUNTIME_BORROW, (void *)rxf_runtime_borrow},
        {RXF_LEAF_RUNTIME_RELEASE_BORROW, (void *)rxf_runtime_release_borrow},
        {RXF_LEAF_RUNTIME_PIN, (void *)rxf_runtime_pin},
        {RXF_LEAF_RUNTIME_UNPIN, (void *)rxf_runtime_unpin},
        {RXF_LEAF_RUNTIME_READ, (void *)rxf_runtime_read},
        {RXF_LEAF_RUNTIME_WRITE, (void *)rxf_runtime_write},
        {RXF_LEAF_RUNTIME_APPEND, (void *)rxf_runtime_append},
        {RXF_LEAF_RUNTIME_FORMAT_U32, (void *)rxf_runtime_format_u32},
        {RXF_LEAF_RUNTIME_SEARCH, (void *)rxf_runtime_search},
        {RXF_LEAF_RUNTIME_CLEANUP, (void *)rxf_runtime_cleanup},
        {RXF_LEAF_SAFE_POINT, (void *)rxf_safe_point},
    };
    memset(p->functions, 0, sizeof(p->functions));
    for (rxf_u64 i = 0; i < sizeof(leaves) / sizeof(leaves[0]); i++) {
        p->functions[i].id = leaves[i].id;
        p->functions[i].generation = 1;
        p->functions[i].address = (rxf_u64)(unsigned long)leaves[i].address;
    }
}

/* ---- one program, opened and compiled --------------------------------------------------------- */

static void program_open(pm_program *p, const char *name) {
    const pm_words *w;
    for (w = pm_words_table; w->name; w++)
        if (!strcmp(w->name, name))
            break;
    if (!w->name) {
        memset(p, 0, sizeof(*p));
        return;
    }
    memset(p, 0, sizeof(*p));
    p->words = w->words;
    p->words_bytes = w->words_bytes;
    leaves_of(p);
    p->objects[0].id = 1;
    p->objects[0].generation = 1;
    p->objects[0].payload = (rxf_u64)(unsigned long)p->words;
    p->objects[0].size = p->words_bytes;
    p->objects[0].capacity = p->words_bytes;
    p->objects[0].state = RXF_OBJECT_LIVE;
    p->capabilities[0].id = RXF_CAPABILITY_CHANNEL;
    p->capabilities[0].generation = 1;
    p->capabilities[0].address = (rxf_u64)(unsigned long)p->area;
    p->context.version = RXF_RUNTIME_ABI_VERSION;
    p->context.size = sizeof(rxf_runtime_context);
    p->context.function_count = sizeof(p->functions) / sizeof(p->functions[0]);
    p->context.functions = (rxf_u64)(unsigned long)p->functions;
    p->context.object_count = 1;
    p->context.objects = (rxf_u64)(unsigned long)p->objects;
    p->context.capability_count = 1;
    p->context.capabilities = (rxf_u64)(unsigned long)p->capabilities;
    p->handle.object_id = 1;
    p->handle.generation = 1;
    p->handle.offset = 0;
}

/* The in-image compiler takes the program into machine code where it is put (docs/18). A
 * program it refuses (a word it cannot hold with a machine's shape) stays on the
 * interpreter: the words are the same either way. The code room is mapped, not malloc'd:
 * machine code runs where it is put, so the page is written once and then read+execute. */
static void program_compile(pm_program *p) {
    if (!p->words)
        return;
    p->scratch_bytes = p->words_bytes; /* 8 bytes for each word of the program */
    p->scratch = malloc(p->scratch_bytes);
    if (!p->scratch)
        return;
    for (rxf_u64 bytes = 1u << 16;;) {
        void *room = mmap(0, (size_t)bytes, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        if (room == MAP_FAILED)
            return;
        rxf_compile_request r;
        memset(&r, 0, sizeof(r));
        r.program = (rxf_u64)(unsigned long)p->words;
        r.program_size = p->words_bytes;
        r.out = (rxf_u64)(unsigned long)room;
        r.out_capacity = bytes;
        r.work = (rxf_u64)(unsigned long)p->scratch;
        r.work_capacity = p->scratch_bytes;
        rxf_u32 status = rxf_compile_program(&p->context, &r);
        if (r.out_size <= bytes) {
            if (status != RXF_OK) {
                munmap(room, (size_t)bytes);
                return; /* the compiler refused the program: it runs on the interpreter */
            }
            if (mprotect(room, (size_t)bytes, PROT_READ | PROT_EXEC) != 0) {
                munmap(room, (size_t)bytes);
                return;
            }
            p->code = room;
            p->code_bytes = bytes;
            p->entry = (pm_entry_fn)(unsigned long)room;
            return;
        }
        /* the room was too small (the compiler says 9 with what it takes, "also when the
         * room is too small"): map what the code needs and compile it again */
        munmap(room, (size_t)bytes);
        bytes = r.out_size;
    }
}

static void program_close(pm_program *p) {
    if (p->code) {
        munmap(p->code, (size_t)p->code_bytes);
        p->code = 0;
    }
    if (p->scratch) {
        free(p->scratch);
        p->scratch = 0;
    }
    p->entry = 0;
}

/* One run of a program on arguments of whole words: what tests/rxf_native.py runs, with the
 * code the compiler made when it made some. */
static rxf_u32 program_run(pm_program *p, const rxf_u64 *args, rxf_u64 argc, rxf_u64 *out) {
    *out = 0;
    if (!p->words)
        return RXF_REFUSE_UNBOUND;
    if (p->entry)
        return p->entry(&p->context, (rxf_u64)(unsigned long)args, argc, out);
    return rxf_graph_run(&p->context, p->handle, (rxf_u64)(unsigned long)args, argc, out);
}

/* ---- the flat memory of a call ---------------------------------------------------------------- */

typedef struct {
    rxf_u8 *bytes;
    rxf_u64 base;
    rxf_u64 size;
    rxf_u64 next;
} pm_memory;

static int memory_open(pm_memory *m, rxf_u64 want) {
    m->size = PM_MEM_DATA_AT + want + PM_MEM_WORK + 64;
    m->bytes = calloc(1, m->size);
    if (!m->bytes)
        return 0;
    m->base = (rxf_u64)(unsigned long)m->bytes;
    m->next = PM_MEM_DATA_AT;
    return 1;
}

static rxf_u64 memory_place(pm_memory *m, const void *data, rxf_u64 len) {
    rxf_u64 at = m->next;
    if (data && len)
        memcpy(m->bytes + at, data, (size_t)len);
    m->next = at + (len ? len : 1);
    return m->base + at;
}

static rxf_u64 memory_take(pm_memory *m, rxf_u64 len) {
    return memory_place(m, 0, len);
}

static rxf_u64 memory_work(pm_memory *m) {
    /* the work region always ends the block: past every buffer of the call */
    rxf_u64 at = (m->next + 63) & ~63ULL;
    if (at + PM_MEM_WORK > m->size)
        at = m->size - PM_MEM_WORK;
    m->next = at + PM_MEM_WORK;
    return m->base + at;
}

static void memory_close(pm_memory *m) {
    free(m->bytes);
    m->bytes = 0;
}

static void memory_read(pm_memory *m, rxf_u64 address, void *out, rxf_u64 len) {
    memcpy(out, m->bytes + (address - m->base), (size_t)len);
}

/* ---- the module pair -------------------------------------------------------------------------- */

int32_t pm_crypto_init(void) {
    if (pm_ready)
        return PM_OK;
    program_open(&p_sha256, "sha256");
    program_open(&p_blake2s, "blake2s");
    program_open(&p_chacha, "chacha20poly1305");
    program_open(&p_aes, "aes_gcm");
    program_open(&p_hkdf, "hkdf");
    program_open(&p_hkdf_b2s, "hkdf_blake2s");
    program_open(&p_x25519, "x25519");
    if (!p_sha256.words || !p_blake2s.words || !p_chacha.words || !p_aes.words ||
        !p_hkdf.words || !p_hkdf_b2s.words || !p_x25519.words) {
        pm_crypto_deinit();
        return PM_ERR_ARGUMENT;
    }
    program_compile(&p_sha256);
    program_compile(&p_blake2s);
    program_compile(&p_chacha);
    program_compile(&p_aes);
    program_compile(&p_hkdf);
    program_compile(&p_hkdf_b2s);
    program_compile(&p_x25519);
    /* the SHA-256 self-check the ABI asks of init (FIPS 180-4, "abc"). The card is
     * already open at this point -- the check RUNS the card, so the door must be one
     * it does not knock on again. */
    pm_ready = 1;
    static const uint8_t abc[3] = {'a', 'b', 'c'};
    uint8_t digest[32];
    pm_crypto_sha256(digest, abc, 3);
    static const uint8_t want[32] = {
        0xba, 0x78, 0x16, 0xbf, 0x8f, 0x01, 0xcf, 0xea, 0x41, 0x41, 0x40,
        0xde, 0x5d, 0xae, 0x22, 0x23, 0xb0, 0x03, 0x61, 0xa3, 0x96, 0x17,
        0x7a, 0x9c, 0xb4, 0x10, 0xff, 0x61, 0xf2, 0x00, 0x15, 0xad};
    if (memcmp(digest, want, 32) != 0) {
        pm_crypto_deinit();
        return PM_ERR_ARGUMENT;
    }
    return PM_OK;
}

void pm_crypto_deinit(void) {
    program_close(&p_sha256);
    program_close(&p_blake2s);
    program_close(&p_chacha);
    program_close(&p_aes);
    program_close(&p_hkdf);
    program_close(&p_hkdf_b2s);
    program_close(&p_x25519);
    pm_ready = 0;
}

/* The card is loaded before it is used; a call that finds it cold loads it. */
static int32_t ready(void) {
    return pm_ready ? PM_OK : pm_crypto_init();
}

/* ---- the hashes ------------------------------------------------------------------------------- */

void pm_crypto_sha256(uint8_t out[32], const uint8_t *data, size_t len) {
    pm_memory m;
    if (!out || (len && !data) || ready() != PM_OK || !memory_open(&m, len + 32)) {
        if (out)
            memset(out, 0, 32);
        return;
    }
    rxf_u64 a_data = memory_place(&m, data, len);
    rxf_u64 a_out = memory_take(&m, 32);
    rxf_u64 args[4] = {a_data, (rxf_u64)len, a_out, memory_work(&m)};
    rxf_u64 value;
    if (program_run(&p_sha256, args, 4, &value) == RXF_OK)
        memory_read(&m, a_out, out, 32);
    else
        memset(out, 0, 32);
    memory_close(&m);
}

void pm_crypto_blake2s(uint8_t out[32], const uint8_t *data, size_t len) {
    pm_memory m;
    if (!out || (len && !data) || ready() != PM_OK || !memory_open(&m, len + 32)) {
        if (out)
            memset(out, 0, 32);
        return;
    }
    rxf_u64 a_data = memory_place(&m, data, len);
    rxf_u64 a_out = memory_take(&m, 32);
    rxf_u64 args[7] = {0, 0, a_data, (rxf_u64)len, a_out, 32, memory_work(&m)};
    rxf_u64 value;
    if (program_run(&p_blake2s, args, 7, &value) == RXF_OK)
        memory_read(&m, a_out, out, 32);
    else
        memset(out, 0, 32);
    memory_close(&m);
}

void pm_crypto_blake2s_keyed(uint8_t *out, size_t out_len, const uint8_t *key, size_t key_len,
                             const uint8_t *data, size_t len) {
    pm_memory m;
    if (!out || !out_len || out_len > 32 || !key || !key_len || key_len > 32 || (len && !data) ||
        ready() != PM_OK || !memory_open(&m, key_len + len + out_len))
        return;
    rxf_u64 a_key = memory_place(&m, key, key_len);
    rxf_u64 a_data = memory_place(&m, data, len);
    rxf_u64 a_out = memory_take(&m, out_len);
    rxf_u64 args[7] = {a_key, (rxf_u64)key_len, a_data, (rxf_u64)len,
                       a_out, (rxf_u64)out_len, memory_work(&m)};
    rxf_u64 value;
    if (program_run(&p_blake2s, args, 7, &value) == RXF_OK)
        memory_read(&m, a_out, out, out_len);
    memory_close(&m);
}

/* ---- X25519 (RFC 7748) ------------------------------------------------------------------------ */

static int32_t x25519_run(uint8_t out[32], const uint8_t scalar[32], const uint8_t *point,
                          rxf_u64 op) {
    pm_memory m;
    if (!out || !scalar || ready() != PM_OK || !memory_open(&m, 32 + 32 + 32))
        return PM_ERR_ARGUMENT;
    rxf_u64 a_scalar = memory_place(&m, scalar, 32);
    rxf_u64 a_point = memory_place(&m, point, 32);
    rxf_u64 a_out = memory_take(&m, 32);
    rxf_u64 args[5] = {op, a_scalar, a_point, a_out, memory_work(&m)};
    rxf_u64 value;
    rxf_u32 status = program_run(&p_x25519, args, 5, &value);
    if (status == RXF_OK)
        memory_read(&m, a_out, out, 32);
    memory_close(&m);
    return status == RXF_OK ? PM_OK : PM_ERR_LOW_ORDER;
}

void pm_crypto_x25519_public(uint8_t out[32], const uint8_t scalar[32]) {
    (void)x25519_run(out, scalar, 0, PM_X25519_PUBLIC);
}

int32_t pm_crypto_x25519(uint8_t out[32], const uint8_t scalar[32], const uint8_t point[32]) {
    if (!point)
        return PM_ERR_ARGUMENT;
    return x25519_run(out, scalar, point, PM_X25519_SECRET);
}

/* ---- AEAD (the two flavors of one face) -------------------------------------------------------- */

static int32_t aead_run(pm_program *p, rxf_u64 op, uint8_t *out, size_t *out_len,
                        const uint8_t key[32], const uint8_t nonce[12], const uint8_t *aad,
                        size_t aad_len, const uint8_t *text, size_t text_len, size_t produced,
                        int chacha) {
    pm_memory m;
    if (ready() != PM_OK || !memory_open(&m, 32 + 12 + aad_len + text_len + produced))
        return PM_ERR_ARGUMENT;
    rxf_u64 a_key = memory_place(&m, key, 32);
    rxf_u64 a_nonce = memory_place(&m, nonce, 12);
    rxf_u64 a_aad = memory_place(&m, aad, aad_len);
    rxf_u64 a_text = memory_place(&m, text, text_len);
    rxf_u64 a_out = memory_take(&m, produced);
    rxf_u64 args[10];
    rxf_u64 argc;
    if (chacha) {
        rxf_u64 staged[10] = {op,   a_key,  a_nonce, 0,        a_aad,
                              (rxf_u64)aad_len, a_text, (rxf_u64)text_len, a_out, memory_work(&m)};
        memcpy(args, staged, sizeof(staged));
        argc = 10;
    } else {
        rxf_u64 staged[9] = {op,   a_key,  a_nonce, a_aad,
                             (rxf_u64)aad_len, a_text, (rxf_u64)text_len, a_out, memory_work(&m)};
        memcpy(args, staged, sizeof(staged));
        argc = 9;
    }
    rxf_u64 value;
    rxf_u32 status = program_run(p, args, argc, &value);
    if (status == RXF_OK) {
        memory_read(&m, a_out, out, produced);
        *out_len = produced;
        memory_close(&m);
        return PM_OK;
    }
    memory_close(&m);
    return PM_ERR_TAG;
}

int32_t pm_crypto_aead_seal(int32_t cipher, uint8_t *out, size_t *out_len, const uint8_t key[32],
                            const uint8_t nonce[12], const uint8_t *aad, size_t aad_len,
                            const uint8_t *pt, size_t pt_len) {
    if (!out || !out_len || !key || !nonce || (aad_len && !aad) || (pt_len && !pt))
        return PM_ERR_ARGUMENT;
    size_t produced = pt_len + 16;
    if (cipher == 0)
        return aead_run(&p_chacha, PM_CHACHA_SEAL, out, out_len, key, nonce, aad, aad_len, pt,
                        pt_len, produced, 1);
    if (cipher == 1)
        return aead_run(&p_aes, PM_AES_SEAL, out, out_len, key, nonce, aad, aad_len, pt, pt_len,
                        produced, 0);
    return PM_ERR_ARGUMENT;
}

int32_t pm_crypto_aead_open(int32_t cipher, uint8_t *out, size_t *out_len, const uint8_t key[32],
                            const uint8_t nonce[12], const uint8_t *aad, size_t aad_len,
                            const uint8_t *ct, size_t ct_len) {
    if (!out || !out_len || !key || !nonce || (aad_len && !aad) || (ct_len && !ct))
        return PM_ERR_ARGUMENT;
    if (ct_len < 16)
        return PM_ERR_ARGUMENT;
    size_t produced = ct_len - 16;
    if (cipher == 0)
        return aead_run(&p_chacha, PM_CHACHA_OPEN, out, out_len, key, nonce, aad, aad_len, ct,
                        ct_len, produced, 1);
    if (cipher == 1)
        return aead_run(&p_aes, PM_AES_OPEN, out, out_len, key, nonce, aad, aad_len, ct, ct_len,
                        produced, 0);
    return PM_ERR_ARGUMENT;
}

/* ---- HKDF (RFC 5869 over the two hashes) ------------------------------------------------------ */

static pm_program *hkdf_program(int32_t hash) {
    if (hash == 0)
        return &p_hkdf;
    if (hash == 1)
        return &p_hkdf_b2s;
    return 0;
}

int32_t pm_crypto_hkdf_extract(int32_t hash, uint8_t out[32], const uint8_t *salt, size_t salt_len,
                               const uint8_t *ikm, size_t ikm_len) {
    pm_program *p = hkdf_program(hash);
    pm_memory m;
    if (!p || !out || (salt_len && !salt) || (ikm_len && !ikm) || ready() != PM_OK ||
        !memory_open(&m, salt_len + ikm_len + 32))
        return PM_ERR_ARGUMENT;
    rxf_u64 a_salt = memory_place(&m, salt, salt_len);
    rxf_u64 a_ikm = memory_place(&m, ikm, ikm_len);
    rxf_u64 a_out = memory_take(&m, 32);
    rxf_u64 args[11] = {PM_HKDF_EXTRACT, a_salt,          (rxf_u64)salt_len,
                        a_ikm,           (rxf_u64)ikm_len, 0,
                        0,               0,                a_out,
                        32,              memory_work(&m)};
    rxf_u64 value;
    rxf_u32 status = program_run(p, args, 11, &value);
    if (status == RXF_OK)
        memory_read(&m, a_out, out, 32);
    memory_close(&m);
    return status == RXF_OK ? PM_OK : PM_ERR_ARGUMENT;
}

int32_t pm_crypto_hkdf_expand(int32_t hash, uint8_t *out, size_t out_len, const uint8_t prk[32],
                              const uint8_t *info, size_t info_len) {
    pm_program *p = hkdf_program(hash);
    pm_memory m;
    if (!p || !out || !prk || (info_len && !info) || ready() != PM_OK)
        return PM_ERR_ARGUMENT;
    if (out_len > PM_HKDF_MAX)
        return PM_ERR_LENGTH;
    if (!memory_open(&m, 32 + info_len + out_len))
        return PM_ERR_ARGUMENT;
    rxf_u64 a_prk = memory_place(&m, prk, 32);
    rxf_u64 a_info = memory_place(&m, info, info_len);
    rxf_u64 a_out = memory_take(&m, out_len);
    rxf_u64 args[11] = {PM_HKDF_EXPAND,   0,   0,   0,
                        0,                a_prk, a_info, (rxf_u64)info_len,
                        a_out,            (rxf_u64)out_len, memory_work(&m)};
    rxf_u64 value;
    rxf_u32 status = program_run(p, args, 11, &value);
    if (status == RXF_OK)
        memory_read(&m, a_out, out, out_len);
    memory_close(&m);
    return status == RXF_OK ? PM_OK : PM_ERR_LENGTH;
}
