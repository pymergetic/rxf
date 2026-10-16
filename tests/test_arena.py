"""The C++ arena through the nanobind module: allocation, index, names, compaction."""

import pytest

from pymergetic.rxftk.core.arena import HANDLE_NONE, ArenaError
from pymergetic.rxftk.core.object import Image

NONE = HANDLE_NONE

KIND_MODULE = 19
KIND_DATA = 8
MANDATORY, OPTIONAL = 0, 1
REF_DATA = 1

TRANSIENT = 1 << 1
TOMBSTONE = 2 << 1
PINNED = 1 << 4


def booted(**kwargs):
    image = Image.arena(**kwargs)
    image.bootstrap()
    return image


# Root, Type, Field, uint32_t, uint64_t, Enumerator, the eight enumerations of the format with their enumerators, and the Field objects of Type and Field;
# plus the six Doc objects of the ground (Documentation, Intent, Norm, Constraint, Requirement, Origin) and their fifteen Field objects (docs slice 1).
GROUND = 120


def module(image, parent, name):
    return image.add(parent, name, kind=KIND_MODULE)


def data(image, parent, name, payload=b"", refs=(), **kwargs):
    return image.add(
        parent, name, kind=KIND_DATA, payload=payload, refs=list(refs), **kwargs
    )


def raises(status, call, *args, **kwargs):
    with pytest.raises(ArenaError) as caught:
        call(*args, **kwargs)
    assert str(caught.value) == status


# ---- bootstrap, names, paths ------------------------------------------------


def test_bootstrap_fixes_three_handles():
    image = booted()
    assert image.handle_count == GROUND
    assert image.get(0)["name"] == ""
    assert image.get(1)["path"] == "Type"
    assert image.get(2)["path"] == "Field"
    assert image.get(1)["type"] == 1 and image.get(2)["type"] == 1
    image.verify()


def test_paths_resolve_and_children_are_ordered():
    image = booted()
    pymergetic = module(image, 0, "pymergetic")
    rxf = module(image, pymergetic, "rxf")
    zeta = module(image, rxf, "zeta")
    alpha = module(image, rxf, "alpha")
    assert image.path(alpha) == "pymergetic.rxf.alpha"
    assert image.handle("pymergetic.rxf.zeta") == zeta
    assert image.handle("") == 0
    assert image.resolve("pymergetic.nothing") is None
    assert image.children(rxf) == [alpha, zeta]
    image.verify()


def test_name_rules():
    image = booted()
    module(image, 0, "a")
    raises("NameTaken", module, image, 0, "a")
    raises("BadName", module, image, 0, "")
    raises("BadName", module, image, 0, "a.b")
    raises("BadHandle", module, image, 999, "x")
    raises("BadName", image.add, None, "second_root")


def test_long_and_many_names():
    image = booted()
    long = module(image, 0, "n" * 10000)
    assert image.get(long)["name"] == "n" * 10000
    handles = [module(image, 0, f"module_{i:05d}") for i in range(2000)]
    assert image.get(handles[1999])["name"] == "module_01999"
    image.verify()


# ---- payload and references ---------------------------------------------------


def test_payload_and_refs_round_trip():
    image = booted()
    target = data(image, 0, "target")
    obj = data(
        image,
        0,
        "obj",
        payload=bytes(range(40)),
        refs=[(target, None, 7, REF_DATA, MANDATORY)],
    )
    info = image.get(obj)
    assert info["payload"] == bytes(range(40))
    assert info["refs"] == [(target, None, 7, REF_DATA, MANDATORY)]
    image.verify()


def test_resize_keeps_slot_and_content():
    image = booted()
    target = data(image, 0, "target")
    obj = data(
        image, 0, "obj", payload=b"abcd", refs=[(target, None, 1, REF_DATA, MANDATORY)]
    )
    before = image.frontier
    image.set_payload(obj, b"x" * 100)
    assert image.get(obj)["payload"] == b"x" * 100
    assert image.get(obj)["refs"] == [(target, None, 1, REF_DATA, MANDATORY)]
    assert image.frontier > before
    image.set_refs(
        obj,
        [(target, None, 1, REF_DATA, MANDATORY), (target, None, 2, REF_DATA, OPTIONAL)],
    )
    assert len(image.get(obj)["refs"]) == 2
    assert image.handle("obj") == obj
    image.verify()


# ---- slots, free chain, protection ------------------------------------------


def test_remove_frees_slot_and_reuses_it():
    image = booted()
    a = data(image, 0, "a")
    b = data(image, 0, "b")
    image.remove(a)
    assert not image.live(a)
    assert image.free_head == a
    image.verify()
    c = data(image, 0, "c")
    assert c == a
    assert image.free_head == NONE
    assert image.live(b)
    image.verify()


def test_free_chain_order():
    image = booted()
    hs = [data(image, 0, f"o{i}") for i in range(4)]
    image.remove(hs[1])
    image.remove(hs[3])
    assert image.free_head == hs[3]
    image.verify()
    assert data(image, 0, "x") == hs[3]
    assert data(image, 0, "y") == hs[1]
    image.verify()


def test_protected_and_children():
    image = booted()
    raises("Protected", image.remove, 0)
    raises("Protected", image.remove, 1)
    mod = module(image, 0, "m")
    module(image, mod, "child")
    raises("HasChildren", image.remove, mod)
    raises("BadHandle", image.remove, 12345)


