//! Raw bindings of `rxfb.h`: the slim, freestanding C reader of an RXFB v7 image.
//!
//! Everything here is a declaration of the C core that `build.rs` compiles into the crate; no
//! reader logic lives in Rust. The structs are `rxfb_layout.h` under Rust names, pinned below by
//! `const` assertions that repeat the header's `_Static_assert`s (size, alignment and every field
//! offset), so a drift between the header and these declarations fails the build, not a read.
//!
//! Names: the C `rxfb_foo` types are `Foo` here (`rxfb` itself is [`Rxfb`]); functions and
//! constants keep their C names. Safe use is the `rxfb` crate.
#![no_std]

use core::ffi::{c_char, c_uint};
use core::mem::{align_of, offset_of, size_of};

/// `rxfb_status`: the answer of every call (`RXFB_OK` .. `RXFB_NOT_FOUND`).
pub type Status = c_uint;
/// The call succeeded.
pub const RXFB_OK: Status = 0;
/// The bytes are not a usable v7 image (or a chain/offset is corrupt).
pub const RXFB_BAD_IMAGE: Status = 1;
/// Stale, out of range, or `RXFB_HANDLE_NONE`.
pub const RXFB_BAD_HANDLE: Status = 2;
/// An empty name or path segment.
pub const RXFB_BAD_NAME: Status = 3;
/// The object index itself: its payload is a table, not a chain.
pub const RXFB_PROTECTED: Status = 4;
/// The caller's buffer is too small; `*written` is what it must offer.
pub const RXFB_NO_ROOM: Status = 5;
/// A lookup that names nothing.
pub const RXFB_NOT_FOUND: Status = 6;

/// `rxfb_handle`: `(generation << 20) | index`.
pub type Handle = u32;
/// No handle.
pub const RXFB_HANDLE_NONE: Handle = 0xFFFF_FFFF;
/// Bits of a handle that carry the index.
pub const RXFB_HANDLE_INDEX_BITS: u32 = 20;
/// Bits of a handle that carry the generation.
pub const RXFB_HANDLE_GEN_BITS: u32 = 12;
/// Mask of the index bits.
pub const RXFB_HANDLE_INDEX_MASK: u32 = (1 << RXFB_HANDLE_INDEX_BITS) - 1;
/// Mask of the generation bits (after the shift).
pub const RXFB_HANDLE_GEN_MASK: u32 = (1 << RXFB_HANDLE_GEN_BITS) - 1;

/// `rxfb_handle_pack`.
#[must_use]
pub const fn rxfb_handle_pack(index: u32, gen_: u32) -> Handle {
    ((gen_ & RXFB_HANDLE_GEN_MASK) << RXFB_HANDLE_INDEX_BITS) | (index & RXFB_HANDLE_INDEX_MASK)
}

/// `rxfb_handle_index`.
#[must_use]
pub const fn rxfb_handle_index(handle: Handle) -> u32 {
    handle & RXFB_HANDLE_INDEX_MASK
}

/// `rxfb_handle_gen`.
#[must_use]
pub const fn rxfb_handle_gen(handle: Handle) -> u32 {
    (handle >> RXFB_HANDLE_INDEX_BITS) & RXFB_HANDLE_GEN_MASK
}

/// Size of the superblock: one page at byte 0.
pub const RXFB_SUPER_SIZE: usize = 4096;
/// The superblock version this reader accepts.
pub const RXFB_SUPER_VERSION: u32 = 1;

/// `rxfb_superblock`: 4 KiB at byte 0, the only thing in the image that is not an object.
#[repr(C, align(8))]
pub struct Superblock {
    /// `"RXFA"`.
    pub magic: [u8; 4],
    /// [`RXFB_SUPER_VERSION`].
    pub version: u32,
    /// First free offset: the end of the objects.
    pub committed: u64,
    /// End of the claimed region.
    pub limit: u64,
    /// Root handle: the image itself.
    pub root: u32,
    /// Entry handle: the first run.
    pub entry: u32,
    /// Zero.
    pub reserved: [u8; RXFB_SUPER_SIZE - 32],
}

