#ifndef RXFB_HPP
#define RXFB_HPP

/*
 * rxfb.hpp: the C++ face of the slim reader (rxfb.h), as view types.
 *
 * Header-only, C++20, and a thin face over the SAME C symbols: every call below is one call of
 * rxfb.c (rxfb_attach, rxfb_record, rxfb_payload, ...); nothing here walks, validates or decodes
 * an image on its own. The face only gives the C answers types: a Status instead of an int, a
 * Result instead of an out-parameter, a range instead of a first/next pair.
 *
 * Freestanding-friendly, because the engine builds with -fno-exceptions -fno-rtti and some seats
 * have no C++ runtime at all: no exception, no RTTI, no allocation, no standard-library header
 * (only <stddef.h> and <stdint.h> through the C header). Span and Text are hand-rolled for that
 * reason (<span> and <string_view> are not freestanding headers), and every function is noexcept.
 * Compiling a program that uses the whole face leaves no undefined symbol but the rxfb_* ones.
 *
 * Ownership: nothing is owned. An Image attaches bytes the caller keeps mapped and unchanged. An
 * Object, a Children range, a Refs range and a Slot are views: they point into the image and
 * into the Image object (it holds the library's anchors), so the Image must outlive them, and it
 * can be neither copied nor moved. There is nothing to release: dropping the Image is the
 * teardown, which is why no deinit exists (rxfb.h says the same for the C struct).
 *
 * A failure is a Status, never a throw. A Result is truthy when the call answered RXFB_OK; its
 * value is only meaningful then (RXFB_NO_ROOM is the one failure that also carries a value: the
 * room the caller must offer).
 */

#include "../rxfb.h"

#include <stddef.h>
#include <stdint.h>

namespace pymergetic::rxfb {

/* ---- status and result ---------------------------------------------------------------------- */

enum class Status : int {
    ok = RXFB_OK,
    bad_image = RXFB_BAD_IMAGE,
    bad_handle = RXFB_BAD_HANDLE,
    bad_name = RXFB_BAD_NAME,
    protected_object = RXFB_PROTECTED,
    no_room = RXFB_NO_ROOM,
    not_found = RXFB_NOT_FOUND
};

namespace detail {
[[nodiscard]] constexpr Status status_of(rxfb_status status) noexcept {
    return static_cast<Status>(static_cast<int>(status));
}
} // namespace detail

/* A value or the Status that says why there is none. No allocation, no exception: both members
 * are plain data, and T must be default constructible and trivially copyable. */
template <class T> class Result {
  public:
    constexpr Result(Status status) noexcept : status_(status), value_() {
    }
    constexpr Result(Status status, T value) noexcept : status_(status), value_(value) {
    }
    constexpr Result(T value) noexcept : status_(Status::ok), value_(value) {
    }

    [[nodiscard]] constexpr explicit operator bool() const noexcept {
        return status_ == Status::ok;
    }
    [[nodiscard]] constexpr bool ok() const noexcept {
        return status_ == Status::ok;
    }
    [[nodiscard]] constexpr Status status() const noexcept {
        return status_;
    }
    [[nodiscard]] constexpr const T &value() const noexcept {
        return value_;
    }
    [[nodiscard]] constexpr const T &operator*() const noexcept {
        return value_;
    }
    [[nodiscard]] constexpr const T *operator->() const noexcept {
        return &value_;
    }

  private:
    Status status_;
    T value_;
};

/* ---- spans ---------------------------------------------------------------------------------- */

/* A pointer and a length: the freestanding stand-in for std::span<const T>. */
template <class T> class Span {
  public:
    constexpr Span() noexcept : data_(nullptr), size_(0) {
    }
    constexpr Span(const T *data, size_t size) noexcept : data_(data), size_(size) {
    }

    [[nodiscard]] constexpr const T *data() const noexcept {
        return data_;
    }
    [[nodiscard]] constexpr size_t size() const noexcept {
        return size_;
    }
    [[nodiscard]] constexpr bool empty() const noexcept {
        return size_ == 0;
    }
    [[nodiscard]] constexpr const T *begin() const noexcept {
        return data_;
    }
    [[nodiscard]] constexpr const T *end() const noexcept {
        return data_ + size_;
    }
    [[nodiscard]] constexpr const T &operator[](size_t index) const noexcept {
        return data_[index];
    }

  private:
    const T *data_;
    size_t size_;
};

using Bytes = Span<uint8_t>;

/* Text in the image: a pointer and a length, compared byte for byte, no NUL needed (the engine's
 * own names are NUL-terminated inside the image, a path from a caller is not). */
class Text {
  public:
    constexpr Text() noexcept : data_(nullptr), size_(0) {
    }
    constexpr Text(const char *data, uint32_t size) noexcept : data_(data), size_(size) {
    }
    /* A NUL-terminated string (a literal, an argv entry). */
    constexpr Text(const char *terminated) noexcept
        : data_(terminated), size_(measure(terminated)) {
    }

