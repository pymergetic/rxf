/*
 * The probe of the activation seam: the gate of wrap_activate.c (docs/26 Recipe 1).
 *
 * It runs rxf_activate_check on the two files it is given -- the block (the bytes the
 * wrapper loaded) and the cert block (the package's tail; "-" for none) -- against the
 * trust anchor linked into this binary (wrap_trust.S), and answers the seam's answer
 * word as its exit status. The cases around it (tests/test_wrap_activate.py) are
 * crafted by the Python seat: the statements there are signed with
 * src/pymergetic/rxftk/certify and written with the codec of binary.package, so what
 * is proven is that the C reads what the Python seat wrote and refuses what it must.
 *
 * This is a test gate, a hosted program of the build machine: unlike the wrapper it
 * may read files.
 */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

#include "wrap.h"

static void *read_all(const char *path, rxf_u64 *bytes) {
    char buffer[4096];
    void *held = malloc(1); /* non-zero even for the empty file: a readable one */
    rxf_u64 used = 0, size = 0;
    FILE *file;
    if (!held)
        return 0;
    file = fopen(path, "rb");
    if (!file) {
        free(held);
        return 0;
    }
    for (;;) {
        size_t got = fread(buffer, 1, sizeof buffer, file);
        if (got == 0)
            break;
        if (used + got > size) {
            size = used + got;
            held = realloc(held, size);
            if (!held) {
                fclose(file);
                return 0;
            }
        }
        for (size_t index = 0; index < got; ++index)
            ((unsigned char *)held)[used + index] = (unsigned char)buffer[index];
        used += got;
    }
    fclose(file);
    *bytes = used;
    return held;
}

int main(int argc, char **argv) {
    rxf_u64 block_bytes = 0, cert_bytes = 0;
    void *block, *cert = 0;

    if (argc != 3) {
        fprintf(stderr, "usage: %s BLOCK CERT|- (a trust anchor is linked in)\n", argv[0]);
        return 2;
    }
    block = read_all(argv[1], &block_bytes);
    if (!block) {
        fprintf(stderr, "%s: cannot read the block\n", argv[1]);
        return 2;
    }
    if (argv[2][0] != '-' || argv[2][1] != 0) {
        cert = read_all(argv[2], &cert_bytes);
        if (!cert) {
            fprintf(stderr, "%s: cannot read the cert block\n", argv[2]);
            return 2;
        }
    }
    return (int)rxf_activate_check(block, block_bytes, cert, cert_bytes);
}
