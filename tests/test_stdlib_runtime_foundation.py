import ctypes
import mmap

from pymergetic.rxf.generated_stdlib_native import STDLIB_AARCH64, STDLIB_X86_64
from pymergetic.rxf.stdlib_native_runtime import *


def _fn(name, *args):
    raw = STDLIB_X86_64[name]
    mem = mmap.mmap(
        -1, len(raw), prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC
    )
    mem.write(raw)
    fn = ctypes.CFUNCTYPE(ctypes.c_uint32, *args)(
        ctypes.addressof(ctypes.c_char.from_buffer(mem))
    )
    fn.mem = mem  # pyright: ignore[reportAttributeAccessIssue]
    return fn


def test_runtime_abi_layout_is_architecture_stable():
    assert (
        ctypes.sizeof(NativeSlot) == 24
        and ctypes.sizeof(NativeObjectEntry) == 80
        and ctypes.sizeof(NativeTransactionMember) == 72
        and ctypes.sizeof(NativeTransaction) == 48
        and ctypes.sizeof(NativeRuntimeContext) == 144
    )
    assert (
        RUNTIME_CONTEXT_OFFSETS["heap_base"] == 64
        and RUNTIME_CONTEXT_OFFSETS["heap_frontier"] == 88
    )
    assert set(STDLIB_X86_64) == set(STDLIB_AARCH64) and len(STDLIB_X86_64) == 30


def test_exact_x86_runtime_prepare_publish_resolve_and_stale_refusal():
    heap = (ctypes.c_uint8 * 64)()
    entries = (NativeObjectEntry * 1)()
    journal = (NativeTransactionMember * 2)()
    entries[0].id = 7
    ctx = NativeRuntimeContext(
        2,
        144,
        0,
        0,
        1,
        ctypes.addressof(entries),
        0,
        0,
        ctypes.addressof(heap),
        64,
        64,
        0,
        0,
        2,
        ctypes.addressof(journal),
        0,
        0,
        0,
    )
    tx = NativeTransaction()
    handle = NativeObjectHandle()
    prepare = _fn(
        "runtime_allocate_prepare",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
        ctypes.POINTER(NativeObjectHandle),
    )
    begin = _fn(
        "runtime_begin_private",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
    )
    assert (
        begin(ctypes.byref(ctx), NativeObjectHandle(7, 0, 0), 1007, ctypes.byref(tx))
        == 0
    )
    assert (
        prepare(
            ctypes.byref(ctx), 7, 101, 16, 16, 8, ctypes.byref(tx), ctypes.byref(handle)
        )
        == 0
        and ctx.heap_frontier == 16
        and entries[0].state == 2
    )
    publish = _fn(
        "runtime_publish",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.POINTER(NativeTransaction),
    )
    assert publish(ctypes.byref(ctx), ctypes.byref(tx)) == 0 and entries[0].state == 1
    resolve = _fn(
        "runtime_resolve",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )
    out = ctypes.c_uint64(99)
    assert resolve(
        ctypes.byref(ctx), handle, 8, ctypes.byref(out)
    ) == 0 and out.value == ctypes.addressof(heap)
    handle.generation -= 1
    before = out.value
    assert (
        resolve(ctypes.byref(ctx), handle, 8, ctypes.byref(out)) == 12
        and out.value == before
    )


def test_slot_rebinding_is_generation_checked():
    d = RuntimeDirectory()
    a = d.bind(40, 0x1000)
    b = d.bind(40, 0x2000)
    assert b.generation == a.generation + 1 and d.resolve(40, b.generation) == 0x2000
    try:
        d.resolve(40, a.generation)
    except LookupError:
        pass
    else:
        assert False