    [[nodiscard]] constexpr const char *data() const noexcept {
        return data_;
    }
    [[nodiscard]] constexpr uint32_t size() const noexcept {
        return size_;
    }
    [[nodiscard]] constexpr bool empty() const noexcept {
        return size_ == 0;
    }
    [[nodiscard]] constexpr const char *begin() const noexcept {
        return data_;
    }
    [[nodiscard]] constexpr const char *end() const noexcept {
        return data_ + size_;
    }
    [[nodiscard]] constexpr char operator[](uint32_t index) const noexcept {
        return data_[index];
    }

    [[nodiscard]] constexpr bool operator==(const Text &other) const noexcept {
        if (size_ != other.size_)
            return false;
        for (uint32_t i = 0; i < size_; i++)
            if (data_[i] != other.data_[i])
                return false;
        return true;
    }

  private:
    static constexpr uint32_t measure(const char *text) noexcept {
        uint32_t length = 0;
        if (text != nullptr)
            while (text[length] != '\0')
                length++;
        return length;
    }

    const char *data_;
    uint32_t size_;
};

/* ---- handles -------------------------------------------------------------------------------- */

using Handle = rxfb_handle;
inline constexpr Handle no_handle = RXFB_HANDLE_NONE;

class Image;
class Object;

/* ---- references ----------------------------------------------------------------------------- */

/* One reference row (24 bytes inside the image): a view, eight bytes wide. */
class Ref {
  public:
    constexpr Ref() noexcept : row_(nullptr) {
    }
    constexpr explicit Ref(const rxfb_ref *row) noexcept : row_(row) {
    }

    [[nodiscard]] constexpr Handle target() const noexcept {
        return static_cast<Handle>(row_->target);
    }
    [[nodiscard]] constexpr bool has_role() const noexcept {
        return row_->role != RXFB_REF_NONE;
    }
    [[nodiscard]] constexpr Handle role() const noexcept {
        return has_role() ? static_cast<Handle>(row_->role) : no_handle;
    }
    [[nodiscard]] constexpr uint16_t kind() const noexcept {
        return row_->kind;
    }
    [[nodiscard]] constexpr uint16_t binding() const noexcept {
        return row_->binding;
    }
    [[nodiscard]] constexpr uint32_t index() const noexcept {
        return row_->index;
    }
    [[nodiscard]] constexpr const rxfb_ref &row() const noexcept {
        return *row_;
    }

  private:
    const rxfb_ref *row_;
};

/* The reference rows of an object: a random-access range over the table inside the image. */
class Refs {
  public:
    class Iterator {
      public:
        constexpr Iterator() noexcept : row_(nullptr) {
        }
        constexpr explicit Iterator(const rxfb_ref *row) noexcept : row_(row) {
        }
        [[nodiscard]] constexpr Ref operator*() const noexcept {
            return Ref(row_);
        }
        constexpr Iterator &operator++() noexcept {
            row_++;
            return *this;
        }
        constexpr Iterator operator++(int) noexcept {
            const Iterator before = *this;
            row_++;
            return before;
        }
        [[nodiscard]] constexpr bool operator==(const Iterator &other) const noexcept {
            return row_ == other.row_;
        }

