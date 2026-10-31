/*
 * rxfb_dump: maps an RXFB v7 image and prints what the reader library answers.
 *
 *   rxfb_dump IMAGE                       the image: size, handles, SHA-256
 *   rxfb_dump IMAGE walk                  every object, depth first in chain order: path, kind,
 *                                         payload size and SHA-256 (the whole tree, no allocation)
 *   rxfb_dump IMAGE object PATH...        per path: payload (hex) and its SHA-256, the semantic
 *                                         row, the children, the references
 *   rxfb_dump IMAGE code MACHINE SLOT...  per function slot: the bytes of its code for MACHINE
 *
 * The image is mmap'ed and read in place: the library hands out views into the mapping and this
 * program copies nothing but the lines it prints. Output is tab-separated, one record per line,
 * so another program (the test suite's Python model) can reproduce it byte for byte.
 *
 * This is the hosted side of the example: stdio and mmap live here, never in the library. The
 * SHA-256 is the engine's own (sha256_engine.cpp); the library itself does no hashing.
 */

#include "../rxfb.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

void rxfb_example_sha256(const void *data, size_t size, uint8_t out[32]);

#define PATH_ROOM 4096U
/* How many references and child names one `object` record spells out (the counts are exact). */
#define REFS_SHOWN 4U
#define NAMES_SHOWN 8U

static void hex(const uint8_t *bytes, size_t size) {
    static const char digits[] = "0123456789abcdef";
    for (size_t i = 0; i < size; i++) {
        putchar(digits[bytes[i] >> 4]);
        putchar(digits[bytes[i] & 15U]);
    }
}

static void digest(const void *bytes, size_t size) {
    uint8_t sum[32];
    rxfb_example_sha256(bytes, size, sum);
    hex(sum, sizeof(sum));
}

static int fail(const char *what, rxfb_status status) {
    fprintf(stderr, "rxfb_dump: %s: status %d\n", what, (int)status);
    return 1;
}

/* The path of a handle into `buffer`; false when the handle or the buffer is not good. */
static int path_text(const rxfb *rx, rxfb_handle handle, char *buffer, uint32_t *length) {
    return rxfb_path(rx, handle, buffer, PATH_ROOM, length) == RXFB_OK;
}

static int print_object(const rxfb *rx, const char *path) {
    char buffer[PATH_ROOM];
    rxfb_handle handle = RXFB_HANDLE_NONE;
    rxfb_status status = rxfb_lookup(rx, path, (uint32_t)strlen(path), &handle);
    if (status != RXFB_OK)
        return fail(path, status);

    const rxfb_object *record = 0;
    const uint8_t *payload = 0;
    uint32_t size = 0;
    if ((status = rxfb_record(rx, handle, &record)) != RXFB_OK ||
        (status = rxfb_payload(rx, handle, &payload, &size)) != RXFB_OK)
        return fail(path, status);
    printf("object\t%s\t%u\t%u\t", path, (unsigned)record->kind, (unsigned)size);
    hex(payload, size);
    putchar('\t');
    digest(payload, size);
    putchar('\n');

    const rxfb_spec_row *row = 0;
    if ((status = rxfb_spec(rx, handle, &row)) != RXFB_OK)
        return fail(path, status);
    uint32_t length = 0;
    const char *type = "";
    if (row->type != RXFB_HANDLE_NONE) {
        if (!path_text(rx, row->type, buffer, &length))
            return fail(path, RXFB_BAD_IMAGE);
        buffer[length] = 0;
        type = buffer;
    }
    printf("spec\t%s\t%s\t%u\t%u\t%llu\n", path, type, (unsigned)row->flags, (unsigned)row->index,
           (unsigned long long)row->generation);

    printf("children\t%s\t", path);
    rxfb_handle child = RXFB_HANDLE_NONE;
    if ((status = rxfb_first_child(rx, handle, &child)) != RXFB_OK)
        return fail(path, status);
    uint32_t count = 0;
    while (child != RXFB_HANDLE_NONE) {
        const char *name = 0;
        uint32_t name_length = 0;
        if ((status = rxfb_name(rx, child, &name, &name_length)) != RXFB_OK)
            return fail(path, status);
        if (count < NAMES_SHOWN)
            printf("%s%.*s", count == 0 ? "" : ",", (int)name_length, name);
        count++;
        if ((status = rxfb_next_sibling(rx, child, &child)) != RXFB_OK)
            return fail(path, status);
    }
    printf("\t%u\n", (unsigned)count);
    /* The iterator and the array form are one walk: they must agree on the count. */
    uint32_t total = 0;
    status = rxfb_children(rx, handle, 0, 0, &total);
    if ((status != RXFB_OK && status != RXFB_NO_ROOM) || total != count)
        return fail(path, RXFB_BAD_IMAGE);

    const rxfb_ref *refs = 0;
    uint32_t held = 0;
    if ((status = rxfb_refs(rx, handle, &refs, &held)) != RXFB_OK)
        return fail(path, status);
    printf("refs\t%s\t%u", path, (unsigned)held);
    for (uint32_t i = 0; i < held && i < REFS_SHOWN; i++) {
        printf("\t");
        if (!path_text(rx, (rxfb_handle)refs[i].target, buffer, &length))
            return fail(path, RXFB_BAD_IMAGE);
        printf("%.*s:", (int)length, buffer);
        if (refs[i].role != RXFB_REF_NONE) {
            if (!path_text(rx, (rxfb_handle)refs[i].role, buffer, &length))
                return fail(path, RXFB_BAD_IMAGE);
            printf("%.*s", (int)length, buffer);
        }
        printf(":%u:%u", (unsigned)refs[i].kind, (unsigned)refs[i].index);
    }
    putchar('\n');
    return 0;
}