/// Object kinds (`RXFB_OBJ_*`).
pub const RXFB_OBJ_MODULE: u16 = 1;
/// A Code object: the bytes of a function for one machine.
pub const RXFB_OBJ_FUNC: u16 = 2;
/// A hash.
pub const RXFB_OBJ_HASH: u16 = 3;
/// A table of rows: `sys/specs`, one relation table.
pub const RXFB_OBJ_SPEC: u16 = 4;
/// The interned symbol table.
pub const RXFB_OBJ_SYM: u16 = 5;
/// Opaque bytes: a CodeBlob.
pub const RXFB_OBJ_BLOB: u16 = 6;
/// A frame.
pub const RXFB_OBJ_FRAME: u16 = 7;
/// A journal.
pub const RXFB_OBJ_JOURNAL: u16 = 8;
/// A freed record.
pub const RXFB_OBJ_FREE: u16 = 0xFFFF;

/// Object flags (`RXFB_OFLAG_*`).
pub const RXFB_OFLAG_RO: u16 = 0x1;
/// The root object.
pub const RXFB_OFLAG_ROOT: u16 = 0x2;
/// Modified since the last commit.
pub const RXFB_OFLAG_DIRTY: u16 = 0x4;
/// Written through the journal.
pub const RXFB_OFLAG_JOURNALED: u16 = 0x8;

/// `rxfb_object`: a 24-byte header, then the payload inline in 8-byte slots.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct Object {
    /// `RXFB_OBJ_*`.
    pub kind: u16,
    /// Generation (stale-handle detection).
    pub gen_: u16,
    /// `RXFB_OFLAG_*`.
    pub flags: u16,
    /// Symbol id (interned, 0 = anonymous).
    pub name: u16,
    /// Handle of the parent object.
    pub parent: u32,
    /// Next sibling under the parent.
    pub link: u32,
    /// Reference count.
    pub refs: u32,
    /// The payload room in bytes (a whole number of 8-byte slots).
    pub payload: u32,
}

/// `rxfb_index_entry`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct IndexEntry {
    /// Kind of the object.
    pub kind: u16,
    /// Generation of the object.
    pub gen_: u16,
    /// Zero.
    pub reserved: u32,
    /// The offset of the record in the image; 0 = no object.
    pub slot: u64,
}

/// `rxfb_index_head`: the first words of the object index.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct IndexHead {
    /// Entries published: the next free index.
    pub count: u32,
    /// Entries this table holds.
    pub capacity: u32,
}

/// `rxfb_module`: a Module's payload opens with the head of the children chain.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct Module {
    /// Handle of the first child, [`RXFB_HANDLE_NONE`] = none.
    pub children: u32,
    /// Number of children.
    pub count: u32,
}

/// `rxfb_syms`: the head of the symbol table.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct Syms {
    /// The next id to hand out.
    pub count: u32,
    /// String offsets this table holds.
    pub capacity: u32,
}

/// `rxfb_spec_row`: the semantic face of an object, one row per index.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct SpecRow {
    /// Handle of the Type object, [`RXFB_HANDLE_NONE`] = untyped.
    pub type_: u32,
    /// The world's flags word: kind | state | owner.
    pub flags: u32,
    /// Position among the ordered siblings; 0 = the order is the name.
    pub index: u32,
    /// Zero.
    pub reserved: u32,
    /// The world's generation of the object.
    pub generation: u64,
}

/// Shift of the node kind in a spec row's flags word.
pub const RXFB_FLAG_KIND_SHIFT: u32 = 0;
/// Mask of the node kind.
pub const RXFB_FLAG_KIND_MASK: u32 = 0xFF;
/// Shift of the state.
pub const RXFB_FLAG_STATE_SHIFT: u32 = 8;
/// Mask of the state.
pub const RXFB_FLAG_STATE_MASK: u32 = 0x3F;
/// Shift of the owner.
pub const RXFB_FLAG_OWNER_SHIFT: u32 = 14;
/// Mask of the owner.
pub const RXFB_FLAG_OWNER_MASK: u32 = 0xF;