      private:
        const rxfb_ref *row_;
    };

    constexpr Refs() noexcept : rows_(nullptr), count_(0) {
    }
    constexpr Refs(const rxfb_ref *rows, uint32_t count) noexcept : rows_(rows), count_(count) {
    }

    [[nodiscard]] constexpr uint32_t size() const noexcept {
        return count_;
    }
    [[nodiscard]] constexpr bool empty() const noexcept {
        return count_ == 0;
    }
    [[nodiscard]] constexpr Iterator begin() const noexcept {
        return Iterator(rows_);
    }
    [[nodiscard]] constexpr Iterator end() const noexcept {
        return Iterator(rows_ + count_);
    }
    [[nodiscard]] constexpr Ref operator[](uint32_t index) const noexcept {
        return Ref(rows_ + index);
    }

  private:
    const rxfb_ref *rows_;
    uint32_t count_;
};

/* ---- the code story ------------------------------------------------------------------------- */

/* A function's bytes for one machine: where it starts inside the image and how long it is. */
class Slot {
  public:
    constexpr Slot() noexcept : entry_(nullptr), length_(0), code_(no_handle), blob_(no_handle) {
    }
    constexpr explicit Slot(const rxfb_code &code) noexcept
        : entry_(code.entry), length_(code.length), code_(code.code), blob_(code.blob) {
    }

    /* The bytes, a view into the image: for a host to interpret, JIT or link. */
    [[nodiscard]] constexpr Bytes bytes() const noexcept {
        return Bytes(entry_, static_cast<size_t>(length_));
    }
    [[nodiscard]] constexpr const uint8_t *entry() const noexcept {
        return entry_;
    }
    [[nodiscard]] constexpr uint64_t length() const noexcept {
        return length_;
    }
    /* The Code object the bytes came from and the CodeBlob that holds them. */
    [[nodiscard]] constexpr Handle code() const noexcept {
        return code_;
    }
    [[nodiscard]] constexpr Handle blob() const noexcept {
        return blob_;
    }

  private:
    const uint8_t *entry_;
    uint64_t length_;
    Handle code_;
    Handle blob_;
};

/* ---- objects -------------------------------------------------------------------------------- */

/* The children of an object in chain order: a range over rxfb's iterator that needs no memory
 * (rxfb_first_child, rxfb_next_sibling). The iterator lands on each child through rxfb_record, so
 * dereferencing is always safe; a corrupt chain ends the loop and leaves its Status here:
 *
 *   auto kids = object.children();
 *   for (Object child : kids) ...
 *   if (!kids.ok()) ... kids.status() says why the loop stopped early
 *
 * The range must outlive its iterators. An object whose payload is a table (the object index) is
 * not a chain: the range is empty and the Status is Status::protected_object. */
class Children {
  public:
    class Iterator {
      public:
        constexpr Iterator() noexcept : image_(nullptr), status_(nullptr), handle_(no_handle) {
        }

        [[nodiscard]] Object operator*() const noexcept;
        Iterator &operator++() noexcept;
        [[nodiscard]] constexpr bool operator==(const Iterator &other) const noexcept {
            return handle_ == other.handle_;
        }

      private:
        friend class Children;
        constexpr Iterator(const ::rxfb *image, Status *status, Handle handle) noexcept
            : image_(image), status_(status), handle_(handle) {
        }

        const ::rxfb *image_;
        Status *status_;
        Handle handle_;
    };

    constexpr Children(const ::rxfb *image, Handle parent) noexcept
        : image_(image), parent_(parent), status_(Status::ok) {
    }

