//! `rxfb`: a safe, zero-copy reader of an RXFB v7 image (docs/32).
//!
//! The reader itself is the freestanding C core (`rxfb/rxfb.c`, compiled by `rxfb-sys`); this crate
//! only gives it Rust types:
//!
//! * [`Image`] borrows the image bytes (`&'a [u8]`, 8-byte aligned) and every view it answers
//!   (`&'a [u8]` payloads and names, [`Record`], [`SpecRow`], [`Reference`], [`Code`]) borrows the
//!   same bytes: nothing is copied and nothing outlives the mapping.
//! * Refusals are [`Error`] values (`Result`), one per C status; a corrupt image is refused, never
//!   read outside the bytes.
//! * The children of an object are an iterator ([`Children`]) that needs no memory, its references
//!   are a slice of rows inside the image, and a path is written into a buffer the caller owns.
//!
//! The crate is `#![no_std]` and allocates nothing (no `alloc` either).
#![no_std]

use core::ffi::{CStr, c_char};
use core::fmt;
use core::marker::PhantomData;
use core::ptr;
use core::slice;

use rxfb_sys as sys;

/// What a refusal of the C core is. One variant per `rxfb_status` other than `RXFB_OK`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[non_exhaustive]
pub enum Error {
    /// The bytes are not a usable v7 image (or a chain or offset in them is corrupt).
    BadImage,
    /// A handle that is stale, out of range, or none.
    BadHandle,
    /// An empty name or path segment.
    BadName,
    /// The object index itself: its payload is a table, not a chain.
    Protected,
    /// The buffer is too small; `needed` is what it must offer.
    NoRoom {
        /// The length the answer takes.
        needed: u32,
    },
    /// A lookup that names nothing, or a function without code for that machine.
    NotFound,
    /// A status this crate does not know (a newer core).
    Unknown(u32),
}

impl Error {
    /// The `rxfb_status` number of the refusal (what the C example prints as `status N`).
    #[must_use]
    pub const fn status(self) -> u32 {
        match self {
            Self::BadImage => sys::RXFB_BAD_IMAGE,
            Self::BadHandle => sys::RXFB_BAD_HANDLE,
            Self::BadName => sys::RXFB_BAD_NAME,
            Self::Protected => sys::RXFB_PROTECTED,
            Self::NoRoom { .. } => sys::RXFB_NO_ROOM,
            Self::NotFound => sys::RXFB_NOT_FOUND,
            Self::Unknown(status) => status,
        }
    }

    fn from_status(status: sys::Status, written: u32) -> Self {
        match status {
            sys::RXFB_BAD_IMAGE => Self::BadImage,
            sys::RXFB_BAD_HANDLE => Self::BadHandle,
            sys::RXFB_BAD_NAME => Self::BadName,
            sys::RXFB_PROTECTED => Self::Protected,
            sys::RXFB_NO_ROOM => Self::NoRoom { needed: written },
            sys::RXFB_NOT_FOUND => Self::NotFound,
            other => Self::Unknown(other),
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::BadImage => out.write_str("not a usable rxfb image"),
            Self::BadHandle => out.write_str("stale or out-of-range handle"),
            Self::BadName => out.write_str("empty name or path segment"),
            Self::Protected => out.write_str("the object index has no children chain"),
            Self::NoRoom { needed } => write!(out, "buffer too small: {needed} needed"),
            Self::NotFound => out.write_str("not found"),
            Self::Unknown(status) => write!(out, "unknown rxfb status {status}"),
        }
    }
}

impl core::error::Error for Error {}

/// Shorthand for the result type of this crate.
pub type Result<T> = core::result::Result<T, Error>;

fn check(status: sys::Status, written: u32) -> Result<()> {
    if status == sys::RXFB_OK {
        Ok(())
    } else {
        Err(Error::from_status(status, written))
    }
}

/// A handle of an object in the image: `(generation << 20) | index`. Never the none value; an
/// absent handle is `Option<Handle>`. A handle is only a number: it is checked when it is used.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
#[repr(transparent)]
pub struct Handle(u32);

