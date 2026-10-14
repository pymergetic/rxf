/* C++ outside the subset, on purpose: each function names one refusal of the importer
   (asserted in test_outside_the_subset_is_refused). Nothing here is imported. */

#include <stdint.h>

struct Big {
    uint64_t a, b, c;
};

/* a by-value aggregate return: the sret pointer of the ABI (the subset returns one word) */
extern "C" Big big_make(uint64_t n) {
    return Big{n, n + 1, n + 2};
}

/* a by-value aggregate parameter: the byval copy the callee may write */
extern "C" uint64_t big_take(Big b, uint64_t x) {
    return b.a + b.b + x;
}

/* a throw: the __cxa_* runtime of the exception machinery */
extern "C" uint64_t cpp_throw(uint64_t x) {
    if (x == 0) {
        throw 42;
    }
    return x;
}

/* a try/catch: the invoke edge and its landingpad */
extern "C" uint64_t cpp_catch(uint64_t x) {
    try {
        if (x == 0) {
            throw 1;
        }
    } catch (...) {
    }
    return x;
}

struct Base {
    uint64_t n;
    virtual uint64_t f(uint64_t v) {
        return v + n;
    }
    virtual ~Base() {}
};

struct Der : Base {
    uint64_t f(uint64_t v) override {
        return v + n + 1;
    }
};

/* a virtual call: the indirect call of the vtable */
extern "C" uint64_t vcall(Base* b, uint64_t x) {
    return b->f(x);
}

/* a dynamic_cast lowers to a null compare of the pointer first (clang must answer null
   for a null cast), so the natural shape refuses as an address compare */
extern "C" uint64_t dyn(Base* b) {
    Der* p = dynamic_cast<Der*>(b);
    return p ? p->f(0) : 0;
}

/* a direct call of the RTTI runtime: refused by the runtime's name (a hand-written
   extern "C" signature of __dynamic_cast is what the ABI gives it) */
extern "C" void* __dynamic_cast(const void*, const void*, const void*, long);
extern "C" uint64_t rtti_call(uint64_t p) {
    void* r = __dynamic_cast((const void*)p, (const void*)p, (const void*)p, 0);
    return (uint64_t)(uintptr_t)r;
}