def test_exact_x86_pending_receipt_mutation_requires_owner_transaction():
    heap = (ctypes.c_uint8 * 64)()
    entries = (NativeObjectEntry * 1)()
    journal = (NativeTransactionMember * 2)()
    entries[0].id = 7
    ctx = NativeRuntimeContext(
        2,
        144,
        0,
        0,
        1,
        ctypes.addressof(entries),
        0,
        0,
        ctypes.addressof(heap),
        64,
        64,
        0,
        0,
        2,
        ctypes.addressof(journal),
        0,
        0,
        0,
    )
    tx, wrong = NativeTransaction(), NativeTransaction()
    handle = NativeObjectHandle()
    prepare = _fn(
        "runtime_allocate_prepare",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
        ctypes.POINTER(NativeObjectHandle),
    )
    append = _fn(
        "runtime_append",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.POINTER(NativeTransaction),
        ctypes.c_uint64,
        ctypes.c_uint64,
    )
    format_u32 = _fn(
        "runtime_format_u32",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.POINTER(NativeTransaction),
        ctypes.c_uint32,
    )
    publish = _fn(
        "runtime_publish",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.POINTER(NativeTransaction),
    )
    prefix, newline = (
        ctypes.create_string_buffer(b"Receipt total: "),
        ctypes.create_string_buffer(b"\n"),
    )
    begin = _fn(
        "runtime_begin_private",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
    )
    assert (
        begin(ctypes.byref(ctx), NativeObjectHandle(7, 0, 0), 1007, ctypes.byref(tx))
        == 0
    )
    assert (
        prepare(
            ctypes.byref(ctx), 7, 101, 0, 64, 8, ctypes.byref(tx), ctypes.byref(handle)
        )
        == 0
    )
    snapshot = (entries[0].size, bytes(heap))
    assert append(ctypes.byref(ctx), handle, None, ctypes.addressof(prefix), 15) == 13
    wrong.root_object_id, wrong.identity, wrong.active = 7, tx.identity + 1, 1
    assert (
        append(
            ctypes.byref(ctx), handle, ctypes.byref(wrong), ctypes.addressof(prefix), 15
        )
        == 13
    )
    assert (entries[0].size, bytes(heap)) == snapshot
    assert (
        append(
            ctypes.byref(ctx), handle, ctypes.byref(tx), ctypes.addressof(prefix), 15
        )
        == 0
    )
    assert format_u32(ctypes.byref(ctx), handle, ctypes.byref(tx), 570) == 0
    assert (
        append(
            ctypes.byref(ctx), handle, ctypes.byref(tx), ctypes.addressof(newline), 1
        )
        == 0
    )
    assert entries[0].size == 19 and bytes(heap[:19]) == b"Receipt total: 570\n"
    assert publish(ctypes.byref(ctx), ctypes.byref(tx)) == 0
    assert entries[0].state == 1 and entries[0].owner_transaction == 0


def test_exact_x86_transaction_mismatch_rollback_is_atomic():
    heap = (ctypes.c_uint8 * 32)()
    entries = (NativeObjectEntry * 1)()
    journal = (NativeTransactionMember * 2)()
    entries[0].id = 9
    ctx = NativeRuntimeContext(
        2,
        144,
        0,
        0,
        1,
        ctypes.addressof(entries),
        0,
        0,
        ctypes.addressof(heap),
        32,
        32,
        0,
        0,
        2,
        ctypes.addressof(journal),
        0,
        0,
        0,
    )
    tx, wrong, handle = NativeTransaction(), NativeTransaction(), NativeObjectHandle()
    prepare = _fn(
        "runtime_allocate_prepare",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
        ctypes.POINTER(NativeObjectHandle),
    )
    rollback = _fn(
        "runtime_rollback",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.POINTER(NativeTransaction),
    )
    begin = _fn(
        "runtime_begin_private",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
    )
    assert (
        begin(ctypes.byref(ctx), NativeObjectHandle(9, 0, 0), 1009, ctypes.byref(tx))
        == 0
    )
    assert (
        prepare(
            ctypes.byref(ctx), 9, 101, 0, 16, 8, ctypes.byref(tx), ctypes.byref(handle)
        )
        == 0
    )
    wrong.root_object_id, wrong.identity, wrong.active = 9, tx.identity + 1, 1
    before = (ctx.heap_frontier, entries[0].payload, entries[0].state)
    assert rollback(ctypes.byref(ctx), ctypes.byref(wrong)) == 13
    assert (ctx.heap_frontier, entries[0].payload, entries[0].state) == before
    assert rollback(ctypes.byref(ctx), ctypes.byref(tx)) == 0
    assert ctx.heap_frontier == 0 and entries[0].state == 0 and entries[0].payload == 0