    [[nodiscard]] Iterator begin() const noexcept;
    [[nodiscard]] constexpr Iterator end() const noexcept {
        return Iterator(image_, &status_, no_handle);
    }

    /* How the last walk ended: ok, or why it stopped before the end of the chain. */
    [[nodiscard]] constexpr Status status() const noexcept {
        return status_;
    }
    [[nodiscard]] constexpr bool ok() const noexcept {
        return status_ == Status::ok;
    }

  private:
    const ::rxfb *image_;
    Handle parent_;
    mutable Status status_;
};

/* An object: its handle and its record (the 24-byte header inside the image), checked once by
 * rxfb_record. Everything else is asked of the library when it is needed. */
class Object {
  public:
    constexpr Object() noexcept : image_(nullptr), handle_(no_handle), record_(nullptr) {
    }

    /* The object a handle names (the library checks the index entry and the record). */
    [[nodiscard]] static Result<Object> at(const ::rxfb *image, Handle handle) noexcept {
        const rxfb_object *record = nullptr;
        const rxfb_status status = rxfb_record(image, handle, &record);
        if (status != RXFB_OK)
            return Result<Object>(detail::status_of(status));
        return Result<Object>(Object(image, handle, record));
    }

    [[nodiscard]] constexpr Handle handle() const noexcept {
        return handle_;
    }
    [[nodiscard]] constexpr const rxfb_object &record() const noexcept {
        return *record_;
    }
    [[nodiscard]] constexpr uint16_t kind() const noexcept {
        return record_->kind;
    }
    [[nodiscard]] constexpr uint16_t flags() const noexcept {
        return record_->flags;
    }
    [[nodiscard]] constexpr Handle parent() const noexcept {
        return record_->parent;
    }

    /* The payload bytes, a view into the image (a multiple of 8; a module's opens with its chain
     * head). */
    [[nodiscard]] Result<Bytes> payload() const noexcept {
        const uint8_t *bytes = nullptr;
        uint32_t size = 0;
        const rxfb_status status = rxfb_payload(image_, handle_, &bytes, &size);
        if (status != RXFB_OK)
            return Result<Bytes>(detail::status_of(status));
        return Result<Bytes>(Bytes(bytes, size));
    }

    /* The interned name; an anonymous object answers an empty Text. */
    [[nodiscard]] Result<Text> name() const noexcept {
        const char *text = nullptr;
        uint32_t length = 0;
        const rxfb_status status = rxfb_name(image_, handle_, &text, &length);
        if (status != RXFB_OK)
            return Result<Text>(detail::status_of(status));
        return Result<Text>(Text(text, length));
    }

    /* The semantic row (sys/specs): the Type, flags word, order index and generation. */
    [[nodiscard]] Result<const rxfb_spec_row *> spec() const noexcept {
        const rxfb_spec_row *row = nullptr;
        const rxfb_status status = rxfb_spec(image_, handle_, &row);
        if (status != RXFB_OK)
            return Result<const rxfb_spec_row *>(detail::status_of(status));
        return Result<const rxfb_spec_row *>(row);
    }

    /* The path of the object, written into `out` (no NUL). The value is the length; on
     * Status::no_room nothing was written and the value is the room the path needs. */
    [[nodiscard]] Result<uint32_t> path(char *out, uint32_t capacity) const noexcept {
        uint32_t written = 0;
        const rxfb_status status = rxfb_path(image_, handle_, out, capacity, &written);
        if (status != RXFB_OK)
            return Result<uint32_t>(detail::status_of(status), written);
        return Result<uint32_t>(written);
    }

    /* The child with this name; Status::not_found when the chain has none. */
    [[nodiscard]] Result<Object> find(Text name) const noexcept {
        Handle found = no_handle;
        const rxfb_status status = rxfb_find(image_, handle_, name.data(), name.size(), &found);
        if (status != RXFB_OK)
            return Result<Object>(detail::status_of(status));
        return at(image_, found);
    }

