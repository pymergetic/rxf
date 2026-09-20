#ifndef RXF_STDLIB_RUNTIME_H
#define RXF_STDLIB_RUNTIME_H

#include "basics.h"

#define RXF_RUNTIME_ABI_VERSION 2ULL
#define RXF_OBJECT_LIVE 1ULL
#define RXF_OBJECT_PENDING 2ULL
#define RXF_BORROW_MUTABLE (1ULL << 63)
#define RXF_REFUSE_STALE_GENERATION 12U
#define RXF_REFUSE_BORROW_CONFLICT 10U
#define RXF_REFUSE_PINNED 11U
#define RXF_REFUSE_OUT_OF_MEMORY 9U
#define RXF_REFUSE_UNBOUND 13U
#define RXF_REFUSE_TYPE_MISMATCH 14U
#define RXF_REFUSE_JOURNAL_FULL 15U

typedef struct { rxf_u64 id, generation, address; } rxf_native_slot;
typedef struct {
    rxf_u64 id, generation, type_id, payload, size, capacity, state, read_borrows, pin_count, owner_transaction;
} rxf_object_entry;
typedef struct {
    rxf_u64 identity, object_id, old_generation, old_type_id, old_payload;
    rxf_u64 old_size, old_capacity, old_state, old_owner_transaction;
} rxf_transaction_member;
typedef struct { rxf_u64 root_object_id, old_frontier, identity, member_count, active, reserved; } rxf_transaction;
typedef struct {
    rxf_u64 version, size;
    rxf_u64 function_count, functions;
    rxf_u64 object_count, objects;
    rxf_u64 capability_count, capabilities;
    rxf_u64 heap_base, heap_committed, heap_limit, heap_frontier;
    rxf_u64 journal_count, journal_capacity, journal;
    rxf_u64 cleanup_count, cleanup_capacity, cleanup;
} rxf_runtime_context;
typedef struct { rxf_u64 object_id, generation, offset; } rxf_object_handle;

typedef struct { rxf_u32 tag; rxf_u32 padding; rxf_u64 value; } rxf_option_u64;
typedef struct { rxf_u32 tag; rxf_u32 padding; rxf_object_handle value; } rxf_option_ref;
typedef struct { rxf_object_handle bucket_storage; rxf_u64 capacity, count, tombstone_count, generation; } rxf_hash_header;
typedef struct { rxf_u32 state, padding; rxf_u64 stored_hash, key; rxf_object_handle value; } rxf_hash_map_bucket;
typedef struct { rxf_u32 state, padding; rxf_u64 stored_hash, key; } rxf_hash_set_bucket;

_Static_assert(sizeof(rxf_native_slot)==24,"native slot ABI");
_Static_assert(sizeof(rxf_object_entry)==80,"object entry ABI");
_Static_assert(sizeof(rxf_transaction_member)==72,"transaction member ABI");
_Static_assert(sizeof(rxf_transaction)==48,"transaction ABI");
_Static_assert(sizeof(rxf_runtime_context)==144,"runtime context ABI");
_Static_assert(sizeof(rxf_option_u64)==16,"option u64 ABI");
_Static_assert(sizeof(rxf_option_ref)==32,"option ref ABI");
_Static_assert(sizeof(rxf_hash_header)==56,"hash header ABI");
_Static_assert(sizeof(rxf_hash_map_bucket)==48,"map bucket ABI");
_Static_assert(sizeof(rxf_hash_set_bucket)==24,"set bucket ABI");
_Static_assert(__builtin_offsetof(rxf_object_entry,type_id)==16,"object type offset");
_Static_assert(__builtin_offsetof(rxf_object_entry,owner_transaction)==72,"owner transaction offset");
_Static_assert(__builtin_offsetof(rxf_runtime_context,heap_base)==64,"heap base offset");
_Static_assert(__builtin_offsetof(rxf_runtime_context,heap_frontier)==88,"frontier offset");

rxf_u32 rxf_hash_u64(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_runtime_hash_map_lookup(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64, rxf_option_ref *);
rxf_u32 rxf_runtime_hash_set_lookup(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64, rxf_option_u64 *);
rxf_u32 rxf_runtime_begin_private(rxf_runtime_context *, rxf_object_handle, rxf_u64, rxf_transaction *);
rxf_u32 rxf_runtime_resolve(rxf_runtime_context *, rxf_object_handle, rxf_u64, rxf_u64 *);
rxf_u32 rxf_runtime_resolve_typed(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_runtime_allocate_prepare(rxf_runtime_context *, rxf_u64, rxf_u64, rxf_u64, rxf_u64, rxf_u64, rxf_transaction *, rxf_object_handle *);
rxf_u32 rxf_runtime_publish(rxf_runtime_context *, rxf_transaction *);
rxf_u32 rxf_runtime_rollback(rxf_runtime_context *, rxf_transaction *);
rxf_u32 rxf_runtime_release(rxf_runtime_context *, rxf_object_handle);
rxf_u32 rxf_runtime_borrow(rxf_runtime_context *, rxf_object_handle, rxf_u64);
rxf_u32 rxf_runtime_release_borrow(rxf_runtime_context *, rxf_object_handle, rxf_u64);
rxf_u32 rxf_runtime_pin(rxf_runtime_context *, rxf_object_handle);
rxf_u32 rxf_runtime_unpin(rxf_runtime_context *, rxf_object_handle);
rxf_u32 rxf_runtime_read(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_runtime_write(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64, rxf_u64);
rxf_u32 rxf_runtime_reserve(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64);
rxf_u32 rxf_runtime_append(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u64, rxf_u64);
rxf_u32 rxf_runtime_format_u32(rxf_runtime_context *, rxf_object_handle, rxf_transaction *, rxf_u32);
rxf_u32 rxf_runtime_search(rxf_runtime_context *, rxf_object_handle, rxf_u64, rxf_object_handle, rxf_u64, rxf_transaction *, rxf_u64 *);
rxf_u32 rxf_runtime_cleanup(rxf_runtime_context *, rxf_transaction *);

#endif
