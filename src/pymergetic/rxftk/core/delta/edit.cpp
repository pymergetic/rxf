#include <pymergetic/rxftk/core/delta/edit.hpp>

#include <utility>

#include <pymergetic/rxftk/core/meta/types.hpp>
#include <pymergetic/rxftk/core/xml/xml.hpp>

namespace pymergetic::rxf::core::delta {

namespace {

using Handle = pymergetic_rxf_handle;
constexpr Handle kNone = PYMERGETIC_RXF_HANDLE_NONE;

Status refuse(std::string *error, Status status, std::string message) {
    if (error != nullptr)
        *error = std::move(message);
    return status;
}

/* One operation is one delta: it replays atomically, and its failure is the refusal text of
 * the delta (the operation, the reason, the link problems). */
Status one(Image &image, Op op, std::string *error) {
    Delta delta;
    delta.ops.push_back(std::move(op));
    Failure failure;
    const Status status = apply(image, delta, &failure);
    if (status != Status::Ok && error != nullptr)
        *error = describe(failure);
    return status;
}

/* The object must be live and ordinary: the root is not an object of the world. */
Status target_of(Image &image, Handle target, std::string *path, std::string *error) {
    if (target == PYMERGETIC_RXF_HANDLE_ROOT)
        return refuse(error, Status::Protected, "the root is not an ordinary object");
    if (!image.arena().live(target))
        return refuse(error, Status::BadHandle, "no such object");
    *path = image.path(target);
    return Status::Ok;
}

/* The one leaf a value may go to — or the refusal, said before the delta exists (docs/27):
 * a derived value or a declared list takes no value and no place. */
Status leaf_for(Image &image, Handle target, const std::string &field, meta::Leaf *leaf,
                std::string *error) {
    const pymergetic_rxf_cell *c = image.arena().cell(target);
    if (c == nullptr)
        return refuse(error, Status::BadHandle, "no such object");
    meta::Types types(&image);
    const meta::TypeInfo *t = c->type == kNone ? nullptr : types.get(c->type);
    if (t == nullptr)
        return refuse(error, Status::BadImage, "the object has no type");
    for (const meta::FieldInfo &m : t->members)
        if (m.name == field)
            return refuse(error, Status::Protected, "a declared list takes no value: " + field);
    if (field == meta::kDataAttr)
        return refuse(error, Status::Protected, "the tail bytes are raw: not a field");
    for (const meta::Leaf &l : types.layout(c->type).leaves) {
        if (l.name != field)
            continue;
        if (l.derived)
            return refuse(error, Status::Protected, "derived is never written: " + field);
        *leaf = l;
        return Status::Ok;
    }
    return refuse(error, Status::BadName, "no such field: " + field);
}

/* The empty attribute of a raw type of a fixed size: the cell is made at the type's size. */
void fill_raw(const Image &image, Handle type, std::vector<meta::Attr> *attrs) {
    meta::Types types(&image);
    const meta::Layout &L = types.layout(type);
    if (L.error.empty() && L.raw && L.fixed)
        attrs->push_back({meta::kDataAttr, std::string(2 * L.prefix, '0')});
}

} // namespace

Status edit_create(Image &image, Handle type, Handle parent, const std::string &name, Handle *out,
                   std::string *error) {
    if (type != kNone && !image.arena().live(type))
        return refuse(error, Status::BadHandle, "no such type");
    const std::string type_path = image.path(type);
    if (type_path.empty())
        return refuse(error, Status::BadPath, "a type is named by path, and the root is none");
    std::string parent_path;
    if (parent != PYMERGETIC_RXF_HANDLE_ROOT) {
        const Status status = target_of(image, parent, &parent_path, error);
        if (status != Status::Ok)
            return status;
    }
    Create op;
    op.parent = parent_path;
    op.name = name;
    op.type = type_path;
    fill_raw(image, type, &op.attrs);
    const Status status = one(image, std::move(op), error);
    if (status != Status::Ok)
        return status;
    if (out != nullptr)
        *out = image.resolve(parent_path.empty() ? name : parent_path + "." + name);
    return Status::Ok;
}

Status edit_rename(Image &image, Handle target, const std::string &name, std::string *error) {
    std::string path;
    Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    Set op;
    op.path = path;
    op.name = name;
    return one(image, std::move(op), error);
}

Status edit_move(Image &image, Handle target, Handle parent, std::string *error) {
    std::string path;
    Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    std::string parent_path;
    if (parent != PYMERGETIC_RXF_HANDLE_ROOT) {
        status = target_of(image, parent, &parent_path, error);
        if (status != Status::Ok)
            return status;
    }
    Set op;
    op.path = path;
    op.parent = parent_path;
    return one(image, std::move(op), error);
}

Status edit_delete(Image &image, Handle target, std::string *error) {
    std::string path;
    const Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    Delete op;
    op.path = path;
    return one(image, std::move(op), error);
}

Status edit_put(Image &image, Handle target, const std::string &field, const std::string &value,
                std::string *error) {
    std::string path;
    Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    meta::Leaf leaf;
    status = leaf_for(image, target, field, &leaf, error);
    if (status != Status::Ok)
        return status;
    if (leaf.kind == meta::LeafKind::Ref)
        return refuse(error, Status::Protected, "a reference is written by ref(): " + field);
    Set op;
    op.path = path;
    op.attrs.push_back({field, value, leaf.kind == meta::LeafKind::Enum});
    return one(image, std::move(op), error);
}

Status edit_ref(Image &image, Handle target, const std::string &field, Handle to,
                std::string *error) {
    std::string path;
    Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    meta::Leaf leaf;
    status = leaf_for(image, target, field, &leaf, error);
    if (status != Status::Ok)
        return status;
    if (leaf.kind != meta::LeafKind::Ref)
        return refuse(error, Status::Protected, "only a reference field takes a handle: " + field);
    if (to == PYMERGETIC_RXF_HANDLE_ROOT)
        return refuse(error, Status::Protected, "the root cannot be a reference target");
    if (!image.arena().live(to))
        return refuse(error, Status::BadHandle, "no such object");
    Set op;
    op.path = path;
    op.attrs.push_back({field, image.path(to), true});
    return one(image, std::move(op), error);
}

Status edit_unset(Image &image, Handle target, const std::string &field, std::string *error) {
    std::string path;
    Status status = target_of(image, target, &path, error);
    if (status != Status::Ok)
        return status;
    meta::Leaf leaf;
    status = leaf_for(image, target, field, &leaf, error);
    if (status != Status::Ok)
        return status;
    if (!leaf.optional)
        return refuse(error, Status::Protected, "only an optional field may be unset: " + field);
    Set op;
    op.path = path;
    op.attrs.push_back({field, "", true});
    return one(image, std::move(op), error);
}

std::string Editor::create(const std::string &type, const std::string &parent,
                           const std::string &name) {
    Create op;
    op.parent = parent;
    op.name = name;
    op.type = type;
    const Handle t = image_->resolve(type);
    if (t != kNone)
        fill_raw(*image_, t, &op.attrs);
    delta_.ops.push_back(std::move(op));
    return parent.empty() ? name : parent + "." + name;
}

void Editor::rename(const std::string &path, const std::string &name) {
    Set op;
    op.path = path;
    op.name = name;
    delta_.ops.push_back(std::move(op));
}

void Editor::move(const std::string &path, const std::string &parent) {
    Set op;
    op.path = path;
    op.parent = parent;
    delta_.ops.push_back(std::move(op));
}

void Editor::remove(const std::string &path) {
    Delete op;
    op.path = path;
    delta_.ops.push_back(std::move(op));
}

void Editor::put(const std::string &path, const std::string &field, const std::string &value) {
    Set op;
    op.path = path;
    op.attrs.push_back({field, value, false});
    delta_.ops.push_back(std::move(op));
}

void Editor::ref(const std::string &path, const std::string &field, const std::string &target) {
    Set op;
    op.path = path;
    op.attrs.push_back({field, target, true});
    delta_.ops.push_back(std::move(op));
}

void Editor::unset(const std::string &path, const std::string &field) {
    Set op;
    op.path = path;
    op.attrs.push_back({field, "", true});
    delta_.ops.push_back(std::move(op));
}

void Editor::entry(const std::string &path) {
    Entry op;
    op.path = path;
    delta_.ops.push_back(std::move(op));
}

Status Editor::commit(std::string *delta_text, std::string *error) {
    if (!open_)
        return refuse(error, Status::BadImage, "the edit is already closed");
    std::string text;
    std::string problem;
    if (xml::write(delta_, &text, &problem) != Status::Ok)
        return refuse(error, Status::BadImage, "the delta does not write down: " + problem);
    Failure failure;
    const Status status = apply(*image_, delta_, &failure);
    if (status != Status::Ok) {
        open_ = false; // all of it or none of it: the image is unchanged (apply says so)
        return refuse(error, status, describe(failure));
    }
    open_ = false;
    if (delta_text != nullptr)
        *delta_text = std::move(text);
    return Status::Ok;
}

void Editor::rollback() {
    delta_.ops.clear();
    open_ = false;
}

} // namespace pymergetic::rxf::core::delta
