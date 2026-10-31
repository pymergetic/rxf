/*
 * pymergetic.rxfb: the CPython face of the rxfb reader library (ROADMAP section 2, L1c).
 *
 * One extension over the SAME C core (rxfb/rxfb.c, linked as the `rxfb` library): this file only
 * translates. Every answer comes from an rxfb_* call; nothing of the reader is written twice.
 *
 * Zero copy. `attach(buffer)` takes a read-only memoryview of any object with the buffer protocol
 * (bytes, bytearray, mmap, a numpy array) and pins its export for as long as the Image lives: the
 * image is never copied, and a bytearray cannot be resized under it. Every payload, every code
 * body and every table of reference rows is a slice of that one memoryview, so it shares the
 * caller's bytes - change a byte in the bytearray and the view shows it. The views are read-only.
 *
 * The image is untrusted, as in the C core: a refusal is a typed exception (RxfbError and its
 * subclasses, each carrying the rxfb_status as `status`), never a crash. An in-place change of
 * the bytes after attach is the caller's to make good; the core re-checks every record it reads.
 */

#include <cstdint>
#include <functional>
#include <string>
#include <utility>

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include "../rxfb.h"

namespace nb = nanobind;

namespace {

/* ---- the exceptions: one class per refusal of the C core ----------------------------------- */

PyObject *error_base = nullptr;
PyObject *error_classes[RXFB_NOT_FOUND + 1] = {};

nb::object make_error(const char *name, nb::handle base, nb::handle mixin, int status) {
    nb::dict body;
    body["status"] = status;
    body["__module__"] = "pymergetic.rxfb";
    nb::tuple bases = mixin.is_valid() ? nb::make_tuple(base, mixin) : nb::make_tuple(base);
    return nb::module_::import_("builtins").attr("type")(name, bases, body);
}

void make_errors(nb::module_ &m) {
    const nb::object base = make_error("RxfbError", nb::borrow(PyExc_Exception), nb::handle(), -1);
    error_base = base.inc_ref().ptr();
    m.attr("RxfbError") = base;
    struct Spec {
        const char *name;
        rxfb_status status;
        PyObject *mixin;
    };
    const Spec specs[] = {
        {"BadImage", RXFB_BAD_IMAGE, PyExc_ValueError},
        {"BadHandle", RXFB_BAD_HANDLE, PyExc_LookupError},
        {"BadName", RXFB_BAD_NAME, PyExc_ValueError},
        {"Protected", RXFB_PROTECTED, nullptr},
        {"NotFound", RXFB_NOT_FOUND, PyExc_LookupError},
    };
    for (const Spec &spec : specs) {
        const nb::object cls = make_error(
            spec.name, base, spec.mixin != nullptr ? nb::borrow(spec.mixin) : nb::handle(),
            static_cast<int>(spec.status));
        error_classes[spec.status] = cls.inc_ref().ptr();
        m.attr(spec.name) = cls;
    }
}

const char *status_text(rxfb_status status) {
    switch (status) {
    case RXFB_OK:
        return "ok";
    case RXFB_BAD_IMAGE:
        return "not a usable RXFB v7 image";
    case RXFB_BAD_HANDLE:
        return "stale, out of range or no handle";
    case RXFB_BAD_NAME:
        return "an empty name or path segment";
    case RXFB_PROTECTED:
        return "the object index has no children";
    case RXFB_NO_ROOM:
        return "the buffer is too small";
    case RXFB_NOT_FOUND:
        return "nothing is named so";
    }
    return "unknown status";
}

[[noreturn]] void refuse(rxfb_status status, const std::string &what) {
    PyObject *cls = (status < RXFB_NOT_FOUND + 1 && error_classes[status] != nullptr)
                        ? error_classes[status]
                        : error_base;
    const std::string text = what + ": " + status_text(status);
    nb::object instance = nb::borrow(cls)(text);
    if (cls == error_base)
        instance.attr("status") = static_cast<int>(status);
    PyErr_SetObject(cls, instance.ptr());
    throw nb::python_error();
}

void check(rxfb_status status, const std::string &what) {
    if (status != RXFB_OK)
        refuse(status, what);
}

/* ---- the image ------------------------------------------------------------------------------ */

struct Image {
    nb::object view; /* the whole image: a read-only, flat, byte-format memoryview */
    rxfb rx;
};

Image &image_of(nb::handle owner) {
    return nb::cast<Image &>(owner);
}

/* The slice [begin, begin + size) of the image as a read-only memoryview: a view of a view, so the
 * caller's buffer is shared and nothing is copied. The pointer is checked against the mapping. */
nb::object slice_of(const Image &image, const uint8_t *begin, uint64_t size) {
    if (begin < image.rx.base || static_cast<uint64_t>(begin - image.rx.base) > image.rx.size ||
        size > image.rx.size - static_cast<uint64_t>(begin - image.rx.base))
        refuse(RXFB_BAD_IMAGE, "a view outside the image");
    const auto from = static_cast<Py_ssize_t>(begin - image.rx.base);
    const auto upto = static_cast<Py_ssize_t>(from + static_cast<Py_ssize_t>(size));
    nb::object bounds =
        nb::steal(PySlice_New(PyLong_FromSsize_t(from), PyLong_FromSsize_t(upto), nullptr));
    if (!bounds.is_valid())
        throw nb::python_error();
    nb::object part = nb::steal(PyObject_GetItem(image.view.ptr(), bounds.ptr()));
    if (!part.is_valid())
        throw nb::python_error();
    return part;
}

/* A handle argument: any Python int; out of the 32-bit range is a bad handle, not an overflow. */
rxfb_handle handle_arg(nb::handle value) {
    if (!PyLong_Check(value.ptr()))
        throw nb::type_error("a handle is an int");
    int overflow = 0;
    const long long number = PyLong_AsLongLongAndOverflow(value.ptr(), &overflow);
    if (number == -1 && PyErr_Occurred())
        throw nb::python_error();
    if (overflow != 0 || number < 0 || number > static_cast<long long>(UINT32_MAX))
        refuse(RXFB_BAD_HANDLE, "handle " + nb::cast<std::string>(nb::str(value)));
    return static_cast<rxfb_handle>(number);
}

/* Text of a path or name: str (UTF-8, undecodable bytes survive as surrogate escapes) or bytes. */
std::string text_arg(nb::handle value) {
    if (PyBytes_Check(value.ptr()))
        return std::string(PyBytes_AsString(value.ptr()),
                           static_cast<size_t>(PyBytes_Size(value.ptr())));
    if (!PyUnicode_Check(value.ptr()))
        throw nb::type_error("a path is a str or bytes");
    nb::object encoded =
        nb::steal(PyUnicode_AsEncodedString(value.ptr(), "utf-8", "surrogateescape"));
    if (!encoded.is_valid())
        throw nb::python_error();
    return std::string(PyBytes_AsString(encoded.ptr()),
                       static_cast<size_t>(PyBytes_Size(encoded.ptr())));
}

nb::object text_out(const char *text, uint32_t length) {
    nb::object out =
        nb::steal(PyUnicode_DecodeUTF8(text, static_cast<Py_ssize_t>(length), "surrogateescape"));
    if (!out.is_valid())
        throw nb::python_error();
    return out;
}

nb::object optional_handle(rxfb_handle handle) {
    return handle == RXFB_HANDLE_NONE ? nb::none() : nb::cast(handle);
}

/* ---- the views the image hands out ---------------------------------------------------------- */

/* The object a handle names. Holds its Image (the owner), so the view of the bytes outlives every
 * Python name of the image. Construction checks the handle against the record. */
struct Object {
    nb::object owner;
    rxfb_handle handle;

