/*
 * rxfb_dump (C++): the C example (rxfb/example/rxfb_dump.c), written against the C++ face.
 *
 *   rxfb_dump_cpp IMAGE                       the image: size, handles, SHA-256
 *   rxfb_dump_cpp IMAGE walk                  every object, depth first in chain order
 *   rxfb_dump_cpp IMAGE object PATH...        per path: payload, semantic row, children, references
 *   rxfb_dump_cpp IMAGE code MACHINE SLOT...  per function slot: the bytes of its code
 *
 * It prints exactly what the C program prints (the test suite diffs the two byte for byte, stdout,
 * stderr and exit code): the image is mmap'ed and read in place through rxfb.hpp, whose views are
 * pointers into the mapping, and this program copies nothing but the lines it prints.
 *
 * This is the hosted side of the example: stdio and mmap live here, never in the header. The
 * SHA-256 is the engine's own (sha256_engine.cpp); the library itself does no hashing.
 */

#include "../rxfb.hpp"

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace face = pymergetic::rxfb;

extern "C" void rxfb_example_sha256(const void *data, std::size_t size, std::uint8_t out[32]);

namespace {

constexpr std::uint32_t path_room = 4096U;
/* How many references and child names one `object` record spells out (the counts are exact). */
constexpr std::uint32_t refs_shown = 4U;
constexpr std::uint32_t names_shown = 8U;

void hex(face::Bytes bytes) {
    static const char digits[] = "0123456789abcdef";
    for (const std::uint8_t byte : bytes) {
        std::putchar(digits[byte >> 4]);
        std::putchar(digits[byte & 15U]);
    }
}

void digest(face::Bytes bytes) {
    std::uint8_t sum[32];
    rxfb_example_sha256(bytes.data(), bytes.size(), sum);
    hex(face::Bytes(sum, sizeof(sum)));
}

int fail(const char *what, face::Status status) {
    std::fprintf(stderr, "rxfb_dump: %s: status %d\n", what, static_cast<int>(status));
    return 1;
}

/* The path of a handle into `buffer`; false when the handle or the buffer is not good. */
bool path_text(const face::Image &image, face::Handle handle, char *buffer, std::uint32_t &length) {
    const auto object = image.object(handle);
    if (!object)
        return false;
    const auto path = object->path(buffer, path_room);
    if (!path)
        return false;
    length = *path;
    return true;
}

int print_object(const face::Image &image, const char *path) {
    char buffer[path_room];
    const auto found = image.lookup(path);
    if (!found)
        return fail(path, found.status());
    const face::Object object = *found;

    const auto payload = object.payload();
    if (!payload)
        return fail(path, payload.status());
    std::printf("object\t%s\t%u\t%u\t", path, static_cast<unsigned>(object.kind()),
                static_cast<unsigned>(payload->size()));
    hex(*payload);
    std::putchar('\t');
    digest(*payload);
    std::putchar('\n');

    const auto spec = object.spec();
    if (!spec)
        return fail(path, spec.status());
    const rxfb_spec_row *row = *spec;
    std::uint32_t length = 0;
    const char *type = "";
    if (row->type != face::no_handle) {
        if (!path_text(image, row->type, buffer, length))
            return fail(path, face::Status::bad_image);
        buffer[length] = 0;
        type = buffer;
    }
    std::printf("spec\t%s\t%s\t%u\t%u\t%llu\n", path, type, static_cast<unsigned>(row->flags),
                static_cast<unsigned>(row->index),
                static_cast<unsigned long long>(row->generation));

    std::printf("children\t%s\t", path);
    std::uint32_t count = 0;
    auto children = object.children();
    for (const face::Object child : children) {
        const auto name = child.name();
        if (!name)
            return fail(path, name.status());
        if (count < names_shown)
            std::printf("%s%.*s", count == 0 ? "" : ",", static_cast<int>(name->size()),
                        name->data());
        count++;
    }
    if (!children.ok())
        return fail(path, children.status());
    std::printf("\t%u\n", static_cast<unsigned>(count));
    /* The range and the array form are one walk: they must agree on the count. */
    const auto total = object.child_count();
    if (!total || *total != count)
        return fail(path, face::Status::bad_image);

    const auto refs = object.refs();
    if (!refs)
        return fail(path, refs.status());
    std::printf("refs\t%s\t%u", path, static_cast<unsigned>(refs->size()));
    std::uint32_t shown = 0;
    for (const face::Ref ref : *refs) {
        if (shown++ >= refs_shown)
            break;
        std::printf("\t");
        if (!path_text(image, ref.target(), buffer, length))
            return fail(path, face::Status::bad_image);
        std::printf("%.*s:", static_cast<int>(length), buffer);
        if (ref.has_role()) {
            if (!path_text(image, ref.role(), buffer, length))
                return fail(path, face::Status::bad_image);
            std::printf("%.*s", static_cast<int>(length), buffer);
        }
        std::printf(":%u:%u", static_cast<unsigned>(ref.kind()),
                    static_cast<unsigned>(ref.index()));
    }
    std::putchar('\n');
    return 0;
}

/* One line per object: the tree in chain order, children before the next sibling. The walk keeps
 * no stack: it climbs back through the records' `parent` links. */
int print_walk(const face::Image &image) {
    char buffer[path_room];
    const auto root = image.root();
    if (!root)
        return fail("walk", root.status());
    face::Object at = *root;
    std::uint32_t visited = 0;
    for (;;) {
        const auto payload = at.payload();
        if (!payload)
            return fail("walk", payload.status());
        std::uint32_t length = 0;
        if (!path_text(image, at.handle(), buffer, length))
            return fail("walk", face::Status::bad_image);
        std::printf("walk\t%.*s\t%u\t%u\t", static_cast<int>(length), buffer,
                    static_cast<unsigned>(at.kind()), static_cast<unsigned>(payload->size()));
        digest(*payload);
        std::putchar('\n');
        if (++visited > image.count())
            return fail("walk", face::Status::bad_image); /* more objects than handles: a cycle */

        face::Handle next = face::no_handle;
        const auto first = at.first_child();
        if (first.status() == face::Status::protected_object)
            next = face::no_handle; /* the object index: a table, not a chain */
        else if (!first)
            return fail("walk", first.status());
        else
            next = *first;
        while (next == face::no_handle) {
            if (at.handle() == image.root()->handle())
                return 0; /* the root has no sibling: the tree is done */
            const auto sibling = at.next_sibling();
            if (!sibling)
                return fail("walk", sibling.status());
            next = *sibling;
            if (next != face::no_handle)
                break;
            const auto up = image.object(at.parent());
            if (!up)
                return fail("walk", up.status());
            at = *up;
        }
        const auto landed = image.object(next);
        if (!landed)
            return fail("walk", landed.status());
        at = *landed;
    }
}

int print_slot(const face::Image &image, const char *machine, std::uint32_t slot_number) {
    const auto slot = image.slot(machine, slot_number);
    if (!slot)
        return fail("slot", slot.status());
    /* The bytes are the image's own: the pointer is inside the mapping. */
    const face::Bytes bytes = slot->bytes();
    if (bytes.begin() < image.base() || bytes.end() > image.base() + image.size())
        return fail("slot", face::Status::bad_image);
    char buffer[path_room];
    std::uint32_t length = 0;
    const auto code = image.object(slot->code());
    if (!code)
        return fail("slot", code.status());
    if (!path_text(image, code->parent(), buffer, length))
        return fail("slot", face::Status::bad_image);
    std::printf("code\t%s\t%u\t%.*s\t%llu\t", machine, static_cast<unsigned>(slot_number),
                static_cast<int>(length), buffer, static_cast<unsigned long long>(slot->length()));
    digest(bytes);
    std::putchar('\n');
    return 0;
}

} // namespace

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(stderr,
                     "usage: rxfb_dump IMAGE [walk | object PATH... | code MACHINE SLOT...]\n");
        return 2;
    }
    const int fd = open(argv[1], O_RDONLY);
    struct stat info;
    if (fd < 0 || fstat(fd, &info) != 0) {
        std::perror(argv[1]);
        return 1;
    }
    void *map =
        mmap(nullptr, static_cast<std::size_t>(info.st_size), PROT_READ, MAP_PRIVATE, fd, 0);
    if (map == MAP_FAILED) {
        std::perror("mmap");
        return 1;
    }

    face::Image image;
    const face::Status status = image.attach(map, static_cast<std::uint64_t>(info.st_size));
    if (status != face::Status::ok)
        return fail("attach", status);

    if (argc == 2) {
        std::printf("image\t%llu\t%u\t", static_cast<unsigned long long>(image.size()),
                    static_cast<unsigned>(image.count()));
        digest(face::Bytes(static_cast<const std::uint8_t *>(map),
                           static_cast<std::size_t>(info.st_size)));
        std::putchar('\n');
        return 0;
    }
    int result = 0;
    if (std::strcmp(argv[2], "walk") == 0) {
        result = print_walk(image);
    } else if (std::strcmp(argv[2], "object") == 0) {
        for (int i = 3; i < argc && result == 0; i++)
            result = print_object(image, argv[i]);
    } else if (std::strcmp(argv[2], "code") == 0 && argc >= 4) {
        for (int i = 4; i < argc && result == 0; i++)
            result = print_slot(image, argv[3],
                                static_cast<std::uint32_t>(std::strtoul(argv[i], nullptr, 10)));
    } else {
        std::fprintf(stderr, "rxfb_dump: unknown command %s\n", argv[2]);
        result = 2;
    }
    munmap(map, static_cast<std::size_t>(info.st_size));
    close(fd);
    return result;
}
