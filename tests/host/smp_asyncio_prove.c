/*
 * The command of the asyncio binding proof (docs/23 4.5): read one program in the text form of
 * smp_asyncio.h on stdin, run it on N carriers over the ready-ring, print the report. The proof
 * itself is tests/test_smp_asyncio.py, which feeds the same corpus to MicroPython's real asyncio
 * and to this binary and compares.
 *
 *   smp_asyncio_prove --carriers=4 --flags=1 < program.txt
 *
 * The lines "task I STATE RESULT" and "trace T:V ..." are the report (the format of
 * tests/asyncio_corpus/reference.py); the line "evidence k=v ..." is what the interleaving did and
 * is never compared as a result.
 */
#include "smp_asyncio.h"

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

static char *read_all(FILE *in) {
    size_t cap = 4096, len = 0;
    char *text = (char *)malloc(cap);
    if (!text)
        return NULL;
    for (;;) {
        size_t got = fread(text + len, 1, cap - len - 1, in);
        len += got;
        if (got == 0)
            break;
        if (len + 1 >= cap) {
            cap *= 2;
            char *grown = (char *)realloc(text, cap);
            if (!grown) {
                free(text);
                return NULL;
            }
            text = grown;
        }
    }
    text[len] = '\0';
    return text;
}

int main(int argc, char **argv) {
    uint32_t carriers = (uint32_t)arg_u64(argc, argv, "--carriers", 1);
    uint32_t flags = (uint32_t)arg_u64(argc, argv, "--flags", 0);
    char *text = read_all(stdin);
    if (!text)
        return 2;
    static aio_program program;
    static aio_report report;
    char err[160];
    if (aio_program_parse(&program, text, err, sizeof(err)) != 0) {
        fprintf(stderr, "refused: %s\n", err);
        free(text);
        return 3;
    }
    free(text);
    if (aio_run(&program, carriers, flags, &report) != 0)
        return 4;

    static const char STATE_LETTER[] = {'P', 'D', 'C'};
    for (uint32_t t = 0; t < program.n_tasks; t++)
        printf("task %u %c %u\n", t, STATE_LETTER[report.state[t]], report.result[t]);
    printf("trace");
    for (uint32_t i = 0; i < report.trace_len; i++)
        printf(" %u:%u", report.trace_task[i], report.trace_value[i]);
    printf("\n");
    printf("evidence carriers=%u carriers_stepping=%u deadlock=%u pops=%" PRIu64 " parks=%" PRIu64
           " wakes=%" PRIu64 " timer_fires=%" PRIu64 " cancels=%" PRIu64 " raise_polls=%" PRIu64
           " cross_resumes=%" PRIu64 " state_refused=%" PRIu64 " checksum_fails=%" PRIu64
           " refused_spawns=%" PRIu64 " ring_pushed=%" PRIu64 " ring_popped=%" PRIu64
           " moved_cells=%" PRIu64 " compact_passes=%" PRIu64 " clock_end=%" PRIu64
           " spurious_resumes=%" PRIu64 "\n",
           carriers, report.carriers_stepping, report.deadlock, report.pops, report.parks,
           report.wakes, report.timer_fires, report.cancels, report.raise_polls,
           report.cross_resumes, report.state_refused, report.checksum_fails, report.refused_spawns,
           report.ring_pushed, report.ring_popped, report.moved_cells, report.compact_passes,
           report.clock_end, report.spurious_resumes);
    return 0;
}
