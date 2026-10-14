#include <stdint.h>

/* py/smallint.c of MicroPython end to end: the small-int binary operations of
   py/runtime.c's mp_binary_op -- the modulo that takes the sign of the second operand,
   the floor division that adjusts the numerator before one truncating divide, and the
   checked multiply (py/runtime_utils.c's mp_mul_mp_int_t_overflow, the CERT INT32-C
   check that is mp_small_int_multiply of older smallint.c). mp_int_t is the signed word
   of the port: int64_t here, and the int32_t forms below are a 32-bit port's own (their
   divides and remainders take the 32-bit leaves of native/graph.h, ids 25 and 46-48).

   The bodies are the MicroPython ones with the names of the types kept local. The tree
   answers the multiply flag as a bool; here it is the word 0/1 the differential's ctypes
   reads. The differential runs only the inputs the C defines -- a divisor of zero and
   INT_MIN / -1 are a SIGFPE natively, not an answer to compare against -- and the
   checked multiply, whose every division is bounded by the checks themselves, runs every
   pair (tests/test_import.py keeps the pairs). */

typedef int64_t mp_int_t;
#define MP_INT_MAX INT64_MAX
#define MP_INT_MIN INT64_MIN

mp_int_t mp_small_int_modulo(mp_int_t dividend, mp_int_t divisor) {
    // Python specs require that mod has same sign as second operand
    dividend %= divisor;
    if ((dividend < 0 && divisor > 0) || (dividend > 0 && divisor < 0)) {
        dividend += divisor;
    }
    return dividend;
}

mp_int_t mp_small_int_floor_divide(mp_int_t num, mp_int_t denom) {
    if (num >= 0) {
        if (denom < 0) {
            num += -denom - 1;
        }
    } else {
        if (denom >= 0) {
            num += -denom + 1;
        }
    }
    return num / denom;
}

/* the checked multiply of py/runtime_utils.c (CERT INT32-C); the answer is the flag and
   the product goes out through its pointer, like every mp_* that hands a word over */
int64_t mp_small_int_multiply(mp_int_t x, mp_int_t y, mp_int_t *res) {
    if (x > 0) { // x is positive
        if (y > 0) { // x and y are positive
            if (x > (MP_INT_MAX / y)) {
                return 1;
            }
        } else { // x positive, y nonpositive
            if (y < (MP_INT_MIN / x)) {
                return 1;
            }
        } // x positive, y nonpositive
    } else { // x is nonpositive
        if (y > 0) { // x is nonpositive, y is positive
            if (x < (MP_INT_MIN / y)) {
                return 1;
            }
        } else { // x and y are nonpositive
            if (x != 0 && y < (MP_INT_MAX / x)) {
                return 1;
            }
        } // End if x and y are nonpositive
    } // End if x is nonpositive

    // Result doesn't overflow
    *res = x * y;
    return 0;
}

/* a 32-bit port: the same two functions on its mp_int_t, and the unsigned pair of the
   u32 repr (a quotient out through the pointer, the remainder as the answer) */
typedef int32_t mp_int32_t;

mp_int32_t mp_small_int_modulo32(mp_int32_t dividend, mp_int32_t divisor) {
    // Python specs require that mod has same sign as second operand
    dividend %= divisor;
    if ((dividend < 0 && divisor > 0) || (dividend > 0 && divisor < 0)) {
        dividend += divisor;
    }
    return dividend;
}

mp_int32_t mp_small_int_floor_divide32(mp_int32_t num, mp_int32_t denom) {
    if (num >= 0) {
        if (denom < 0) {
            num += -denom - 1;
        }
    } else {
        if (denom >= 0) {
            num += -denom + 1;
        }
    }
    return num / denom;
}

uint32_t mp_small_int_divmod_u32(uint32_t num, uint32_t denom, uint32_t *quotient) {
    *quotient = num / denom;
    return num % denom;
}