def test_exact_x86_typed_multi_object_journal_and_refusals():
    heap = (ctypes.c_uint8 * 64)()
    entries = (NativeObjectEntry * 3)()
    journal = (NativeTransactionMember * 2)()
    for index, object_id in enumerate((11, 12, 13)):
        entries[index].id = object_id
    ctx = NativeRuntimeContext(
        2,
        144,
        0,
        0,
        3,
        ctypes.addressof(entries),
        0,
        0,
        ctypes.addressof(heap),
        64,
        64,
        0,
        0,
        2,
        ctypes.addressof(journal),
        0,
        0,
        0,
    )
    tx, wrong = NativeTransaction(), NativeTransaction()
    begin = _fn(
        "runtime_begin_private",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
    )
    prepare = _fn(
        "runtime_allocate_prepare",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeTransaction),
        ctypes.POINTER(NativeObjectHandle),
    )
    typed = _fn(
        "runtime_resolve_typed",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.POINTER(NativeTransaction),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint64),
    )
    rollback = _fn(
        "runtime_rollback",
        ctypes.POINTER(NativeRuntimeContext),
        ctypes.POINTER(NativeTransaction),
    )
    assert (
        begin(ctypes.byref(ctx), NativeObjectHandle(11, 0, 0), 991, ctypes.byref(tx))
        == 0
    )
    h1, h2, h3 = NativeObjectHandle(), NativeObjectHandle(), NativeObjectHandle()
    assert (
        prepare(
            ctypes.byref(ctx), 11, 60017, 8, 8, 8, ctypes.byref(tx), ctypes.byref(h1)
        )
        == 0
    )
    assert (
        prepare(
            ctypes.byref(ctx), 12, 60018, 8, 8, 8, ctypes.byref(tx), ctypes.byref(h2)
        )
        == 0
    )
    assert (
        prepare(
            ctypes.byref(ctx), 13, 60019, 8, 8, 8, ctypes.byref(tx), ctypes.byref(h3)
        )
        == 15
    )
    assert tx.member_count == 2 and ctx.journal_count == 2
    out = ctypes.c_uint64(0xBAD)
    assert (
        typed(ctypes.byref(ctx), h1, ctypes.byref(tx), 60017, 8, ctypes.byref(out)) == 0
    )
    assert out.value == ctypes.addressof(heap)
    before = out.value
    assert (
        typed(ctypes.byref(ctx), h1, ctypes.byref(tx), 60018, 8, ctypes.byref(out))
        == 14
        and out.value == before
    )
    stale = NativeObjectHandle(h1.object_id, h1.generation - 1, 0)
    assert (
        typed(ctypes.byref(ctx), stale, ctypes.byref(tx), 60017, 8, ctypes.byref(out))
        == 12
        and out.value == before
    )
    wrong.identity, wrong.active = tx.identity + 1, 1
    assert (
        typed(ctypes.byref(ctx), h1, ctypes.byref(wrong), 60017, 8, ctypes.byref(out))
        == 13
        and out.value == before
    )
    journal[0].identity = 0
    assert (
        typed(ctypes.byref(ctx), h1, ctypes.byref(tx), 60017, 8, ctypes.byref(out))
        == 13
        and out.value == before
    )
    journal[0].identity = tx.identity
    assert rollback(ctypes.byref(ctx), ctypes.byref(tx)) == 0
    assert (
        not tx.active
        and ctx.journal_count == 0
        and all(entry.state == 0 for entry in entries)
    )
    assert rollback(ctypes.byref(ctx), ctypes.byref(tx)) == 13