    const Image &image() const {
        return image_of(owner);
    }
    const rxfb_object &record() const {
        const rxfb_object *found = nullptr;
        check(rxfb_record(&image().rx, handle, &found), "object " + std::to_string(handle));
        return *found;
    }
};

Object object_at(nb::handle owner, rxfb_handle handle) {
    Object object{nb::borrow(owner), handle};
    (void)object.record();
    return object;
}

struct Spec {
    rxfb_handle type;
    uint32_t flags;
    uint32_t index;
    uint64_t generation;
};

struct Ref {
    uint64_t target;
    uint64_t role;
    uint16_t kind;
    uint16_t binding;
    uint32_t index;
};

struct Code {
    nb::object owner;
    uint64_t entry; /* the offset of the first byte in the image */
    uint64_t length;
    rxfb_handle code;
    rxfb_handle blob;
};

struct Children {
    nb::object owner;
    rxfb_handle parent;
};

struct ChildIterator {
    nb::object owner;
    rxfb_handle next;
    uint32_t visited;
};

struct Refs {
    nb::object owner;
    const rxfb_ref *rows;
    uint32_t count;
};

struct RefIterator {
    nb::object refs;
    uint32_t at;
};

/* The reference rows of an object, checked to lie inside the image. */
Refs refs_of(nb::handle owner, rxfb_handle handle) {
    const Image &image = image_of(owner);
    const rxfb_ref *rows = nullptr;
    uint32_t count = 0;
    check(rxfb_refs(&image.rx, handle, &rows, &count), "refs of " + std::to_string(handle));
    if (count > 0) {
        const auto *begin = reinterpret_cast<const uint8_t *>(rows);
        (void)slice_of(image, begin, static_cast<uint64_t>(count) * sizeof(rxfb_ref));
    }
    return Refs{nb::borrow(owner), rows, count};
}

/* The rows of one table object of sys/relations, checked to lie inside the image. */
Refs table_of(nb::handle owner, rxfb_handle handle) {
    const Image &image = image_of(owner);
    const rxfb_ref *rows = nullptr;
    uint32_t count = 0;
    check(rxfb_table(&image.rx, handle, &rows, &count), "table of " + std::to_string(handle));
    if (count > 0) {
        const auto *begin = reinterpret_cast<const uint8_t *>(rows);
        (void)slice_of(image, begin, static_cast<uint64_t>(count) * sizeof(rxfb_ref));
    }
    return Refs{nb::borrow(owner), rows, count};
}

Ref ref_row(const rxfb_ref &row) {
    return Ref{row.target, row.role, row.kind, row.binding, row.index};
}

Code code_of(nb::handle owner, const rxfb_code &found) {
    const Image &image = image_of(owner);
    (void)slice_of(image, found.entry, found.length);
    return Code{nb::borrow(owner), static_cast<uint64_t>(found.entry - image.rx.base), found.length,
                found.code, found.blob};
}

std::string machine_arg(const std::string &machine) {
    if (machine.find('\0') != std::string::npos)
        refuse(RXFB_BAD_NAME, "a machine name has no NUL");
    return machine;
}

std::string path_of(const Image &image, rxfb_handle handle) {
    std::string text(256, '\0');
    for (;;) {
        uint32_t written = 0;
        const rxfb_status status =
            rxfb_path(&image.rx, handle, text.data(), static_cast<uint32_t>(text.size()), &written);
        if (status == RXFB_NO_ROOM) {
            text.assign(static_cast<size_t>(written), '\0');
            continue;
        }
        check(status, "path of " + std::to_string(handle));
        text.resize(written);
        return text;
    }
}

Image attach(nb::handle exporter) {
    /* The export is taken first so that an object without the buffer protocol, or without a flat
     * run of bytes, is refused here (BufferError/TypeError), before any byte is read. */
    Py_buffer probe;
    if (PyObject_GetBuffer(exporter.ptr(), &probe, PyBUF_SIMPLE) != 0)
        throw nb::python_error();
    PyBuffer_Release(&probe);

    nb::object whole = nb::steal(PyMemoryView_FromObject(exporter.ptr()));
    if (!whole.is_valid())
        throw nb::python_error();
    nb::object flat = whole.attr("toreadonly")().attr("cast")("B");
    const Py_buffer *held = PyMemoryView_GET_BUFFER(flat.ptr());

    Image image{flat, {}};
    check(rxfb_attach(&image.rx, held->buf, static_cast<uint64_t>(held->len)), "attach");
    return image;
}

} // namespace