/// `rxfb_ref`: one reference row of `sys/relations`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(C, align(8))]
pub struct Ref {
    /// A handle, zero-extended.
    pub target: u64,
    /// A handle, zero-extended; [`RXFB_REF_NONE`] = no role.
    pub role: u64,
    /// `RXFB_REF_*`.
    pub kind: u16,
    /// `RXFB_BINDING_*`.
    pub binding: u16,
    /// The argument or leaf number.
    pub index: u32,
}

/// The 64-bit none of a reference's role.
pub const RXFB_REF_NONE: u64 = 0xFFFF_FFFF_FFFF_FFFF;
/// Reference kinds (`RXFB_REF_*`).
pub const RXFB_REF_CALL: u16 = 0;
/// A data reference.
pub const RXFB_REF_DATA: u16 = 1;
/// An entry reference.
pub const RXFB_REF_ENTRY: u16 = 2;
/// A type reference.
pub const RXFB_REF_TYPE: u16 = 3;
/// An import reference.
pub const RXFB_REF_IMPORT: u16 = 4;
/// An implementation reference.
pub const RXFB_REF_IMPLEMENTATION: u16 = 5;
/// An owner reference.
pub const RXFB_REF_OWNER: u16 = 6;
/// A doc reference.
pub const RXFB_REF_DOC: u16 = 7;
/// The reference must resolve.
pub const RXFB_BINDING_MANDATORY: u16 = 0;
/// The reference may be absent.
pub const RXFB_BINDING_OPTIONAL: u16 = 1;

/// Code payload: where the function starts inside the blob's bytes (u64).
pub const RXFB_CODE_ENTRY_OFFSET: usize = 40;
/// Code payload: the length of the code (u64).
pub const RXFB_CODE_BYTE_COUNT: usize = 48;
/// Code payload: the handle of the CodeBlob (u64).
pub const RXFB_CODE_BODY: usize = 96;
/// The Code payload this reader needs.
pub const RXFB_CODE_SIZE: usize = 104;
/// CodeBlob payload: the number of bytes (u64).
pub const RXFB_BLOB_BYTE_COUNT: usize = 0;
/// CodeBlob payload: where the bytes start.
pub const RXFB_BLOB_DATA: usize = 40;

/// `rxfb`: an attached image. Plain data; the fields are the library's cache of where the anchors
/// are, not an interface.
#[derive(Clone, Copy)]
#[repr(C)]
pub struct Rxfb {
    /// First byte of the mapped image.
    pub base: *const u8,
    /// Length of the image in bytes.
    pub size: u64,
    /// The superblock.
    pub super_: *const Superblock,
    /// The object index.
    pub index: *const IndexHead,
    /// `sys/specs`, or [`RXFB_HANDLE_NONE`].
    pub specs: Handle,
    /// `sys/relations`, or [`RXFB_HANDLE_NONE`].
    pub relations: Handle,
}

impl Rxfb {
    /// A detached value to be filled by `rxfb_attach`. It is not usable before that.
    #[must_use]
    pub const fn detached() -> Self {
        Self {
            base: core::ptr::null(),
            size: 0,
            super_: core::ptr::null(),
            index: core::ptr::null(),
            specs: RXFB_HANDLE_NONE,
            relations: RXFB_HANDLE_NONE,
        }
    }
}

/// `rxfb_code`: a function's bytes for one machine.
#[derive(Clone, Copy)]
#[repr(C)]
pub struct RxfbCode {
    /// Where the function starts: points into the image.
    pub entry: *const u8,
    /// How long it is.
    pub length: u64,
    /// The Code object it came from.
    pub code: Handle,
    /// The CodeBlob object it came from.
    pub blob: Handle,
}