def test_exact_x86_hash_u64_vectors_and_typed_lookup_pipeline():
    def reference(key: int, seed: int = 14695981039346656037) -> int:
        value = seed
        for byte in key.to_bytes(8, "little"):
            value = ((value ^ byte) * 1099511628211) & ((1 << 64) - 1)
        return value

    hash_u64 = _fn(
        "hash_u64", ctypes.c_uint64, ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64)
    )
    for key in (0, 1, 0x0102030405060708, (1 << 64) - 1):
        out = ctypes.c_uint64(0xBAD)
        assert hash_u64(key, 14695981039346656037, ctypes.byref(out)) == 0
        assert out.value == reference(key)

    map_header, set_header = NativeHashHeader(), NativeHashHeader()
    map_buckets = (NativeHashMapBucket * 8)()
    set_buckets = (NativeHashSetBucket * 8)()
    entries = (NativeObjectEntry * 4)()
    handles = [NativeObjectHandle(20 + index, 1, 0) for index in range(4)]
    for entry, handle, type_id, payload, size in zip(
        entries,
        handles,
        (60017, 41018, 60018, 41019),
        (
            ctypes.addressof(map_header),
            ctypes.addressof(map_buckets),
            ctypes.addressof(set_header),
            ctypes.addressof(set_buckets),
        ),
        (56, ctypes.sizeof(map_buckets), 56, ctypes.sizeof(set_buckets)),
        strict=True,
    ):
        (
            entry.id,
            entry.generation,
            entry.type_id,
            entry.payload,
            entry.size,
            entry.capacity,
            entry.state,
        ) = handle.object_id, 1, type_id, payload, size, size, 1
    map_header.bucket_storage, map_header.capacity = handles[1], 8
    set_header.bucket_storage, set_header.capacity = handles[3], 8
    ctx = NativeRuntimeContext(
        2, 144, 0, 0, 4, ctypes.addressof(entries), 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
    )
    map_lookup = _fn(
        "runtime_hash_map_lookup",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.POINTER(NativeTransaction),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeOptionRef),
    )
    set_lookup = _fn(
        "runtime_hash_set_lookup",
        ctypes.POINTER(NativeRuntimeContext),
        NativeObjectHandle,
        ctypes.POINTER(NativeTransaction),
        ctypes.c_uint64,
        ctypes.c_uint64,
        ctypes.POINTER(NativeOptionU64),
    )
    key, hashed = 55, reference(55)
    map_out, set_out = NativeOptionRef(), NativeOptionU64()
    assert (
        map_lookup(
            ctypes.byref(ctx), handles[0], None, hashed, key, ctypes.byref(map_out)
        )
        == 0
        and map_out.tag == 0
    )
    slot = hashed & 7
    (
        map_buckets[slot].state,
        map_buckets[slot].stored_hash,
        map_buckets[slot].key,
        map_buckets[slot].value,
    ) = 2, hashed, 1, NativeObjectHandle(99, 1, 0)
    (
        map_buckets[(slot + 1) & 7].state,
        map_buckets[(slot + 1) & 7].stored_hash,
        map_buckets[(slot + 1) & 7].key,
        map_buckets[(slot + 1) & 7].value,
    ) = 1, hashed, key, NativeObjectHandle(77, 3, 4)
    map_header.count, map_header.tombstone_count = 1, 1
    assert map_lookup(
        ctypes.byref(ctx), handles[0], None, hashed, key, ctypes.byref(map_out)
    ) == 0 and (map_out.tag, map_out.value.object_id) == (1, 77)
    for bucket in set_buckets:
        bucket.state, bucket.stored_hash, bucket.key = 2, hashed, 1
    (
        set_buckets[(slot + 3) & 7].state,
        set_buckets[(slot + 3) & 7].stored_hash,
        set_buckets[(slot + 3) & 7].key,
    ) = 1, hashed, key
    set_header.count, set_header.tombstone_count = 1, 7
    assert set_lookup(
        ctypes.byref(ctx), handles[2], None, hashed, key, ctypes.byref(set_out)
    ) == 0 and (set_out.tag, set_out.value) == (1, key)
    set_buckets[(slot + 3) & 7].key = key + 1
    assert (
        set_lookup(
            ctypes.byref(ctx), handles[2], None, hashed, key, ctypes.byref(set_out)
        )
        == 0
        and set_out.tag == 0
    )
    set_out.tag, set_out.value = 9, 0xBAD
    entries[2].type_id = 60017
    assert set_lookup(
        ctypes.byref(ctx), handles[2], None, hashed, key, ctypes.byref(set_out)
    ) == 14 and (set_out.tag, set_out.value) == (9, 0xBAD)
