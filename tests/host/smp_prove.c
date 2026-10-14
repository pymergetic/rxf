/*
 * The command of the SMP proof (docs/20, C2): run one witness program and print its result and
 * evidence as JSON. The proof itself is tests/test_smp.py, which drives this binary across
 * carrier counts, seeds and modes and compares the results; this file is only its door.
 *
 *   smp_prove --runs=256 --carriers=8 --seed=7 --steps=200 --messages=4 --flags=15
 *
 * The flags are the WIT_FLAG_* of smp_witness.h: 2 compaction, 4 delta, 8 force cross-carrier
 * resume. The words 0 to 2 of "out" are the deterministic result of the program and must be
 * equal for every carrier count and every flag set (the differential of docs/20, S10).
 */
#include "smp_witness.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint64_t arg_u64(int argc, char **argv, const char *name, uint64_t fallback) {
    size_t n = strlen(name);
    for (int i = 1; i < argc; i++)
        if (strncmp(argv[i], name, n) == 0 && argv[i][n] == '=')
            return strtoull(argv[i] + n + 1, NULL, 0);
    return fallback;
}

int main(int argc, char **argv) {
    uint32_t n_runs = (uint32_t)arg_u64(argc, argv, "--runs", 64);
    uint32_t n_carriers = (uint32_t)arg_u64(argc, argv, "--carriers", 4);
    uint32_t seed = (uint32_t)arg_u64(argc, argv, "--seed", 1);
    uint64_t steps = arg_u64(argc, argv, "--steps", 100);
    uint32_t messages = (uint32_t)arg_u64(argc, argv, "--messages", 4);
    uint32_t flags = (uint32_t)arg_u64(argc, argv, "--flags", 0);

    static uint64_t out[WIT_OUT_WORDS];
    wit_go(n_runs, n_carriers, seed, steps, messages, flags, out);

    printf("{\"runs\":%u,\"carriers\":%u,\"seed\":%u,\"steps\":%" PRIu64
           ",\"messages\":%u,\"flags\":%u,\"out\":[",
           n_runs, n_carriers, seed, steps, messages, flags);
    for (uint32_t i = 0; i < WIT_OUT_WORDS; i++)
        printf("%s%" PRIu64, i ? "," : "", out[i]);
    printf("]}\n");
    return 0;
}