impl RxfbCode {
    /// An empty value to be filled by `rxfb_code_of` and its kin.
    #[must_use]
    pub const fn empty() -> Self {
        Self {
            entry: core::ptr::null(),
            length: 0,
            code: RXFB_HANDLE_NONE,
            blob: RXFB_HANDLE_NONE,
        }
    }
}

/// The interpreter whose Leaf references are the function slots of a linked image.
pub const RXFB_RUN_PATH: &str = "pymergetic.rxf.foundation.graph.run";

// Pins: the same sizes and offsets as the header's _Static_asserts (rxfb_layout.h, 64-bit).
const _: () = {
    assert!(size_of::<Superblock>() == RXFB_SUPER_SIZE);
    assert!(align_of::<Superblock>() == 8);
    assert!(offset_of!(Superblock, magic) == 0);
    assert!(offset_of!(Superblock, version) == 4);
    assert!(offset_of!(Superblock, committed) == 8);
    assert!(offset_of!(Superblock, limit) == 16);
    assert!(offset_of!(Superblock, root) == 24);
    assert!(offset_of!(Superblock, entry) == 28);
    assert!(offset_of!(Superblock, reserved) == 32);

    assert!(size_of::<Object>() == 24);
    assert!(align_of::<Object>() == 8);
    assert!(offset_of!(Object, kind) == 0);
    assert!(offset_of!(Object, gen_) == 2);
    assert!(offset_of!(Object, flags) == 4);
    assert!(offset_of!(Object, name) == 6);
    assert!(offset_of!(Object, parent) == 8);
    assert!(offset_of!(Object, link) == 12);
    assert!(offset_of!(Object, refs) == 16);
    assert!(offset_of!(Object, payload) == 20);

    assert!(size_of::<IndexEntry>() == 16);
    assert!(offset_of!(IndexEntry, kind) == 0);
    assert!(offset_of!(IndexEntry, gen_) == 2);
    assert!(offset_of!(IndexEntry, slot) == 8);
    assert!(size_of::<IndexHead>() == 8);
    assert!(offset_of!(IndexHead, count) == 0);
    assert!(offset_of!(IndexHead, capacity) == 4);

    assert!(size_of::<Module>() == 8);
    assert!(offset_of!(Module, children) == 0);
    assert!(offset_of!(Module, count) == 4);
    assert!(size_of::<Syms>() == 8);
    assert!(offset_of!(Syms, count) == 0);
    assert!(offset_of!(Syms, capacity) == 4);

    assert!(size_of::<SpecRow>() == 24);
    assert!(offset_of!(SpecRow, type_) == 0);
    assert!(offset_of!(SpecRow, flags) == 4);
    assert!(offset_of!(SpecRow, index) == 8);
    assert!(offset_of!(SpecRow, reserved) == 12);
    assert!(offset_of!(SpecRow, generation) == 16);

    assert!(size_of::<Ref>() == 24);
    assert!(offset_of!(Ref, target) == 0);
    assert!(offset_of!(Ref, role) == 8);
    assert!(offset_of!(Ref, kind) == 16);
    assert!(offset_of!(Ref, binding) == 18);
    assert!(offset_of!(Ref, index) == 20);
};

// On a 64-bit target the structs the C core hands around have the layout of `rxfb.h` (the pointer
// fields make `Rxfb` and `RxfbCode` target-width dependent; the pins above are width-free).
#[cfg(target_pointer_width = "64")]
const _: () = {
    assert!(size_of::<Rxfb>() == 40);
    assert!(offset_of!(Rxfb, base) == 0);
    assert!(offset_of!(Rxfb, size) == 8);
    assert!(offset_of!(Rxfb, super_) == 16);
    assert!(offset_of!(Rxfb, index) == 24);
    assert!(offset_of!(Rxfb, specs) == 32);
    assert!(offset_of!(Rxfb, relations) == 36);
    assert!(size_of::<RxfbCode>() == 24);
    assert!(offset_of!(RxfbCode, entry) == 0);
    assert!(offset_of!(RxfbCode, length) == 8);
    assert!(offset_of!(RxfbCode, code) == 16);
    assert!(offset_of!(RxfbCode, blob) == 20);
};