    /* The children as a range. */
    [[nodiscard]] constexpr Children children() const noexcept {
        return Children(image_, handle_);
    }

    /* The iterator's two steps, for a walk that keeps no range: the first child and the sibling
     * after this object (no_handle at the end of the chain). */
    [[nodiscard]] Result<Handle> first_child() const noexcept {
        Handle child = no_handle;
        const rxfb_status status = rxfb_first_child(image_, handle_, &child);
        if (status != RXFB_OK)
            return Result<Handle>(detail::status_of(status));
        return Result<Handle>(child);
    }
    [[nodiscard]] Result<Handle> next_sibling() const noexcept {
        Handle next = no_handle;
        const rxfb_status status = rxfb_next_sibling(image_, handle_, &next);
        if (status != RXFB_OK)
            return Result<Handle>(detail::status_of(status));
        return Result<Handle>(next);
    }

    /* How many children the chain holds (the array form of the same walk, asked with no room). */
    [[nodiscard]] Result<uint32_t> child_count() const noexcept {
        uint32_t total = 0;
        const rxfb_status status = rxfb_children(image_, handle_, nullptr, 0, &total);
        if (status != RXFB_OK && status != RXFB_NO_ROOM)
            return Result<uint32_t>(detail::status_of(status));
        return Result<uint32_t>(total);
    }

    /* The reference rows, a view into the image. An object with no table has none. */
    [[nodiscard]] Result<Refs> refs() const noexcept {
        const rxfb_ref *rows = nullptr;
        uint32_t count = 0;
        const rxfb_status status = rxfb_refs(image_, handle_, &rows, &count);
        if (status != RXFB_OK)
            return Result<Refs>(detail::status_of(status));
        return Result<Refs>(Refs(rows, count));
    }

    /* The same rows read straight off a table object of sys/relations (the one-pass walk over
     * every table, where `refs` finds one object's table by walking the chain). Any other object
     * is Status::bad_handle. */
    [[nodiscard]] Result<Refs> table() const noexcept {
        const rxfb_ref *rows = nullptr;
        uint32_t count = 0;
        const rxfb_status status = rxfb_table(image_, handle_, &rows, &count);
        if (status != RXFB_OK)
            return Result<Refs>(detail::status_of(status));
        return Result<Refs>(Refs(rows, count));
    }

    /* The bytes of this Function for a machine ("x86_64_linux", "aarch64_linux", "wasm32"). */
    [[nodiscard]] Result<Slot> code(const char *machine) const noexcept {
        rxfb_code code;
        const rxfb_status status = rxfb_code_of(image_, handle_, machine, &code);
        if (status != RXFB_OK)
            return Result<Slot>(detail::status_of(status));
        return Result<Slot>(Slot(code));
    }

    /* Function slot `slot` of this interpreter, resolved to its bytes for a machine. */
    [[nodiscard]] Result<Slot> slot(const char *machine, uint32_t slot) const noexcept {
        rxfb_code code;
        const rxfb_status status = rxfb_slot_of(image_, handle_, machine, slot, &code);
        if (status != RXFB_OK)
            return Result<Slot>(detail::status_of(status));
        return Result<Slot>(Slot(code));
    }

  private:
    constexpr Object(const ::rxfb *image, Handle handle, const rxfb_object *record) noexcept
        : image_(image), handle_(handle), record_(record) {
    }

    const ::rxfb *image_;
    Handle handle_;
    const rxfb_object *record_;
};

/* ---- the image ------------------------------------------------------------------------------ */

/* An attached image. It holds the library's anchors (a struct of 48 bytes) and nothing else; the
 * bytes stay the caller's, mapped, 8-byte aligned and unchanged while any view is in use. */
class Image {
  public:
    Image() noexcept {
        rx_.base = nullptr;
        rx_.size = 0;
        rx_.super = nullptr;
        rx_.index = nullptr;
        rx_.specs = no_handle;
        rx_.relations = no_handle;
    }
    Image(const Image &) = delete;
    Image &operator=(const Image &) = delete;

