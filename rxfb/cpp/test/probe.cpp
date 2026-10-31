/*
 * A program that touches the whole C++ face with no standard library: it is compiled freestanding
 * (-ffreestanding -fno-exceptions -fno-rtti) for x86_64, aarch64, i386 and wasm32, and the test
 * suite then demands that the object needs no symbol but the rxfb_* ones of the C core: no
 * operator new, no __cxa_*, no typeinfo, no libc.
 */

#include "../rxfb.hpp"

namespace face = pymergetic::rxfb;

namespace {

/* A checksum over everything the face can show: it keeps every call alive at -O2. */
uint64_t touch(const face::Image &image) {
    uint64_t sum = image.size() + image.count() + image.entry();
    char path[256];

    const auto root = image.root();
    if (!root)
        return sum;
    auto children = root->children();
    for (const face::Object child : children) {
        sum += child.kind() + child.flags() + child.parent();
        if (const auto name = child.name())
            for (const char letter : *name)
                sum += static_cast<uint8_t>(letter);
        if (const auto payload = child.payload())
            for (const uint8_t byte : *payload)
                sum += byte;
        if (const auto spec = child.spec())
            sum += (*spec)->flags;
        if (const auto text = child.path(path, sizeof(path)))
            sum += *text;
        if (const auto refs = child.refs())
            for (const face::Ref ref : *refs)
                sum += ref.target() + ref.role() + ref.kind() + ref.binding() + ref.index();
        if (const auto table = child.table())
            sum += table->size();
        if (const auto count = child.child_count())
            sum += *count;
        if (const auto first = child.first_child())
            sum += *first;
        if (const auto next = child.next_sibling())
            sum += *next;
        if (const auto found = child.find("run"))
            sum += found->handle();
    }
    sum += static_cast<uint64_t>(children.status());

    if (const auto run = image.lookup(RXFB_RUN_PATH)) {
        if (const auto slot = run->slot("wasm32", 1))
            for (const uint8_t byte : slot->bytes())
                sum += byte;
        if (const auto code = run->code("x86_64_linux"))
            sum += code->length() + code->code() + code->blob();
    }
    if (const auto slot = image.slot("aarch64_linux", 2))
        sum += reinterpret_cast<uintptr_t>(slot->entry());
    if (const auto symbol = image.symbol(1))
        sum += symbol->size() + (*symbol == face::Text("sys") ? 1U : 0U);
    return sum;
}

} // namespace

/* The entry the test links and the sanitizer-free probe calls. */
extern "C" uint64_t rxfb_probe(const void *bytes, uint64_t length) {
    face::Image image;
    const face::Status status = image.attach(bytes, length);
    if (status != face::Status::ok)
        return static_cast<uint64_t>(status);
    return touch(image);
}