NB_MODULE(rxfb, m) {
    m.doc() = "The slim reader of an RXFB v7 image: zero-copy views of the mapped bytes.";
    make_errors(m);

    m.attr("HANDLE_NONE") = static_cast<uint32_t>(RXFB_HANDLE_NONE);
    m.attr("RUN_PATH") = RXFB_RUN_PATH;
    m.attr("OBJ_MODULE") = static_cast<int>(RXFB_OBJ_MODULE);
    m.attr("OBJ_FUNC") = static_cast<int>(RXFB_OBJ_FUNC);
    m.attr("OBJ_HASH") = static_cast<int>(RXFB_OBJ_HASH);
    m.attr("OBJ_SPEC") = static_cast<int>(RXFB_OBJ_SPEC);
    m.attr("OBJ_SYM") = static_cast<int>(RXFB_OBJ_SYM);
    m.attr("OBJ_BLOB") = static_cast<int>(RXFB_OBJ_BLOB);
    m.attr("OBJ_FRAME") = static_cast<int>(RXFB_OBJ_FRAME);
    m.attr("OBJ_JOURNAL") = static_cast<int>(RXFB_OBJ_JOURNAL);
    m.attr("REF_CALL") = static_cast<int>(RXFB_REF_CALL);
    m.attr("REF_DATA") = static_cast<int>(RXFB_REF_DATA);
    m.attr("REF_ENTRY") = static_cast<int>(RXFB_REF_ENTRY);
    m.attr("REF_TYPE") = static_cast<int>(RXFB_REF_TYPE);
    m.attr("REF_IMPORT") = static_cast<int>(RXFB_REF_IMPORT);
    m.attr("REF_IMPLEMENTATION") = static_cast<int>(RXFB_REF_IMPLEMENTATION);
    m.attr("REF_OWNER") = static_cast<int>(RXFB_REF_OWNER);
    m.attr("REF_DOC") = static_cast<int>(RXFB_REF_DOC);

    nb::class_<Spec>(m, "Spec", "The semantic row of an object (sys/specs).")
        .def_prop_ro(
            "type", [](const Spec &spec) { return optional_handle(spec.type); },
            nb::sig("def type(self) -> int | None"),
            "The handle of the Type object, or None when the object is untyped.")
        .def_ro("flags", &Spec::flags, "The world's flags word: kind | state << 8 | owner << 14.")
        .def_ro("index", &Spec::index, "The position among ordered siblings; 0: ordered by name.")
        .def_ro("generation", &Spec::generation, "The world's generation of the object.")
        .def("__repr__", [](const Spec &spec) {
            return "Spec(type=" + std::to_string(spec.type) +
                   ", flags=" + std::to_string(spec.flags) +
                   ", index=" + std::to_string(spec.index) +
                   ", generation=" + std::to_string(spec.generation) + ")";
        });

    nb::class_<Ref>(m, "Ref", "One reference row of an object (sys/relations).")
        .def_prop_ro(
            "target", [](const Ref &ref) { return ref.target; }, "The handle of the target.")
        .def_prop_ro(
            "role",
            [](const Ref &ref) {
                return ref.role == RXFB_REF_NONE ? nb::object(nb::none()) : nb::cast(ref.role);
            },
            nb::sig("def role(self) -> int | None"), "The handle of the role, or None.")
        .def_ro("kind", &Ref::kind, "One of the REF_* kinds.")
        .def_ro("binding", &Ref::binding, "0 mandatory, 1 optional.")
        .def_ro("index", &Ref::index, "The argument or leaf number.");

    nb::class_<Code>(m, "Code", "The bytes of a function for one machine.")
        .def_prop_ro(
            "view",
            [](const Code &code) {
                return slice_of(image_of(code.owner), image_of(code.owner).rx.base + code.entry,
                                code.length);
            },
            nb::sig("def view(self) -> memoryview"),
            "The bytes, a read-only memoryview into the image.")
        .def_ro("entry", &Code::entry, "The offset of the first byte in the image.")
        .def_ro("length", &Code::length, "The number of bytes.")
        .def_ro("code", &Code::code, "The handle of the Code object.")
        .def_ro("blob", &Code::blob, "The handle of the CodeBlob object.")
        .def("__len__", [](const Code &code) { return code.length; });

    nb::class_<ChildIterator>(m, "ChildIterator")
        .def(
            "__iter__", [](nb::handle self) { return nb::borrow(self); },
            nb::sig("def __iter__(self) -> ChildIterator"))
        .def("__next__", [](ChildIterator &it) {
            if (it.next == RXFB_HANDLE_NONE)
                throw nb::stop_iteration();
            const Image &image = image_of(it.owner);
            if (++it.visited > rxfb_count(&image.rx))
                refuse(RXFB_BAD_IMAGE, "a sibling chain that does not end");
            Object object = object_at(it.owner, it.next);
            check(rxfb_next_sibling(&image.rx, it.next, &it.next), "next sibling");
            return object;
        });

    nb::class_<Children>(m, "Children", "The children of an object, in chain order.")
        .def("__iter__",
             [](const Children &children) {
                 const Image &image = image_of(children.owner);
                 rxfb_handle first = RXFB_HANDLE_NONE;
                 check(rxfb_first_child(&image.rx, children.parent, &first), "children");
                 return ChildIterator{children.owner, first, 0};
             })
        .def("__len__", [](const Children &children) {
            uint32_t total = 0;
            const rxfb_status status =
                rxfb_children(&image_of(children.owner).rx, children.parent, nullptr, 0, &total);
            if (status != RXFB_NO_ROOM)
                check(status, "children");
            return total;
        });

    nb::class_<Object>(m, "Object", "An object of the image: its record, payload and neighbours.")
        .def_prop_ro("handle", [](const Object &o) { return o.handle; })
        .def_prop_ro(
            "kind", [](const Object &o) { return o.record().kind; }, "One of the OBJ_* kinds.")
        .def_prop_ro("generation", [](const Object &o) { return o.record().gen; })
        .def_prop_ro("flags", [](const Object &o) { return o.record().flags; })
        .def_prop_ro(
            "name_id", [](const Object &o) { return o.record().name; },
            "The interned symbol id (0: anonymous).")
        .def_prop_ro(
            "name",
            [](const Object &o) {
                const char *text = nullptr;
                uint32_t length = 0;
                check(rxfb_name(&o.image().rx, o.handle, &text, &length), "name");
                return text == nullptr ? nb::object(nb::none()) : text_out(text, length);
            },
            nb::sig("def name(self) -> str | None"),
            "The name of the object, or None when it is anonymous.")
        .def_prop_ro(
            "parent", [](const Object &o) { return optional_handle(o.record().parent); },
            nb::sig("def parent(self) -> int | None"),
            "The handle of the parent object, or None for the root.")
        .def_prop_ro(
            "link", [](const Object &o) { return optional_handle(o.record().link); },
            nb::sig("def link(self) -> int | None"), "The handle of the next sibling, or None.")
        .def_prop_ro("refcount", [](const Object &o) { return o.record().refs; })
        .def_prop_ro("payload_size", [](const Object &o) { return o.record().payload; })
        .def_prop_ro(
            "payload",
            [](const Object &o) {
                const uint8_t *bytes = nullptr;
                uint32_t size = 0;
                check(rxfb_payload(&o.image().rx, o.handle, &bytes, &size), "payload");
                return slice_of(o.image(), bytes, size);
            },
            nb::sig("def payload(self) -> memoryview"),
            "The payload: a read-only memoryview slice of the image, never a copy.")
        .def(
            "path",
            [](const Object &o) {
                const std::string text = path_of(o.image(), o.handle);
                return text_out(text.data(), static_cast<uint32_t>(text.size()));
            },
            nb::sig("def path(self) -> str"),
            "The dotted path from the root (the root's own is the empty string).")
        .def(
            "spec",
            [](const Object &o) {
                const rxfb_spec_row *row = nullptr;
                check(rxfb_spec(&o.image().rx, o.handle, &row), "spec");
                return Spec{row->type, row->flags, row->index, row->generation};
            },
            "The semantic row of the object.")
        .def(
            "children",
            [](const Object &o) {
                rxfb_handle first = RXFB_HANDLE_NONE; /* refuses the index and a bad handle now */
                check(rxfb_first_child(&o.image().rx, o.handle, &first), "children");
                return Children{o.owner, o.handle};
            },
            "The children in chain order, an iterable that can be walked again.")
        .def(
            "refs", [](const Object &o) { return refs_of(o.owner, o.handle); },
            "The reference rows of the object.")
        .def(
            "table", [](const Object &o) { return table_of(o.owner, o.handle); },
            "The object read as a reference table: an object of sys/relations, named by the path of "
            "the object whose rows it holds. Any other object is a BadHandle.")
        .def(
            "code",
            [](const Object &o, const std::string &machine) {
                const std::string name = machine_arg(machine);
                rxfb_code found;
                check(rxfb_code_of(&o.image().rx, o.handle, name.c_str(), &found), "code");
                return code_of(o.owner, found);
            },
            nb::arg("machine"), "The bytes of this function for a machine (e.g. 'wasm32').")
        .def("__eq__",
             [](const Object &a, nb::handle other) {
                 if (!nb::isinstance<Object>(other))
                     return false;
                 const Object &b = nb::cast<const Object &>(other);
                 return a.owner.is(b.owner) && a.handle == b.handle;
             })
        .def("__hash__",
             [](const Object &o) {
                 return std::hash<uint64_t>()((reinterpret_cast<uintptr_t>(o.owner.ptr()) << 1) ^
                                              o.handle);
             })
        .def("__repr__", [](const Object &o) {
            return "<rxfb.Object handle=" + std::to_string(o.handle) +
                   " path=" + path_of(o.image(), o.handle) + ">";
        });

    nb::class_<RefIterator>(m, "RefIterator")
        .def(
            "__iter__", [](nb::handle self) { return nb::borrow(self); },
            nb::sig("def __iter__(self) -> RefIterator"))
        .def("__next__", [](RefIterator &it) {
            const Refs &refs = nb::cast<const Refs &>(it.refs);
            if (it.at >= refs.count)
                throw nb::stop_iteration();
            return ref_row(refs.rows[it.at++]);
        });

    nb::class_<Refs>(m, "Refs", "The reference rows of an object: a sequence of Ref.")
        .def("__len__", [](const Refs &refs) { return refs.count; })
        .def("__iter__", [](nb::handle self) { return RefIterator{nb::borrow(self), 0}; })
        .def("__getitem__",
             [](const Refs &refs, int64_t index) {
                 const int64_t at = index < 0 ? index + static_cast<int64_t>(refs.count) : index;
                 if (at < 0 || at >= static_cast<int64_t>(refs.count))
                     throw nb::index_error("a reference index is out of range");
                 return ref_row(refs.rows[at]);
             })
        .def_prop_ro(
            "view",
            [](const Refs &refs) {
                const Image &image = image_of(refs.owner);
                const auto *begin = reinterpret_cast<const uint8_t *>(refs.rows);
                return slice_of(image, refs.count == 0 ? image.rx.base : begin,
                                static_cast<uint64_t>(refs.count) * sizeof(rxfb_ref));
            },
            nb::sig("def view(self) -> memoryview"),
            "The raw 24-byte rows: a read-only memoryview into the image.");

    nb::class_<Image>(m, "Image", "An attached image: views onto the caller's bytes.")
        .def_prop_ro(
            "view", [](const Image &image) { return image.view; },
            nb::sig("def view(self) -> memoryview"),
            "The whole image: a read-only memoryview of the caller's buffer.")
        .def_prop_ro("size", [](const Image &image) { return image.rx.size; })
        .def_prop_ro(
            "count", [](const Image &image) { return rxfb_count(&image.rx); },
            "The handles (index slots) the image holds.")
        .def_prop_ro("root",
                     [](nb::handle self) { return object_at(self, rxfb_root(&image_of(self).rx)); })
        .def_prop_ro(
            "entry",
            [](nb::handle self) -> nb::object {
                const rxfb_handle entry = rxfb_entry(&image_of(self).rx);
                if (entry == RXFB_HANDLE_NONE)
                    return nb::none();
                return nb::cast(object_at(self, entry));
            },
            nb::sig("def entry(self) -> Object | None"),
            "The entry object, or None when the image has none.")
        .def(
            "get",
            [](nb::handle self, nb::handle handle) { return object_at(self, handle_arg(handle)); },
            nb::sig("def get(self, handle: int) -> Object"), nb::arg("handle"),
            "The object a handle names.")
        .def(
            "lookup",
            [](nb::handle self, nb::handle path) {
                const std::string text = text_arg(path);
                rxfb_handle found = RXFB_HANDLE_NONE;
                check(rxfb_lookup(&image_of(self).rx, text.data(),
                                  static_cast<uint32_t>(text.size()), &found),
                      "lookup '" + text + "'");
                return object_at(self, found);
            },
            nb::sig("def lookup(self, path: str | bytes) -> Object"), nb::arg("path"),
            "The object at a dotted path ('' is the root).")
        .def(
            "__getitem__",
            [](nb::handle self, nb::handle key) -> nb::object {
                if (PyLong_Check(key.ptr()))
                    return nb::cast(object_at(self, handle_arg(key)));
                const std::string text = text_arg(key);
                rxfb_handle found = RXFB_HANDLE_NONE;
                check(rxfb_lookup(&image_of(self).rx, text.data(),
                                  static_cast<uint32_t>(text.size()), &found),
                      "lookup '" + text + "'");
                return nb::cast(object_at(self, found));
            },
            nb::sig("def __getitem__(self, key: int | str | bytes, /) -> Object"))
        .def("__len__", [](const Image &image) { return rxfb_count(&image.rx); })
        .def(
            "symbol",
            [](const Image &image, uint32_t id) {
                const char *text = nullptr;
                uint32_t length = 0;
                check(rxfb_symbol(&image.rx, id, &text, &length), "symbol " + std::to_string(id));
                return text_out(text, length);
            },
            nb::sig("def symbol(self, id: int) -> str"), nb::arg("id"),
            "The text of an interned name.")
        .def(
            "slot",
            [](nb::handle self, uint32_t slot, const std::string &machine, nb::handle run) {
                const std::string name = machine_arg(machine);
                const Image &image = image_of(self);
                rxfb_code found;
                if (run.is_none()) {
                    check(rxfb_slot(&image.rx, name.c_str(), slot, &found), "slot");
                } else {
                    const Object &runner = nb::cast<const Object &>(run);
                    check(rxfb_slot_of(&image.rx, runner.handle, name.c_str(), slot, &found),
                          "slot");
                }
                return code_of(self, found);
            },
            nb::sig("def slot(self, slot: int, machine: str, run: Object | None = None) -> Code"),
            nb::arg("slot"), nb::arg("machine"), nb::arg("run") = nb::none(),
            "The bytes of function slot `slot` of the interpreter (RUN_PATH, or `run`) for a "
            "machine.");

    m.def("attach", &attach, nb::sig("def attach(buffer: collections.abc.Buffer) -> Image"),
          "Validates an image and maps it in place: no byte is copied, the buffer stays exported "
          "for as long as the Image lives.");
}