/* One line per object: the tree in chain order, children before the next sibling. The walk keeps
 * no stack: it climbs back through the records' `parent` links. */
static int print_walk(const rxfb *rx) {
    char buffer[PATH_ROOM];
    rxfb_handle at = rxfb_root(rx);
    uint32_t visited = 0;
    for (;;) {
        const rxfb_object *record = 0;
        const uint8_t *payload = 0;
        uint32_t size = 0;
        uint32_t length = 0;
        rxfb_status status = rxfb_record(rx, at, &record);
        if (status == RXFB_OK)
            status = rxfb_payload(rx, at, &payload, &size);
        if (status != RXFB_OK)
            return fail("walk", status);
        if (!path_text(rx, at, buffer, &length))
            return fail("walk", RXFB_BAD_IMAGE);
        printf("walk\t%.*s\t%u\t%u\t", (int)length, buffer, (unsigned)record->kind, (unsigned)size);
        digest(payload, size);
        putchar('\n');
        if (++visited > rxfb_count(rx))
            return fail("walk", RXFB_BAD_IMAGE); /* more objects than handles: a cycle */

        rxfb_handle next = RXFB_HANDLE_NONE;
        status = rxfb_first_child(rx, at, &next);
        if (status == RXFB_PROTECTED)
            next = RXFB_HANDLE_NONE; /* the object index: a table, not a chain */
        else if (status != RXFB_OK)
            return fail("walk", status);
        while (next == RXFB_HANDLE_NONE) {
            if (at == rxfb_root(rx))
                return 0; /* the root has no sibling: the tree is done */
            if ((status = rxfb_next_sibling(rx, at, &next)) != RXFB_OK)
                return fail("walk", status);
            if (next != RXFB_HANDLE_NONE)
                break;
            at = record->parent;
            if ((status = rxfb_record(rx, at, &record)) != RXFB_OK)
                return fail("walk", status);
        }
        at = next;
    }
}

static int print_slot(const rxfb *rx, const char *machine, uint32_t slot) {
    rxfb_code code;
    rxfb_status status = rxfb_slot(rx, machine, slot, &code);
    if (status != RXFB_OK)
        return fail("slot", status);
    /* The bytes are the image's own: the pointer is inside the mapping. */
    if (code.entry < rx->base || code.entry + code.length > rx->base + rx->size)
        return fail("slot", RXFB_BAD_IMAGE);
    char buffer[PATH_ROOM];
    uint32_t length = 0;
    rxfb_handle owner = RXFB_HANDLE_NONE;
    const rxfb_object *record = 0;
    if ((status = rxfb_record(rx, code.code, &record)) != RXFB_OK)
        return fail("slot", status);
    owner = record->parent;
    if (!path_text(rx, owner, buffer, &length))
        return fail("slot", RXFB_BAD_IMAGE);
    printf("code\t%s\t%u\t%.*s\t%llu\t", machine, (unsigned)slot, (int)length, buffer,
           (unsigned long long)code.length);
    digest(code.entry, (size_t)code.length);
    putchar('\n');
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: rxfb_dump IMAGE [walk | object PATH... | code MACHINE SLOT...]\n");
        return 2;
    }
    const int fd = open(argv[1], O_RDONLY);
    struct stat info;
    if (fd < 0 || fstat(fd, &info) != 0) {
        perror(argv[1]);
        return 1;
    }
    void *map = mmap(0, (size_t)info.st_size, PROT_READ, MAP_PRIVATE, fd, 0);
    if (map == MAP_FAILED) {
        perror("mmap");
        return 1;
    }

    rxfb rx;
    rxfb_status status = rxfb_attach(&rx, map, (uint64_t)info.st_size);
    if (status != RXFB_OK)
        return fail("attach", status);

    if (argc == 2) {
        printf("image\t%llu\t%u\t", (unsigned long long)rx.size, (unsigned)rxfb_count(&rx));
        digest(map, (size_t)info.st_size);
        putchar('\n');
        return 0;
    }
    int result = 0;
    if (strcmp(argv[2], "walk") == 0) {
        result = print_walk(&rx);
    } else if (strcmp(argv[2], "object") == 0) {
        for (int i = 3; i < argc && result == 0; i++)
            result = print_object(&rx, argv[i]);
    } else if (strcmp(argv[2], "code") == 0 && argc >= 4) {
        for (int i = 4; i < argc && result == 0; i++)
            result = print_slot(&rx, argv[3], (uint32_t)strtoul(argv[i], 0, 10));
    } else {
        fprintf(stderr, "rxfb_dump: unknown command %s\n", argv[2]);
        result = 2;
    }
    munmap(map, (size_t)info.st_size);
    close(fd);
    return result;
}
