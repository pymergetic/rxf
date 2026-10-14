/* A wide struct global (the shape of MicroPython's mp_state_ctx): one cell whose payload
   is the struct bytes. The field accesses walk both forms of the address the LLVM IR
   gives a static: a getelementptr instruction with a moving index, and the constant
   expression a constant field offset compiles to (ctx.top and ctx.limit below). */
#include <stdint.h>

struct state_ctx {
    uint64_t words[8]; /* the words of the state */
    uint64_t top;      /* a constant field offset: a constant-expression getelementptr */
    uint64_t limit;
};

static struct state_ctx ctx = {{1, 2, 3, 4, 5, 6, 7, 8}, 10, 20};

uint64_t ctx_bump(uint64_t index, uint64_t by) {
    uint64_t slot = index & 7;
    ctx.words[slot] += by; /* a moving index: a getelementptr instruction */
    ctx.top += by;         /* a constant offset: a getelementptr constant expression */
    return ctx.words[slot];
}

uint64_t ctx_read(uint64_t index) {
    return ctx.words[index & 7];
}

uint64_t ctx_top(void) {
    return ctx.top;
}

uint64_t ctx_limit(void) {
    return ctx.limit;
}
