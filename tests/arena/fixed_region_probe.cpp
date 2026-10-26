// The fixed-region build of the v7 allocator (docs/32 3.1): PYMERGETIC_RXF_FIXED_REGION is
// the hook that refuses. A native body has no growth callback, so the build refuses the ask
// even when the caller hands it one. See tests/test_object_arena.py.
#include <pymergetic/rxftk/core/arena/object_arena.hpp>

using namespace pymergetic::rxf::core::arena;

namespace {

uint8_t region[8192];
bool commit_called = false;

bool commit_ok(void *ctx, uint64_t committed) {
    (void)ctx;
    (void)committed;
    commit_called = true;
    return true;
}

} // namespace

int main() {
    Memory memory;
    memory.base = region;
    memory.committed = PYMERGETIC_RXF_SUPER_SIZE + PYMERGETIC_RXF_FREE_MIN_SPAN;
    memory.limit = sizeof(region);
    memory.commit = commit_ok;
    memory.ctx = nullptr;
    ObjectArena arena;
    if (arena.init(memory, 0, 0) != Status::Ok)
        return 1;
    /* The superblock and the end marker are inside the committed headroom. */
    if (arena.committed() != PYMERGETIC_RXF_SUPER_SIZE)
        return 2;
    commit_called = false;
    /* An ask past committed bytes refuses and never consults the hook. */
    if (arena.s_ensure(0, sizeof(region)) != Status::OutOfMemory)
        return 3;
    if (commit_called)
        return 4;
    if (arena.s_ensure(0, memory.committed) != Status::Ok)
        return 5;
    return 0;
}
