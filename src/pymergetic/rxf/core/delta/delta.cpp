#include <pymergetic/rxf/core/delta/delta.hpp>

#include <algorithm>
#include <cstring>
#include <map>
#include <set>

namespace pymergetic::rxf::core::delta {

namespace {

using Handle = pymergetic_rxf_handle;
constexpr Handle kNone = PYMERGETIC_RXF_HANDLE_NONE;

struct Pending {
    Handle object;
    bool in_ref;       // a reference entry, else a payload offset
    uint64_t index;    // reference index or payload offset
    std::string path;
    bool role = false; // the role of the reference, else its target
};

/* What a new object of this type is, when the operation does not say. */
uint32_t derived_kind(meta::Types &types, Handle type) {
    const meta::TypeInfo *t = types.get(type);
    return t != nullptr && t->node_kind != 0 ? t->node_kind : static_cast<uint32_t>(PYMERGETIC_RXF_NODE_DATA);
}

uint32_t kind_for(meta::Types &types, const Create &op, Handle type) {
    return op.kind != kKindDerived ? op.kind : derived_kind(types, type);
}

Status fail(Failure *f, size_t op, Status status, std::string message) {
    if (f != nullptr) {
        f->op = op;
        f->status = status;
        f->message = std::move(message);
    }
    return status;
}

void collect_subtree(const Image &image, Handle at, std::vector<Handle> *post) {
    for (Handle child : image.children(at)) collect_subtree(image, child, post);
    post->push_back(at);
}

} // namespace

Status make_image(uint64_t limit, uint64_t index_capacity, std::unique_ptr<Image> *out) {
    std::unique_ptr<Image> image;
    Status status = Image::create(limit, index_capacity, &image);
    if (status != Status::Ok) return status;
    status = image->bootstrap();
    if (status != Status::Ok) return status;
    *out = std::move(image);
    return Status::Ok;
}

/* The replay itself, on the image that is given: the caller has begun a transaction and
 * rolls it back when this does not answer Ok. */
static Status replay(Image &image, const Delta &delta, Failure *failure) {
    Image *work = &image;
    Status status = Status::Ok;
    bool log_full = false; // a patch of a forward reference found no room in the undo log
    meta::Types types(work);
    std::vector<Pending> pending;
    std::set<Handle> touched_types;

    // Paths in a payload are read as the object sees them: an alias is the one of the module the object sits in.
    const auto resolver_for = [&](Handle scope) -> meta::Resolver {
        return [&types, scope](const std::string &path) -> Handle { return meta::resolve_in(types, scope, path); };
    };
    const auto has_pending = [&](Handle h) {
        return std::any_of(pending.begin(), pending.end(), [&](const Pending &p) { return p.object == h; });
    };
    const auto touch = [&](Handle object, Handle type) {
        if (type == PYMERGETIC_RXF_HANDLE_TYPE) touched_types.insert(object);
        if (type == PYMERGETIC_RXF_HANDLE_FIELD) {
            const pymergetic_rxf_cell *c = work->arena().cell(object);
            if (c != nullptr) touched_types.insert(c->parent);
        }
        if (type == PYMERGETIC_RXF_HANDLE_TYPE || type == PYMERGETIC_RXF_HANDLE_FIELD) types.invalidate();
    };
    const auto make_refs = [&](const std::vector<RefSpec> &specs, Handle object, std::vector<pymergetic_rxf_ref> *refs,
                               std::vector<Pending> *later) {
        for (size_t i = 0; i < specs.size(); i++) {
            pymergetic_rxf_ref r{};
            r.role = specs[i].role.empty() ? kNone : work->resolve(specs[i].role);
            if (!specs[i].role.empty() && r.role == kNone) later->push_back({object, true, i, specs[i].role, true});
            r.index = specs[i].index;
            r.kind = static_cast<uint16_t>(specs[i].kind);
            r.binding = static_cast<uint16_t>(specs[i].binding);
            r.target = work->resolve(specs[i].target);
            if (r.target == kNone) later->push_back({object, true, i, specs[i].target});
            refs->push_back(r);
        }
    };
    // Patches every reference whose target exists now; the others stay pending. A patch can make
    // another resolvable (an alias whose target was open), so it runs until nothing changes.
    const auto resolve_pending = [&]() {
        size_t total = 0;
        for (;;) {
            size_t patched = 0;
            std::vector<Pending> rest;
            for (Pending &p : pending) {
                const Handle target = meta::resolve_in(types, p.object, p.path);
                if (target == kNone) {
                    rest.push_back(std::move(p));
                    continue;
                }
                if (p.in_ref) {
                    pymergetic_rxf_ref *refs = work->arena().refs_w(p.object);
                    if (refs == nullptr) {
                        log_full = true;
                        return total + patched;
                    }
                    (p.role ? refs[p.index].role : refs[p.index].target) = target;
                } else {
                    uint8_t *payload = work->arena().payload_w(p.object);
                    if (payload == nullptr) {
                        log_full = true;
                        return total + patched;
                    }
                    std::memcpy(payload + p.index, &target, 8);
                }
                patched++;
            }
            pending = std::move(rest);
            if (patched == 0) break;
            total += patched;
            types.invalidate();
        }
        return total;
    };

    // One Create. `retry` is set when it may work after more of the delta has been applied:
    // the parent or the type does not exist yet, or the type still has an unresolved Field.
    const auto create = [&](size_t n, const Create &c, bool *retry) -> Status {
        *retry = false;
        const Handle parent = work->resolve(c.parent);
        if (parent == kNone) {
            *retry = true;
            return fail(failure, n, Status::BadPath, "the parent does not exist: " + c.parent);
        }
        if (meta::is_ground(*work, parent)) return fail(failure, n, Status::Protected, "the ground cannot have new children: " + c.parent);
        const Handle type = types.find(c.type);
        if (type == kNone) {
            *retry = true;
            return fail(failure, n, Status::BadPath, "unknown type " + c.type);
        }
        std::string error;
        std::vector<uint8_t> payload;
        std::vector<meta::PendingRef> forward;
        Status s = meta::encode_payload(types, type, c.attrs, resolver_for(parent), &payload, &forward, &error, parent);
        if (s != Status::Ok) {
            *retry = s == Status::Dangling;
            return fail(failure, n, s, c.name + ": " + error);
        }
        object::ObjectSpec spec;
        spec.type = type;
        spec.parent = parent;
        spec.name = c.name;
        spec.kind = kind_for(types, c, type);
        spec.state = object::image_state(c.mobility);
        spec.owner_kind = c.owner_kind;
        spec.generation = c.generation;
        spec.index = c.index;
        spec.payload = std::move(payload);
        std::vector<Pending> later;
        make_refs(c.refs, kNone, &spec.refs, &later);
        Handle made = kNone;
        s = work->add(spec, &made);
        if (s != Status::Ok)
            return fail(failure, n, s, std::string("cannot create ") + (c.parent.empty() ? c.name : c.parent + "." + c.name) + ": " + arena::status_name(s));
        for (const meta::PendingRef &f : forward) pending.push_back({made, false, f.offset, f.path});
        for (Pending &p : later) {
            p.object = made;
            pending.push_back(std::move(p));
        }
        touch(made, type);
        if (spec.kind == PYMERGETIC_RXF_NODE_ENUMERATOR) types.invalidate();
        return Status::Ok;
    };

    size_t n = 0;
    while (n < delta.ops.size()) {
        if (std::holds_alternative<Create>(delta.ops[n])) {
            // A run of creates is a set: they may be given in any order. Creates that
            // wait for another (a parent, a type whose Fields point forward) are retried.
            size_t end = n;
            while (end < delta.ops.size() && std::holds_alternative<Create>(delta.ops[end])) end++;
            std::vector<size_t> todo;
            for (size_t i = n; i < end; i++) todo.push_back(i);
            while (!todo.empty()) {
                std::vector<size_t> waiting;
                Failure first;
                Status first_status = Status::Ok;
                bool progress = false;
                for (size_t i : todo) {
                    bool retry = false;
                    Failure local;
                    Failure *saved = failure;
                    failure = &local;
                    const Status s = create(i, std::get<Create>(delta.ops[i]), &retry);
                    failure = saved;
                    if (s == Status::Ok) {
                        progress = true;
                    } else if (retry) {
                        if (waiting.empty()) {
                            first = std::move(local);
                            first_status = s;
                        }
                        waiting.push_back(i);
                    } else {
                        if (failure != nullptr) *failure = std::move(local);
                        return s;
                    }
                }
                if (waiting.empty()) break;
                const size_t patched = resolve_pending();
                types.invalidate();
                if (!progress && patched == 0) {
                    if (failure != nullptr) *failure = std::move(first);
                    return first_status;
                }
                todo = std::move(waiting);
            }
            n = end;
            continue;
        }
        const Op &op = delta.ops[n];
        if (const Set *s = std::get_if<Set>(&op)) {
            resolve_pending();
            const Handle h = work->resolve(s->path);
            if (h == kNone || h == PYMERGETIC_RXF_HANDLE_ROOT) return fail(failure, n, Status::BadPath, "no such object: " + s->path);
            if (meta::is_ground(*work, h)) return fail(failure, n, Status::Protected, "the ground cannot change: " + s->path);
            if (has_pending(h)) return fail(failure, n, Status::Dangling, s->path + " still has unresolved references; give them in its create");
            const pymergetic_rxf_cell *cell = work->arena().cell(h);
            const Handle type = cell->type;
            if (!s->attrs.empty()) {
                std::string error;
                std::vector<Attr> merged;
                status = meta::decode_payload(types, h, &merged, &error);
                if (status != Status::Ok) return fail(failure, n, status, s->path + ": " + error);
                for (const Attr &a : s->attrs) {
                    const auto it = std::find_if(merged.begin(), merged.end(), [&](const Attr &m) { return m.name == a.name; });
                    if (it != merged.end()) it->value = a.value;
                    else merged.push_back(a);
                }
                std::vector<uint8_t> payload;
                std::vector<meta::PendingRef> forward;
                status = meta::encode_payload(types, type, merged, resolver_for(h), &payload, &forward, &error, cell->parent);
                if (status != Status::Ok) return fail(failure, n, status, s->path + ": " + error);
                status = work->set_payload(h, payload.data(), payload.size());
                if (status != Status::Ok) return fail(failure, n, status, "cannot change " + s->path + ": " + arena::status_name(status));
                for (const meta::PendingRef &f : forward) pending.push_back({h, false, f.offset, f.path});
            }
            if (s->has_refs) {
                std::vector<pymergetic_rxf_ref> refs;
                std::vector<Pending> later;
                make_refs(s->refs, h, &refs, &later);
                status = work->set_refs(h, refs.data(), static_cast<uint32_t>(refs.size()));
                if (status != Status::Ok) return fail(failure, n, status, "cannot change references of " + s->path);
                for (Pending &p : later) pending.push_back(std::move(p));
            }
            pymergetic_rxf_cell *mc = work->arena().cell_w(h);
            if (mc == nullptr) return fail(failure, n, Status::JournalFull, "the undo log is full: " + s->path);
            if (s->mobility) {
                uint32_t state = (mc->flags >> PYMERGETIC_RXF_FLAG_STATE_SHIFT) & PYMERGETIC_RXF_FLAG_STATE_MASK;
                state = (state & ~(3U << PYMERGETIC_RXF_STATE_MOBILITY_SHIFT)) | (*s->mobility << PYMERGETIC_RXF_STATE_MOBILITY_SHIFT);
                work->set_state(h, state);
            }
            if (s->owner_kind)
                mc->flags = (mc->flags & ~(PYMERGETIC_RXF_FLAG_OWNER_MASK << PYMERGETIC_RXF_FLAG_OWNER_SHIFT)) |
                            ((*s->owner_kind & PYMERGETIC_RXF_FLAG_OWNER_MASK) << PYMERGETIC_RXF_FLAG_OWNER_SHIFT);
            if (s->generation) mc->generation = *s->generation;
            if (s->index) mc->index = *s->index;
            touch(h, type);
        } else if (const Entry *e = std::get_if<Entry>(&op)) {
            resolve_pending();
            const Handle h = e->path.empty() ? kNone : work->resolve(e->path);
            if (!e->path.empty() && (h == kNone || h == PYMERGETIC_RXF_HANDLE_ROOT)) return fail(failure, n, Status::BadPath, "no such object: " + e->path);
            work->arena().mutable_header()->entry = h;
        } else {
            resolve_pending();
            const Delete &d = std::get<Delete>(op);
            const Handle h = work->resolve(d.path);
            if (h == kNone || h == PYMERGETIC_RXF_HANDLE_ROOT) return fail(failure, n, Status::BadPath, "no such object: " + d.path);
            std::vector<Handle> post;
            collect_subtree(*work, h, &post);
            for (Handle x : post)
                if (meta::is_ground(*work, x)) return fail(failure, n, Status::Protected, "the ground cannot be removed: " + d.path);
            std::vector<std::string> users = meta::referrers(types, post);
            const Handle entry = work->arena().header()->entry;
            if (entry != kNone && std::find(post.begin(), post.end(), entry) != post.end()) users.push_back("the entry of the image");
            if (!users.empty()) {
                Failure local;
                local.op = n;
                local.status = Status::Dangling;
                local.message = d.path + " is still in use";
                for (const std::string &u : users) local.issues.push_back({d.path, u});
                if (failure != nullptr) *failure = std::move(local);
                return Status::Dangling;
            }
            bool types_changed = false;
            for (Handle x : post) {
                const pymergetic_rxf_cell *c = work->arena().cell(x);
                if (c->type == PYMERGETIC_RXF_HANDLE_TYPE || c->type == PYMERGETIC_RXF_HANDLE_FIELD) types_changed = true;
                if (c->type == PYMERGETIC_RXF_HANDLE_FIELD) touched_types.insert(c->parent);
                touched_types.erase(x);
                pending.erase(std::remove_if(pending.begin(), pending.end(), [&](const Pending &p) { return p.object == x; }), pending.end());
                status = work->remove(x);
                if (status != Status::Ok) return fail(failure, n, status, "cannot remove " + d.path + ": " + arena::status_name(status));
            }
            if (types_changed) types.invalidate();
        }
        n++;
    }

    // Forward references that are still open must resolve now.
    resolve_pending();
    if (log_full) return fail(failure, static_cast<size_t>(-1), Status::JournalFull, "the undo log is full");
    if (!pending.empty()) {
        std::vector<meta::Issue> unresolved;
        for (const Pending &p : pending) unresolved.push_back({work->path(p.object), "reference to " + p.path + " does not resolve"});
        if (failure != nullptr) {
            failure->op = static_cast<size_t>(-1);
            failure->status = Status::Dangling;
            failure->message = std::to_string(unresolved.size()) + " references do not resolve";
            failure->issues = std::move(unresolved);
        }
        return Status::Dangling;
    }

    // The number of Field children is derived, not written.
    for (Handle t : touched_types) {
        const pymergetic_rxf_cell *c = work->arena().cell(t);
        if (c == nullptr || c->type != PYMERGETIC_RXF_HANDLE_TYPE || c->payload_size != sizeof(pymergetic_rxf_type)) continue;
        uint32_t count = 0;
        for (Handle child : work->children(t)) {
            const pymergetic_rxf_cell *cc = work->arena().cell(child);
            if (cc != nullptr && cc->type == PYMERGETIC_RXF_HANDLE_FIELD) count++;
        }
        uint8_t *payload = work->arena().payload_w(t);
        if (payload == nullptr) return fail(failure, static_cast<size_t>(-1), Status::JournalFull, "the undo log is full");
        reinterpret_cast<pymergetic_rxf_type *>(payload)->field_count = count;
    }
    types.invalidate();

    std::vector<meta::Issue> issues = meta::check(types);
    if (!issues.empty()) {
        if (failure != nullptr) {
            failure->op = static_cast<size_t>(-1);
            failure->status = Status::BadImage;
            failure->message = std::to_string(issues.size()) + " problems after the last operation";
            failure->issues = std::move(issues);
        }
        return Status::BadImage;
    }
    status = work->verify();
    if (status != Status::Ok) return fail(failure, static_cast<size_t>(-1), status, "the image does not verify");
    return Status::Ok;
}

Status apply(Image &image, const Delta &delta, Failure *failure) {
    Status status = image.begin();
    if (status != Status::Ok) return fail(failure, static_cast<size_t>(-1), status, "cannot begin a transaction");
    status = replay(image, delta, failure);
    if (status == Status::Ok) return image.commit();
    const Status undone = image.rollback();
    if (undone != Status::Ok) return fail(failure, static_cast<size_t>(-1), undone, "cannot roll back");
    return status;
}

std::string describe(const Failure &failure) {
    std::string text = failure.op == static_cast<size_t>(-1) ? "the delta does not link" : "operation " + std::to_string(failure.op);
    text += ": " + (failure.message.empty() ? std::string(arena::status_name(failure.status)) : failure.message);
    for (const meta::Issue &issue : failure.issues) text += "\n  " + (issue.path.empty() ? std::string("(root)") : issue.path) + ": " + issue.message;
    return text;
}

Status export_image(const Image &image, Delta *out, std::string *error, const std::string *module) {
    meta::Types types(&image);
    for (const std::string &name : types.duplicate_names()) {
        *error = "more than one Type object is named " + name;
        return Status::NameTaken;
    }
    const arena::Arena &arena = image.arena();
    out->ops.clear();
    // `module` limits the export to one Module object without its sub-modules ("" is the root).
    Handle start = PYMERGETIC_RXF_HANDLE_ROOT;
    if (module != nullptr) {
        start = image.resolve(*module);
        if (start == kNone) {
            *error = "no object at " + *module;
            return Status::BadHandle;
        }
        const pymergetic_rxf_cell *sc = arena.cell(start);
        if (start != PYMERGETIC_RXF_HANDLE_ROOT && (sc->flags & PYMERGETIC_RXF_FLAG_KIND_MASK) != PYMERGETIC_RXF_NODE_MODULE) {
            *error = *module + " is not a module";
            return Status::BadHandle;
        }
    }
    std::map<Handle, std::vector<std::pair<std::string, std::string>>> alias_cache;
    std::vector<Handle> stack;
    if (module == nullptr || start == PYMERGETIC_RXF_HANDLE_ROOT) {
        const std::vector<Handle> top = image.children(PYMERGETIC_RXF_HANDLE_ROOT);
        for (auto it = top.rbegin(); it != top.rend(); ++it) stack.push_back(*it);
    } else {
        stack.push_back(start);
    }
    while (!stack.empty()) {
        const Handle h = stack.back();
        stack.pop_back();
        if (module != nullptr && h != start) {
            const pymergetic_rxf_cell *hc = arena.cell(h);
            if ((hc->flags & PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_MODULE) continue; // a file of its own
        }
        // Aliases come first, so that a file starts with the short names it uses.
        std::vector<Handle> kids = image.children(h);
        const auto is_alias = [&](Handle k) {
            const pymergetic_rxf_cell *kc = arena.cell(k);
            return ((kc->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_ALIAS;
        };
        std::stable_partition(kids.begin(), kids.end(), is_alias);
        for (auto it = kids.rbegin(); it != kids.rend(); ++it) stack.push_back(*it);
        if (meta::is_ground(image, h)) continue;
        const pymergetic_rxf_cell *c = arena.cell(h);
        const meta::TypeInfo *t = types.get(c->type);
        if (t == nullptr) {
            *error = image.path(h) + ": the type is not a Type object";
            return Status::BadImage;
        }
        Create op;
        op.parent = image.path(c->parent);
        op.name = arena.name(h);
        op.type = t->name;
        const uint32_t kind = c->flags & PYMERGETIC_RXF_FLAG_KIND_MASK;
        if (kind != derived_kind(types, c->type)) op.kind = kind;
        const uint32_t state = (c->flags >> PYMERGETIC_RXF_FLAG_STATE_SHIFT) & PYMERGETIC_RXF_FLAG_STATE_MASK;
        op.mobility = (state >> PYMERGETIC_RXF_STATE_MOBILITY_SHIFT) & 3U;
        op.owner_kind = (c->flags >> PYMERGETIC_RXF_FLAG_OWNER_SHIFT) & PYMERGETIC_RXF_FLAG_OWNER_MASK;
        op.generation = c->generation;
        op.index = c->index;
        std::string problem;
        Status status = meta::decode_payload(types, h, &op.attrs, &problem);
        if (status != Status::Ok) {
            *error = image.path(h) + ": " + problem;
            return status;
        }
        // The aliases of the module the object sits in shorten the paths it holds; an alias itself keeps its target whole.
        const std::vector<std::pair<std::string, std::string>> *aliases = nullptr;
        if (kind != PYMERGETIC_RXF_NODE_ALIAS) {
            const Handle scope = meta::scope_of(image, h);
            auto found = alias_cache.find(scope);
            if (found == alias_cache.end()) found = alias_cache.emplace(scope, scope == kNone ? std::vector<std::pair<std::string, std::string>>() : meta::aliases_of(types, scope)).first;
            if (!found->second.empty()) aliases = &found->second;
        }
        if (aliases != nullptr)
            for (Attr &a : op.attrs)
                if (a.is_path) a.value = meta::shorten(*aliases, a.value);
        const pymergetic_rxf_ref *refs = arena.refs(h);
        for (uint32_t k = 0; k < c->ref_count; k++) {
            if (refs[k].target == kNone || arena.cell(refs[k].target) == nullptr) {
                *error = image.path(h) + ": a reference has no target";
                return Status::Dangling;
            }
            RefSpec r;
            if (refs[k].role != kNone) {
                if (arena.cell(refs[k].role) == nullptr) {
                    *error = image.path(h) + ": a reference names a dead role";
                    return Status::Dangling;
                }
                r.role = image.path(refs[k].role);
            }
            r.index = refs[k].index;
            r.kind = refs[k].kind;
            r.binding = refs[k].binding;
            r.target = image.path(refs[k].target);
            if (aliases != nullptr) {
                r.target = meta::shorten(*aliases, r.target);
                if (!r.role.empty()) r.role = meta::shorten(*aliases, r.role);
            }
            op.refs.push_back(std::move(r));
        }
        out->ops.push_back(std::move(op));
    }
    const Handle entry = arena.header()->entry;
    if (entry != kNone && (module == nullptr || module->empty())) {
        if (arena.cell(entry) == nullptr) {
            *error = "the entry is not a live object";
            return Status::Dangling;
        }
        out->ops.push_back(Entry{image.path(entry)});
    }
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::delta