impl Handle {
    /// The handle of a raw word; `None` for the none value.
    #[must_use]
    pub const fn from_raw(raw: u32) -> Option<Self> {
        if raw == sys::RXFB_HANDLE_NONE {
            None
        } else {
            Some(Self(raw))
        }
    }

    /// The raw word.
    #[must_use]
    pub const fn raw(self) -> u32 {
        self.0
    }

    /// The index part.
    #[must_use]
    pub const fn index(self) -> u32 {
        sys::rxfb_handle_index(self.0)
    }

    /// The generation part.
    #[must_use]
    pub const fn generation(self) -> u32 {
        sys::rxfb_handle_gen(self.0)
    }
}

impl fmt::Display for Handle {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(out, "{}", self.0)
    }
}

/// The 24-byte header of an object, read in place.
#[derive(Debug)]
#[repr(transparent)]
pub struct Record(sys::Object);

impl Record {
    /// The object kind (`RXFB_OBJ_*`, re-exported in [`kind`]).
    #[must_use]
    pub const fn kind(&self) -> u16 {
        self.0.kind
    }

    /// The generation of the record.
    #[must_use]
    pub const fn generation(&self) -> u16 {
        self.0.gen_
    }

    /// The object flags (`RXFB_OFLAG_*`).
    #[must_use]
    pub const fn flags(&self) -> u16 {
        self.0.flags
    }

    /// The interned name's symbol id; 0 is anonymous.
    #[must_use]
    pub const fn name_id(&self) -> u16 {
        self.0.name
    }

    /// The parent object, `None` for the root.
    #[must_use]
    pub const fn parent(&self) -> Option<Handle> {
        Handle::from_raw(self.0.parent)
    }

    /// The next sibling under the parent.
    #[must_use]
    pub const fn link(&self) -> Option<Handle> {
        Handle::from_raw(self.0.link)
    }

    /// The reference count.
    #[must_use]
    pub const fn refs(&self) -> u32 {
        self.0.refs
    }

    /// The payload room in bytes.
    #[must_use]
    pub const fn payload_size(&self) -> u32 {
        self.0.payload
    }
}

/// The semantic row of an object (`sys/specs`), read in place.
#[derive(Debug)]
#[repr(transparent)]
pub struct SpecRow(sys::SpecRow);

impl SpecRow {
    /// The object's Type, `None` when it is untyped.
    #[must_use]
    pub const fn type_handle(&self) -> Option<Handle> {
        Handle::from_raw(self.0.type_)
    }

    /// The world's flags word: kind | state | owner.
    #[must_use]
    pub const fn flags(&self) -> u32 {
        self.0.flags
    }

    /// The position among the ordered siblings; 0 = the order is the name.
    #[must_use]
    pub const fn index(&self) -> u32 {
        self.0.index
    }

    /// The world's generation of the object.
    #[must_use]
    pub const fn generation(&self) -> u64 {
        self.0.generation
    }
}

/// One reference row (`sys/relations`), read in place.
#[derive(Debug)]
#[repr(transparent)]
pub struct Reference(sys::Ref);

impl Reference {
    /// The target object. The row stores a zero-extended handle; the word is read as a handle.
    #[must_use]
    pub const fn target(&self) -> Handle {
        #[allow(clippy::cast_possible_truncation)]
        Handle(self.0.target as u32)
    }

    /// The role object, `None` when the reference has no role.
    #[must_use]
    pub const fn role(&self) -> Option<Handle> {
        if self.0.role == sys::RXFB_REF_NONE {
            None
        } else {
            #[allow(clippy::cast_possible_truncation)]
            Some(Handle(self.0.role as u32))
        }
    }

    /// The reference kind (`RXFB_REF_*`, re-exported in [`reference`]).
    #[must_use]
    pub const fn kind(&self) -> u16 {
        self.0.kind
    }

    /// The binding (`RXFB_BINDING_*`).
    #[must_use]
    pub const fn binding(&self) -> u16 {
        self.0.binding
    }

    /// The argument or leaf number.
    #[must_use]
    pub const fn index(&self) -> u32 {
        self.0.index
    }
}