unsafe extern "C" {
    /// Validates the superblock and the object index and fills `rx`; reads nothing else.
    pub fn rxfb_attach(rx: *mut Rxfb, bytes: *const u8, length: u64) -> Status;
    /// The root handle.
    pub fn rxfb_root(rx: *const Rxfb) -> Handle;
    /// The entry handle, [`RXFB_HANDLE_NONE`] when the image has none.
    pub fn rxfb_entry(rx: *const Rxfb) -> Handle;
    /// The handles (index slots) the image holds.
    pub fn rxfb_count(rx: *const Rxfb) -> u32;

    /// The record a handle names.
    pub fn rxfb_record(rx: *const Rxfb, handle: Handle, record: *mut *const Object) -> Status;
    /// The payload of an object.
    pub fn rxfb_payload(
        rx: *const Rxfb,
        handle: Handle,
        bytes: *mut *const u8,
        size: *mut u32,
    ) -> Status;
    /// The text of an interned name (NUL-terminated inside the image).
    pub fn rxfb_symbol(
        rx: *const Rxfb,
        id: u32,
        text: *mut *const c_char,
        length: *mut u32,
    ) -> Status;
    /// The name of an object.
    pub fn rxfb_name(
        rx: *const Rxfb,
        handle: Handle,
        text: *mut *const c_char,
        length: *mut u32,
    ) -> Status;
    /// The semantic row of an object.
    pub fn rxfb_spec(rx: *const Rxfb, handle: Handle, row: *mut *const SpecRow) -> Status;

    /// The child of `parent` with this name.
    pub fn rxfb_find(
        rx: *const Rxfb,
        parent: Handle,
        name: *const c_char,
        length: u32,
        handle: *mut Handle,
    ) -> Status;
    /// A dotted path from the root.
    pub fn rxfb_lookup(
        rx: *const Rxfb,
        path: *const c_char,
        length: u32,
        handle: *mut Handle,
    ) -> Status;
    /// The path of an object, written into `out` (no NUL).
    pub fn rxfb_path(
        rx: *const Rxfb,
        handle: Handle,
        out: *mut c_char,
        capacity: u32,
        written: *mut u32,
    ) -> Status;
    /// The first child of `parent`.
    pub fn rxfb_first_child(rx: *const Rxfb, parent: Handle, child: *mut Handle) -> Status;
    /// The sibling after `child`.
    pub fn rxfb_next_sibling(rx: *const Rxfb, child: Handle, next: *mut Handle) -> Status;
    /// The children of an object into a caller's array.
    pub fn rxfb_children(
        rx: *const Rxfb,
        handle: Handle,
        out: *mut Handle,
        capacity: u32,
        written: *mut u32,
    ) -> Status;

    /// The reference rows of an object, as a view into the image.
    pub fn rxfb_refs(
        rx: *const Rxfb,
        handle: Handle,
        rows: *mut *const Ref,
        count: *mut u32,
    ) -> Status;

    /// The bytes of a Function for a machine (NUL-terminated name).
    pub fn rxfb_code_of(
        rx: *const Rxfb,
        function: Handle,
        machine: *const c_char,
        code: *mut RxfbCode,
    ) -> Status;
    /// Function slot `slot` of the interpreter `run`.
    pub fn rxfb_slot_of(
        rx: *const Rxfb,
        run: Handle,
        machine: *const c_char,
        slot: u32,
        code: *mut RxfbCode,
    ) -> Status;
    /// The same for the image's own interpreter.
    pub fn rxfb_slot(
        rx: *const Rxfb,
        machine: *const c_char,
        slot: u32,
        code: *mut RxfbCode,
    ) -> Status;
}