    /* Validates the bytes (rxfb_attach). An Image that failed to attach must not be used. */
    [[nodiscard]] Status attach(const void *bytes, uint64_t length) noexcept {
        return detail::status_of(rxfb_attach(&rx_, bytes, length));
    }

    [[nodiscard]] const ::rxfb &c() const noexcept {
        return rx_;
    }
    [[nodiscard]] const uint8_t *base() const noexcept {
        return rx_.base;
    }
    [[nodiscard]] uint64_t size() const noexcept {
        return rx_.size;
    }
    [[nodiscard]] Handle entry() const noexcept {
        return rxfb_entry(&rx_);
    }
    [[nodiscard]] uint32_t count() const noexcept {
        return rxfb_count(&rx_);
    }

    [[nodiscard]] Result<Object> root() const noexcept {
        return Object::at(&rx_, rxfb_root(&rx_));
    }
    [[nodiscard]] Result<Object> object(Handle handle) const noexcept {
        return Object::at(&rx_, handle);
    }

    /* A dotted path from the root; the empty path is the root. */
    [[nodiscard]] Result<Object> lookup(Text path) const noexcept {
        Handle found = no_handle;
        const rxfb_status status = rxfb_lookup(&rx_, path.data(), path.size(), &found);
        if (status != RXFB_OK)
            return Result<Object>(detail::status_of(status));
        return Object::at(&rx_, found);
    }

    /* The text of an interned name. */
    [[nodiscard]] Result<Text> symbol(uint32_t id) const noexcept {
        const char *text = nullptr;
        uint32_t length = 0;
        const rxfb_status status = rxfb_symbol(&rx_, id, &text, &length);
        if (status != RXFB_OK)
            return Result<Text>(detail::status_of(status));
        return Result<Text>(Text(text, length));
    }

    /* Function slot `slot` of the image's own interpreter (RXFB_RUN_PATH), for a machine. */
    [[nodiscard]] Result<Slot> slot(const char *machine, uint32_t slot) const noexcept {
        rxfb_code code;
        const rxfb_status status = rxfb_slot(&rx_, machine, slot, &code);
        if (status != RXFB_OK)
            return Result<Slot>(detail::status_of(status));
        return Result<Slot>(Slot(code));
    }

  private:
    ::rxfb rx_;
};

/* ---- the children range, out of line (it needs a complete Object) --------------------------- */

inline Object Children::Iterator::operator*() const noexcept {
    return *Object::at(image_, handle_);
}

namespace detail {
/* Lands an iterator on a child: the handle itself, or no_handle with the reason in *status. */
inline Handle land(const ::rxfb *image, Handle child, Status *status) noexcept {
    if (child == no_handle)
        return no_handle;
    const rxfb_object *record = nullptr;
    const rxfb_status checked = rxfb_record(image, child, &record);
    if (checked == RXFB_OK)
        return child;
    *status = status_of(checked);
    return no_handle;
}
} // namespace detail

inline Children::Iterator &Children::Iterator::operator++() noexcept {
    Handle next = no_handle;
    const rxfb_status status = rxfb_next_sibling(image_, handle_, &next);
    if (status != RXFB_OK) {
        *status_ = detail::status_of(status);
        next = no_handle;
    }
    handle_ = detail::land(image_, next, status_);
    return *this;
}

inline Children::Iterator Children::begin() const noexcept {
    status_ = Status::ok;
    Handle first = no_handle;
    const rxfb_status status = rxfb_first_child(image_, parent_, &first);
    if (status != RXFB_OK) {
        status_ = detail::status_of(status);
        first = no_handle;
    }
    return Iterator(image_, &status_, detail::land(image_, first, &status_));
}

} // namespace pymergetic::rxfb

#endif