# ---- index growth and limits --------------------------------------------------


def test_index_grows_by_chunks():
    image = booted()
    for i in range(9000):
        data(image, 0, f"d{i}")
    assert image.handle_count == 9000 + GROUND
    assert image.index_chunks == 3
    assert image.get(8000)["name"].startswith("d")
    image.verify()


def test_index_full_and_out_of_memory():
    image = Image.arena(index_capacity=4096)  # directory of 8 chunks: 32768 handles
    image.bootstrap()
    with pytest.raises(ArenaError) as caught:
        for i in range(40000):
            data(image, 0, f"d{i}")
    assert str(caught.value) == "IndexFull"
    image.verify()
    small = Image.arena(limit=64 * 1024)
    small.bootstrap()
    with pytest.raises(ArenaError) as caught:
        for i in range(10000):
            small.add(0, f"d{i}", payload=b"z" * 64)
    assert str(caught.value) == "OutOfMemory"
    small.verify()


# ---- serialization ------------------------------------------------------------


def test_bytes_round_trip_and_corruption():
    image = booted()
    mod = module(image, 0, "m")
    data(image, mod, "d", payload=b"hello")
    blob = image.to_bytes()
    assert len(blob) == image.body_size
    loaded = Image.from_bytes(blob)
    assert loaded.resolve("m.d") is not None
    assert loaded.get(loaded.handle("m.d"))["payload"] == b"hello"
    assert loaded.to_bytes() == blob
    loaded.verify()
    broken = bytearray(blob)
    broken[0] ^= 0xFF
    raises("BadImage", Image.from_bytes, bytes(broken))
    raises("BadImage", Image.from_bytes, blob[:100])


# ---- compaction ---------------------------------------------------------------


def build(order):
    """The same logical image; `order` shuffles the creation order."""
    image = booted()
    items = {
        "alpha": lambda: module(image, 0, "alpha"),
        "beta": lambda: module(image, 0, "beta"),
    }
    handles = {}
    for key in order:
        handles[key] = items[key]()
    for key in order:
        data(image, handles[key], "leaf", payload=key.encode())
    return image


def test_compaction_is_canonical_and_idempotent():
    one = build(["alpha", "beta"]).compact()
    two = build(["beta", "alpha"]).compact()
    assert one.to_bytes() == two.to_bytes()
    assert one.compact().to_bytes() == one.to_bytes()
    one.verify()
    paths = [one.path(h) for h in range(one.handle_count)]
    assert paths[:3] == ["", "Type", "Field"]
    # Then the path order, bytewise: the ground, our modules, the scalar types.
    rest = paths[3:]
    assert rest == sorted(rest)
    for expected in (
        "Field.count",
        "Type.form",
        "Enumerator.value",
        "NodeKind.Module",
        "alpha.leaf",
        "beta.leaf",
        "uint64_t",
    ):
        assert expected in rest


def test_compaction_drops_omitted_and_rewrites_references():
    image = booted()
    mod = module(image, 0, "m")
    junk = data(image, mod, "junk", payload=b"j" * 50)
    gone = data(image, mod, "gone")
    scratch = data(image, mod, "scratch", state=TRANSIENT)
    keeper = data(image, mod, "keeper", payload=b"k")
    data(
        image,
        mod,
        "holder",
        refs=[
            (keeper, None, 1, REF_DATA, MANDATORY),
            (scratch, None, 2, REF_DATA, OPTIONAL),
        ],
        state=PINNED,
        generation=7,
    )
    image.remove(junk)
    image.remove(gone)
    image.set_payload(keeper, b"k" * 30)
    compact = image.compact()
    compact.verify()
    assert compact.resolve("m.junk") is None
    assert compact.resolve("m.scratch") is None
    new_keeper = compact.handle("m.keeper")
    new_holder = compact.handle("m.holder")
    info = compact.get(new_holder)
    assert info["refs"] == [
        (new_keeper, None, 1, REF_DATA, MANDATORY)
    ]  # the optional one is gone
    assert info["generation"] == 7
    assert info["state"] == (1 | (PINNED))  # IMAGE, RETAIN, CLEAN, PINNED kept
    assert compact.get(new_keeper)["payload"] == b"k" * 30
    assert compact.handle_count == GROUND + 3  # the ground, m, keeper, holder
    assert compact.free_head == NONE
    assert compact.body_size < image.body_size


def test_compaction_refuses_dangling_mandatory_reference():
    image = booted()
    scratch = data(image, 0, "scratch", state=TRANSIENT)
    data(image, 0, "holder", refs=[(scratch, None, 1, REF_DATA, MANDATORY)])
    raises("Dangling", image.compact)


def test_compaction_refuses_retained_child_of_omitted_parent():
    image = booted()
    mod = module(image, 0, "m")
    module(image, mod, "kid")
    image.set_state(mod, TRANSIENT)
    raises("Dangling", image.compact)


def test_compaction_into_a_larger_index():
    image = Image.arena(index_capacity=4096)
    image.bootstrap()
    for i in range(100):
        data(image, 0, f"d{i}")
    bigger = image.compact(index_capacity=100000)
    bigger.verify()
    for i in range(40000):
        data(bigger, 0, f"e{i}")
    bigger.verify()