/// The bytes of a function for one machine, inside the image.
#[derive(Clone, Copy, Debug)]
pub struct Code<'a> {
    bytes: &'a [u8],
    code: Handle,
    blob: Handle,
}

impl<'a> Code<'a> {
    /// The code: a read-only view into the image for a host to interpret, JIT or link.
    #[must_use]
    pub const fn bytes(&self) -> &'a [u8] {
        self.bytes
    }

    /// The Code object the bytes came from.
    #[must_use]
    pub const fn code(&self) -> Handle {
        self.code
    }

    /// The CodeBlob object the bytes came from.
    #[must_use]
    pub const fn blob(&self) -> Handle {
        self.blob
    }
}

/// Object kinds, as the `kind` of a [`Record`].
pub mod kind {
    pub use rxfb_sys::{
        RXFB_OBJ_BLOB as BLOB, RXFB_OBJ_FRAME as FRAME, RXFB_OBJ_FREE as FREE,
        RXFB_OBJ_FUNC as FUNC, RXFB_OBJ_HASH as HASH, RXFB_OBJ_JOURNAL as JOURNAL,
        RXFB_OBJ_MODULE as MODULE, RXFB_OBJ_SPEC as SPEC, RXFB_OBJ_SYM as SYM,
    };
}

/// Reference kinds, as the `kind` of a [`Reference`].
pub mod reference {
    pub use rxfb_sys::{
        RXFB_REF_CALL as CALL, RXFB_REF_DATA as DATA, RXFB_REF_DOC as DOC, RXFB_REF_ENTRY as ENTRY,
        RXFB_REF_IMPLEMENTATION as IMPLEMENTATION, RXFB_REF_IMPORT as IMPORT,
        RXFB_REF_OWNER as OWNER, RXFB_REF_TYPE as TYPE,
    };
}

/// The interpreter whose Leaf references are the function slots of a linked image.
pub const RUN_PATH: &str = sys::RXFB_RUN_PATH;

/// An attached image. It borrows the bytes for `'a` and every view it answers lives as long as
/// they do. It is plain data: copy it, share it between threads; a reader never writes.
#[derive(Clone, Copy)]
pub struct Image<'a> {
    raw: sys::Rxfb,
    bytes: PhantomData<&'a [u8]>,
}

// SAFETY: an `Image` holds pointers into bytes it only reads, and the borrow keeps those bytes
// alive and unchanged for `'a`; there is no interior mutability anywhere in the C core.
unsafe impl Send for Image<'_> {}
// SAFETY: as above; every call is a read.
unsafe impl Sync for Image<'_> {}

impl fmt::Debug for Image<'_> {
    fn fmt(&self, out: &mut fmt::Formatter<'_>) -> fmt::Result {
        out.debug_struct("Image")
            .field("size", &self.raw.size)
            .field("count", &self.count())
            .finish_non_exhaustive()
    }
}

/// A slice's length as the `u32` the C core takes; longer than that is refused as a name.
fn length_of(bytes: &[u8]) -> Result<u32> {
    u32::try_from(bytes.len()).map_err(|_| Error::BadName)
}

impl<'a> Image<'a> {
    /// Validates the superblock and the object index of `bytes` and attaches. The bytes must be
    /// 8-byte aligned (a mapped file, an allocation, a `Vec<u64>`); anything else is
    /// [`Error::BadImage`], as is every corrupt or truncated image.
    ///
    /// # Errors
    ///
    /// [`Error::BadImage`] when the bytes are not a usable v7 image.
    pub fn attach(bytes: &'a [u8]) -> Result<Self> {
        let mut raw = sys::Rxfb::detached();
        // SAFETY: `raw` is a valid out-parameter and `bytes` is a readable slice of the length
        // given; the core validates the contents and the alignment and writes `raw` on success only.
        let status = unsafe { sys::rxfb_attach(&raw mut raw, bytes.as_ptr(), bytes.len() as u64) };
        check(status, 0)?;
        Ok(Self {
            raw,
            bytes: PhantomData,
        })
    }

