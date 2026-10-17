/*
 * The probe of the activation seam's primitives (tests/test_wrap_crypto.py compiles
 * this with the pinned Clang and runs it): it runs the pinned vectors and answers
 * with their failure count -- 0 when every vector passed. The gate of the crypto
 * sits here, in the code's own seat, not in a Python echo of it.
 */
#include "wrap_crypto.h"

int main(void) { return (int)rxf_wrap_crypto_vectors(); }