    /// The image bytes this reader is attached to.
    #[must_use]
    pub fn bytes(&self) -> &'a [u8] {
        // SAFETY: `attach` stored the pointer and length of the slice `'a` borrows.
        unsafe { slice::from_raw_parts(self.raw.base, self.len()) }
    }

    #[allow(clippy::cast_possible_truncation)]
    fn len(&self) -> usize {
        // `attach` refused anything longer than the address space.
        self.raw.size as usize
    }

    /// The root object: the image itself. Its path is `""`.
    #[must_use]
    pub fn root(&self) -> Handle {
        // SAFETY: `self.raw` was filled by a successful attach.
        Handle(unsafe { sys::rxfb_root(&raw const self.raw) })
    }

    /// The entry object (the first run), `None` when the image has none.
    #[must_use]
    pub fn entry(&self) -> Option<Handle> {
        // SAFETY: as in `root`.
        Handle::from_raw(unsafe { sys::rxfb_entry(&raw const self.raw) })
    }

    /// The handles (index slots) the image holds.
    #[must_use]
    pub fn count(&self) -> u32 {
        // SAFETY: as in `root`.
        unsafe { sys::rxfb_count(&raw const self.raw) }
    }

    // ---- objects --------------------------------------------------------------------------

    /// The record a handle names, checked against its index entry.
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] for a stale or out-of-range handle, [`Error::BadImage`] for a
    /// corrupt record.
    pub fn record(&self, handle: Handle) -> Result<&'a Record> {
        let mut record: *const sys::Object = ptr::null();
        // SAFETY: `self.raw` is attached and `record` is a valid out-parameter.
        let status = unsafe { sys::rxfb_record(&raw const self.raw, handle.0, &raw mut record) };
        check(status, 0)?;
        // SAFETY: on success `record` points at a bounds-checked, 8-aligned record inside the
        // image bytes, which `'a` keeps alive; `Record` is a transparent wrapper of the C struct.
        Ok(unsafe { &*record.cast::<Record>() })
    }

    /// The payload of an object, a view into the image (a multiple of 8 bytes; a module's payload
    /// opens with its 8-byte chain head).
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`], [`Error::BadImage`], or [`Error::Protected`] for the object index.
    pub fn payload(&self, handle: Handle) -> Result<&'a [u8]> {
        let mut bytes: *const u8 = ptr::null();
        let mut size = 0_u32;
        // SAFETY: `self.raw` is attached; both out-parameters are valid.
        let status = unsafe {
            sys::rxfb_payload(&raw const self.raw, handle.0, &raw mut bytes, &raw mut size)
        };
        check(status, 0)?;
        Ok(self.view(bytes, size))
    }

    /// A view of `size` bytes at `bytes`, which the core answered inside the image.
    fn view(&self, bytes: *const u8, size: u32) -> &'a [u8] {
        if size == 0 || bytes.is_null() {
            return &[];
        }
        // SAFETY: the core bounds-checked [bytes, bytes + size) against the image, which `'a`
        // keeps alive and unchanged.
        unsafe { slice::from_raw_parts(bytes, size as usize) }
    }

    /// The text of an interned name (without its NUL).
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] when the id is out of range, [`Error::BadImage`] for a corrupt table.
    pub fn symbol(&self, id: u32) -> Result<&'a [u8]> {
        let mut text: *const c_char = ptr::null();
        let mut length = 0_u32;
        // SAFETY: `self.raw` is attached; both out-parameters are valid.
        let status =
            unsafe { sys::rxfb_symbol(&raw const self.raw, id, &raw mut text, &raw mut length) };
        check(status, 0)?;
        Ok(self.view(text.cast(), length))
    }

    /// The name of an object; an anonymous object has the empty name.
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn name(&self, handle: Handle) -> Result<&'a [u8]> {
        let mut text: *const c_char = ptr::null();
        let mut length = 0_u32;
        // SAFETY: `self.raw` is attached; both out-parameters are valid.
        let status = unsafe {
            sys::rxfb_name(
                &raw const self.raw,
                handle.0,
                &raw mut text,
                &raw mut length,
            )
        };
        check(status, 0)?;
        Ok(self.view(text.cast(), length))
    }

    /// The semantic row of an object (`sys/specs`).
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] or [`Error::BadImage`]; an image without semantic rows is
    /// [`Error::BadImage`] too.
    pub fn spec(&self, handle: Handle) -> Result<&'a SpecRow> {
        let mut row: *const sys::SpecRow = ptr::null();
        // SAFETY: `self.raw` is attached and `row` is a valid out-parameter.
        let status = unsafe { sys::rxfb_spec(&raw const self.raw, handle.0, &raw mut row) };
        check(status, 0)?;
        // SAFETY: on success `row` is a bounds-checked, 8-aligned row inside the image bytes;
        // `SpecRow` is a transparent wrapper of the C struct.
        Ok(unsafe { &*row.cast::<SpecRow>() })
    }

    // ---- the tree -------------------------------------------------------------------------

    /// The child of `parent` with this name.
    ///
    /// # Errors
    ///
    /// [`Error::NotFound`] when the chain has none, [`Error::BadName`] for an empty name.
    pub fn find(&self, parent: Handle, name: impl AsRef<[u8]>) -> Result<Handle> {
        let name = name.as_ref();
        let mut handle = sys::RXFB_HANDLE_NONE;
        // SAFETY: `self.raw` is attached; `name` is readable for its length (no NUL needed).
        let status = unsafe {
            sys::rxfb_find(
                &raw const self.raw,
                parent.0,
                name.as_ptr().cast(),
                length_of(name)?,
                &raw mut handle,
            )
        };
        check(status, 0)?;
        Ok(Handle(handle))
    }

    /// A dotted path from the root; the empty path is the root.
    ///
    /// # Errors
    ///
    /// [`Error::NotFound`] when a segment names nothing, [`Error::BadName`] for an empty segment.
    pub fn lookup(&self, path: impl AsRef<[u8]>) -> Result<Handle> {
        let path = path.as_ref();
        let mut handle = sys::RXFB_HANDLE_NONE;
        // SAFETY: as in `find`.
        let status = unsafe {
            sys::rxfb_lookup(
                &raw const self.raw,
                path.as_ptr().cast(),
                length_of(path)?,
                &raw mut handle,
            )
        };
        check(status, 0)?;
        Ok(Handle(handle))
    }

    /// The path of an object, written into `out` (no NUL); the answer is the written prefix.
    ///
    /// # Errors
    ///
    /// [`Error::NoRoom`] (`needed` is the length it takes; nothing is written), [`Error::BadHandle`]
    /// or [`Error::BadImage`].
    pub fn path<'b>(&self, handle: Handle, out: &'b mut [u8]) -> Result<&'b [u8]> {
        let capacity = u32::try_from(out.len()).unwrap_or(u32::MAX);
        let mut written = 0_u32;
        // SAFETY: `self.raw` is attached; `out` is writable for `capacity` bytes and the core
        // writes no more than that.
        let status = unsafe {
            sys::rxfb_path(
                &raw const self.raw,
                handle.0,
                out.as_mut_ptr().cast(),
                capacity,
                &raw mut written,
            )
        };
        check(status, written)?;
        Ok(&out[..written as usize])
    }

    /// The first child of `parent`: `None` for a leaf or an empty module.
    ///
    /// # Errors
    ///
    /// [`Error::Protected`] for the object index, [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn first_child(&self, parent: Handle) -> Result<Option<Handle>> {
        let mut child = sys::RXFB_HANDLE_NONE;
        // SAFETY: `self.raw` is attached and `child` is a valid out-parameter.
        let status =
            unsafe { sys::rxfb_first_child(&raw const self.raw, parent.0, &raw mut child) };
        check(status, 0)?;
        Ok(Handle::from_raw(child))
    }

    /// The sibling after `child`: `None` at the end of the chain.
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn next_sibling(&self, child: Handle) -> Result<Option<Handle>> {
        let mut next = sys::RXFB_HANDLE_NONE;
        // SAFETY: `self.raw` is attached and `next` is a valid out-parameter.
        let status = unsafe { sys::rxfb_next_sibling(&raw const self.raw, child.0, &raw mut next) };
        check(status, 0)?;
        Ok(Handle::from_raw(next))
    }

    /// The children of an object in chain order, as an iterator that needs no memory. A chain
    /// longer than the image has handles (a link cycle) ends in one [`Error::BadImage`].
    ///
    /// # Errors
    ///
    /// The refusals of [`Image::first_child`].
    pub fn children(&self, parent: Handle) -> Result<Children<'a>> {
        Ok(Children {
            image: *self,
            at: self.first_child(parent)?,
            budget: self.count(),
        })
    }

    /// How many children an object has.
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`], [`Error::BadImage`] or [`Error::Protected`].
    pub fn child_count(&self, handle: Handle) -> Result<u32> {
        let mut written = 0_u32;
        // SAFETY: `self.raw` is attached; a null array with capacity 0 asks for the count only.
        let status = unsafe {
            sys::rxfb_children(
                &raw const self.raw,
                handle.0,
                ptr::null_mut(),
                0,
                &raw mut written,
            )
        };
        match status {
            sys::RXFB_OK | sys::RXFB_NO_ROOM => Ok(written),
            other => Err(Error::from_status(other, written)),
        }
    }

    /// The children of an object into `out`; the answer is the written prefix.
    ///
    /// # Errors
    ///
    /// [`Error::NoRoom`] (`needed` is the count) when `out` is too short, [`Error::BadHandle`],
    /// [`Error::BadImage`] or [`Error::Protected`].
    pub fn children_into<'b>(&self, handle: Handle, out: &'b mut [Handle]) -> Result<&'b [Handle]> {
        let capacity = u32::try_from(out.len()).unwrap_or(u32::MAX);
        let mut written = 0_u32;
        // SAFETY: `self.raw` is attached; `Handle` is a transparent `u32`, `out` is writable for
        // `capacity` of them and the core writes no more than that.
        let status = unsafe {
            sys::rxfb_children(
                &raw const self.raw,
                handle.0,
                out.as_mut_ptr().cast(),
                capacity,
                &raw mut written,
            )
        };
        check(status, written)?;
        Ok(&out[..written as usize])
    }

    // ---- references -----------------------------------------------------------------------

    /// The reference rows of an object: a slice of rows inside the image (none for an object
    /// without a table).
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn refs(&self, handle: Handle) -> Result<&'a [Reference]> {
        let mut rows: *const sys::Ref = ptr::null();
        let mut count = 0_u32;
        // SAFETY: `self.raw` is attached; both out-parameters are valid.
        let status =
            unsafe { sys::rxfb_refs(&raw const self.raw, handle.0, &raw mut rows, &raw mut count) };
        check(status, 0)?;
        if count == 0 || rows.is_null() {
            return Ok(&[]);
        }
        // SAFETY: the core bounds-checked `count` rows of 24 bytes at `rows` against the image,
        // 8-aligned; `Reference` is a transparent wrapper of the C row.
        Ok(unsafe { slice::from_raw_parts(rows.cast::<Reference>(), count as usize) })
    }

    /// The rows of a relation table read straight off its object: an object of `sys/relations`,
    /// named by the path of the object whose rows it holds - one pass over every table, where
    /// [`Self::refs`] finds one object's table by walking the chain. Any other object is
    /// [`Error::BadHandle`].
    ///
    /// # Errors
    ///
    /// [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn table(&self, table: Handle) -> Result<&'a [Reference]> {
        let mut rows: *const sys::Ref = ptr::null();
        let mut count = 0_u32;
        // SAFETY: `self.raw` is attached; both out-parameters are valid.
        let status =
            unsafe { sys::rxfb_table(&raw const self.raw, table.0, &raw mut rows, &raw mut count) };
        check(status, 0)?;
        if count == 0 || rows.is_null() {
            return Ok(&[]);
        }
        // SAFETY: the core bounds-checked `count` rows of 24 bytes at `rows` against the image,
        // 8-aligned; `Reference` is a transparent wrapper of the C row.
        Ok(unsafe { slice::from_raw_parts(rows.cast::<Reference>(), count as usize) })
    }

    // ---- the code story -------------------------------------------------------------------

    /// The bytes of a Function for a machine (`c"x86_64_linux"`, `c"aarch64_linux"`,
    /// `c"wasm32"`).
    ///
    /// # Errors
    ///
    /// [`Error::NotFound`] when the function has no code for that machine, [`Error::BadHandle`] or
    /// [`Error::BadImage`].
    pub fn code_of(&self, function: Handle, machine: &CStr) -> Result<Code<'a>> {
        let mut code = sys::RxfbCode::empty();
        // SAFETY: `self.raw` is attached, `machine` is NUL-terminated and `code` is valid.
        let status = unsafe {
            sys::rxfb_code_of(
                &raw const self.raw,
                function.0,
                machine.as_ptr(),
                &raw mut code,
            )
        };
        check(status, 0)?;
        self.code(&code)
    }

    /// Function slot `slot` of the interpreter `run`, resolved to its bytes for `machine`.
    ///
    /// # Errors
    ///
    /// [`Error::NotFound`] when the interpreter has no such leaf or the function has no code for
    /// `machine`, [`Error::BadHandle`] or [`Error::BadImage`].
    pub fn slot_of(&self, run: Handle, machine: &CStr, slot: u32) -> Result<Code<'a>> {
        let mut code = sys::RxfbCode::empty();
        // SAFETY: as in `code_of`.
        let status = unsafe {
            sys::rxfb_slot_of(
                &raw const self.raw,
                run.0,
                machine.as_ptr(),
                slot,
                &raw mut code,
            )
        };
        check(status, 0)?;
        self.code(&code)
    }

    /// The same for the image's own interpreter ([`RUN_PATH`]).
    ///
    /// # Errors
    ///
    /// As [`Image::slot_of`], and [`Error::NotFound`] when the image has no interpreter.
    pub fn slot(&self, machine: &CStr, slot: u32) -> Result<Code<'a>> {
        let mut code = sys::RxfbCode::empty();
        // SAFETY: as in `code_of`.
        let status =
            unsafe { sys::rxfb_slot(&raw const self.raw, machine.as_ptr(), slot, &raw mut code) };
        check(status, 0)?;
        self.code(&code)
    }

    /// The view of a resolved code, checked once more against the image so the slice cannot
    /// leave the bytes `'a` borrows.
    fn code(&self, code: &sys::RxfbCode) -> Result<Code<'a>> {
        let bytes = self.bytes();
        let start = bytes.as_ptr() as usize;
        let entry = code.entry as usize;
        let length = usize::try_from(code.length).map_err(|_| Error::BadImage)?;
        let offset = entry.checked_sub(start).ok_or(Error::BadImage)?;
        let end = offset.checked_add(length).ok_or(Error::BadImage)?;
        let view = bytes.get(offset..end).ok_or(Error::BadImage)?;
        Ok(Code {
            bytes: view,
            code: Handle(code.code),
            blob: Handle(code.blob),
        })
    }
}

/// The children of an object in chain order: an iterator that needs no memory. It yields each
/// handle once; a refusal while walking the chain is yielded as `Err` and ends the iteration, and
/// so does a chain longer than the image has handles (a link cycle).
#[derive(Clone, Copy, Debug)]
pub struct Children<'a> {
    image: Image<'a>,
    at: Option<Handle>,
    budget: u32,
}

impl Iterator for Children<'_> {
    type Item = Result<Handle>;

    fn next(&mut self) -> Option<Self::Item> {
        let at = self.at?;
        if self.budget == 0 {
            self.at = None;
            return Some(Err(Error::BadImage));
        }
        self.budget -= 1;
        match self.image.next_sibling(at) {
            Ok(next) => {
                self.at = next;
                Some(Ok(at))
            }
            Err(error) => {
                self.at = None;
                Some(Err(error))
            }
        }
    }
}

impl core::iter::FusedIterator for Children<'_> {}
