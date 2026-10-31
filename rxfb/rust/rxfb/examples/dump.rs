//! dump: the Rust face of `rxfb/example/rxfb_dump.c`.
//!
//! Maps an RXFB v7 image (read in place, nothing copied but the lines it prints) and prints, byte
//! for byte, what the C example prints: tab-separated records, one per line.
//!
//! ```text
//! dump IMAGE                          image   <size> <handles> <sha256 of the image>
//! dump IMAGE walk                     walk    <path> <kind> <payload size> <payload sha256>
//! dump IMAGE object PATH...           object / spec / children / refs of each path
//! dump IMAGE table NAME...            table   <name> <count> <rows of its relation table>
//! dump IMAGE code MACHINE SLOT...     code    <machine> <slot> <owner path> <length> <sha256>
//! ```
//!
//! Hosted on purpose: mmap and SHA-256 belong to the host (here `libc` and `sha2`), never to the
//! library.

use std::ffi::{CString, OsString};
use std::io::{self, BufWriter, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::process::ExitCode;
use std::{fs, ptr, slice};

use rxfb::{Error, Handle, Image, Reference};
use sha2::{Digest, Sha256};

/// Room for one path (the C example's `PATH_ROOM`).
const PATH_ROOM: usize = 4096;
/// How many references one `object` record spells out (the counts are exact).
const REFS_SHOWN: usize = 4;
/// How many child names one `object` record spells out.
const NAMES_SHOWN: usize = 8;

/// Why a command stopped.
enum Stop {
    /// A refusal of the library, printed as `rxfb_dump: <what>: status <n>`.
    Refused(Vec<u8>, u32),
    /// The output went away.
    Io(io::Error),
}

impl From<io::Error> for Stop {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

type Outcome = Result<(), Stop>;

fn refused(what: impl AsRef<[u8]>, error: Error) -> Stop {
    Stop::Refused(what.as_ref().to_vec(), error.status())
}

fn bad_image(what: &str) -> Stop {
    refused(what, Error::BadImage)
}

fn hex(out: &mut impl Write, bytes: &[u8]) -> io::Result<()> {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut text = Vec::with_capacity(bytes.len() * 2);
    for byte in bytes {
        text.push(DIGITS[usize::from(byte >> 4)]);
        text.push(DIGITS[usize::from(byte & 15)]);
    }
    out.write_all(&text)
}

fn digest(out: &mut impl Write, bytes: &[u8]) -> io::Result<()> {
    hex(out, &Sha256::digest(bytes))
}

/// The path of a handle into `buffer`; `None` when the handle or the buffer is not good.
fn path_text<'b>(image: &Image<'_>, handle: Handle, buffer: &'b mut [u8]) -> Option<&'b [u8]> {
    image.path(handle, buffer).ok()
}

/// The reference rows: one `<target>:<role>:<kind>:<index>` field per row (the first
/// `REFS_SHOWN`; the counts are exact), ended by the newline of the record.
fn ref_rows(image: &Image<'_>, out: &mut impl Write, rows: &[Reference], what: &[u8]) -> Outcome {
    let mut buffer = [0_u8; PATH_ROOM];
    for reference in rows.iter().take(REFS_SHOWN) {
        out.write_all(b"\t")?;
        let target = path_text(image, reference.target(), &mut buffer)
            .ok_or_else(|| refused(what, Error::BadImage))?;
        out.write_all(target)?;
        out.write_all(b":")?;
        if let Some(role) = reference.role() {
            let role = path_text(image, role, &mut buffer)
                .ok_or_else(|| refused(what, Error::BadImage))?;
            out.write_all(role)?;
        }
        write!(out, ":{}:{}", reference.kind(), reference.index())?;
    }
    out.write_all(b"\n")?;
    Ok(())
}

fn print_object(image: &Image<'_>, out: &mut impl Write, path: &[u8]) -> Outcome {
    let mut buffer = [0_u8; PATH_ROOM];
    let handle = image.lookup(path).map_err(|error| refused(path, error))?;
    let record = image.record(handle).map_err(|error| refused(path, error))?;
    let payload = image
        .payload(handle)
        .map_err(|error| refused(path, error))?;
    out.write_all(b"object\t")?;
    out.write_all(path)?;
    write!(out, "\t{}\t{}\t", record.kind(), payload.len())?;
    hex(out, payload)?;
    out.write_all(b"\t")?;
    digest(out, payload)?;
    out.write_all(b"\n")?;

    let row = image.spec(handle).map_err(|error| refused(path, error))?;
    let type_path: &[u8] = match row.type_handle() {
        Some(type_handle) => path_text(image, type_handle, &mut buffer)
            .ok_or_else(|| refused(path, Error::BadImage))?,
        None => b"",
    };
    out.write_all(b"spec\t")?;
    out.write_all(path)?;
    out.write_all(b"\t")?;
    out.write_all(type_path)?;
    writeln!(
        out,
        "\t{}\t{}\t{}",
        row.flags(),
        row.index(),
        row.generation()
    )?;

    out.write_all(b"children\t")?;
    out.write_all(path)?;
    out.write_all(b"\t")?;
    let mut count = 0_u32;
    for child in image
        .children(handle)
        .map_err(|error| refused(path, error))?
    {
        let child = child.map_err(|error| refused(path, error))?;
        let name = image.name(child).map_err(|error| refused(path, error))?;
        if (count as usize) < NAMES_SHOWN {
            if count > 0 {
                out.write_all(b",")?;
            }
            out.write_all(name)?;
        }
        count += 1;
    }
    writeln!(out, "\t{count}")?;
    // The iterator and the counting call are one walk: they must agree on the count.
    match image.child_count(handle) {
        Ok(total) if total == count => {}
        _ => return Err(refused(path, Error::BadImage)),
    }

    let refs = image.refs(handle).map_err(|error| refused(path, error))?;
    out.write_all(b"refs\t")?;
    out.write_all(path)?;
    write!(out, "\t{}", refs.len())?;
    ref_rows(image, out, refs, path)
}

/// The rows of the relation table named after `name`, read straight off the table object of
/// `sys/relations` (the one-pass story of `table`, where `object` walks the chain to find the
/// table of one object).
fn print_table(image: &Image<'_>, out: &mut impl Write, name: &[u8]) -> Outcome {
    let relations = image
        .lookup(b"sys.relations")
        .map_err(|error| refused(name, error))?;
    let table = image
        .find(relations, name)
        .map_err(|error| refused(name, error))?;
    let rows = image.table(table).map_err(|error| refused(name, error))?;
    out.write_all(b"table\t")?;
    out.write_all(name)?;
    write!(out, "\t{}", rows.len())?;
    ref_rows(image, out, rows, name)
}

/// One line per object: the tree in chain order, children before the next sibling. The walk keeps
/// no stack: it climbs back through the records' `parent` links.
fn print_walk(image: &Image<'_>, out: &mut impl Write) -> Outcome {
    let mut buffer = [0_u8; PATH_ROOM];
    let root = image.root();
    let mut at = root;
    let mut visited = 0_u32;
    loop {
        let mut record = image.record(at).map_err(|error| refused("walk", error))?;
        let payload = image.payload(at).map_err(|error| refused("walk", error))?;
        let path = path_text(image, at, &mut buffer).ok_or_else(|| bad_image("walk"))?;
        out.write_all(b"walk\t")?;
        out.write_all(path)?;
        write!(out, "\t{}\t{}\t", record.kind(), payload.len())?;
        digest(out, payload)?;
        out.write_all(b"\n")?;
        visited += 1;
        if visited > image.count() {
            return Err(bad_image("walk")); // more objects than handles: a cycle
        }

        let mut next = match image.first_child(at) {
            Ok(next) => next,
            Err(Error::Protected) => None, // the object index: a table, not a chain
            Err(error) => return Err(refused("walk", error)),
        };
        while next.is_none() {
            if at == root {
                return Ok(()); // the root has no sibling: the tree is done
            }
            next = image
                .next_sibling(at)
                .map_err(|error| refused("walk", error))?;
            if next.is_some() {
                break;
            }
            at = record
                .parent()
                .ok_or_else(|| refused("walk", Error::BadHandle))?;
            record = image.record(at).map_err(|error| refused("walk", error))?;
        }
        at = next.ok_or_else(|| bad_image("walk"))?;
    }
}

fn print_slot(image: &Image<'_>, out: &mut impl Write, machine: &str, slot: u32) -> Outcome {
    let name = CString::new(machine).map_err(|_| refused("slot", Error::BadName))?;
    let code = image
        .slot(&name, slot)
        .map_err(|error| refused("slot", error))?;
    let record = image
        .record(code.code())
        .map_err(|error| refused("slot", error))?;
    let owner = record.parent().ok_or_else(|| bad_image("slot"))?;
    let mut buffer = [0_u8; PATH_ROOM];
    let path = path_text(image, owner, &mut buffer).ok_or_else(|| bad_image("slot"))?;
    write!(out, "code\t{machine}\t{slot}\t")?;
    out.write_all(path)?;
    write!(out, "\t{}\t", code.bytes().len())?;
    digest(out, code.bytes())?;
    out.write_all(b"\n")?;
    Ok(())
}

/// A read-only private mapping of a file: the image's bytes, read in place.
struct Mapping {
    base: *mut libc::c_void,
    length: usize,
}

/// An error as `perror` words it: the system's text without Rust's "(os error N)" suffix.
fn perror(label: &str, error: &io::Error) -> String {
    let text = error.to_string();
    let text = text
        .split_once(" (os error")
        .map_or(text.as_str(), |(head, _)| head);
    format!("{label}: {text}")
}

impl Mapping {
    /// Maps the file; the error is the line `perror` would print.
    fn open(path: &OsString) -> Result<Self, String> {
        let label = path.to_string_lossy();
        let file = fs::File::open(path).map_err(|error| perror(&label, &error))?;
        let size = file
            .metadata()
            .map_err(|error| perror(&label, &error))?
            .len();
        let length = usize::try_from(size).map_err(|_| format!("{label}: file too large"))?;
        // SAFETY: a fresh read-only private mapping of an open file; the result is checked.
        let base = unsafe {
            libc::mmap(
                ptr::null_mut(),
                length,
                libc::PROT_READ,
                libc::MAP_PRIVATE,
                file.as_raw_fd(),
                0,
            )
        };
        if base == libc::MAP_FAILED {
            return Err(perror("mmap", &io::Error::last_os_error()));
        }
        Ok(Self { base, length })
    }

    fn bytes(&self) -> &[u8] {
        // SAFETY: `base` maps `length` readable bytes for as long as `self` lives.
        unsafe { slice::from_raw_parts(self.base.cast::<u8>(), self.length) }
    }
}

impl Drop for Mapping {
    fn drop(&mut self) {
        // SAFETY: `base`/`length` are the mapping `open` made, unmapped exactly once.
        unsafe {
            libc::munmap(self.base, self.length);
        }
    }
}

/// `strtoul(text, 0, 10)`: the leading digits, or 0.
fn slot_number(text: &str) -> u32 {
    let digits: String = text.chars().take_while(char::is_ascii_digit).collect();
    digits.parse().unwrap_or(0)
}

fn run(arguments: &[OsString], out: &mut impl Write) -> Result<u8, Stop> {
    let path = &arguments[1];
    let mapping = match Mapping::open(path) {
        Ok(mapping) => mapping,
        Err(line) => {
            out.flush()?;
            eprintln!("{line}");
            return Ok(1);
        }
    };
    let image = Image::attach(mapping.bytes()).map_err(|error| refused("attach", error))?;

    if arguments.len() == 2 {
        write!(out, "image\t{}\t{}\t", mapping.bytes().len(), image.count())?;
        digest(out, mapping.bytes())?;
        out.write_all(b"\n")?;
        return Ok(0);
    }
    let command = arguments[2].as_bytes();
    if command == b"walk" {
        print_walk(&image, out)?;
    } else if command == b"object" {
        for argument in &arguments[3..] {
            print_object(&image, out, argument.as_bytes())?;
        }
    } else if command == b"table" {
        for argument in &arguments[3..] {
            print_table(&image, out, argument.as_bytes())?;
        }
    } else if command == b"code" && arguments.len() >= 4 {
        let machine = arguments[3].to_string_lossy();
        for argument in &arguments[4..] {
            print_slot(
                &image,
                out,
                &machine,
                slot_number(&argument.to_string_lossy()),
            )?;
        }
    } else {
        out.flush()?;
        eprintln!(
            "rxfb_dump: unknown command {}",
            arguments[2].to_string_lossy()
        );
        return Ok(2);
    }
    Ok(0)
}

fn main() -> ExitCode {
    let arguments: Vec<OsString> = std::env::args_os().collect();
    if arguments.len() < 2 {
        eprintln!(
            "usage: rxfb_dump IMAGE [walk | object PATH... | table NAME... | code MACHINE SLOT...]"
        );
        return ExitCode::from(2);
    }
    let mut out = BufWriter::new(io::stdout().lock());
    let result = run(&arguments, &mut out);
    let flushed = out.flush();
    match result {
        Ok(code) if flushed.is_ok() => ExitCode::from(code),
        Ok(_) => ExitCode::from(1),
        Err(Stop::Io(error)) => {
            eprintln!("rxfb_dump: {error}");
            ExitCode::from(1)
        }
        Err(Stop::Refused(what, status)) => {
            let mut line = b"rxfb_dump: ".to_vec();
            line.extend_from_slice(&what);
            line.extend_from_slice(format!(": status {status}\n").as_bytes());
            // Nothing to do when stderr itself is gone.
            let _ = io::stderr().write_all(&line);
            ExitCode::from(1)
        }
    }
}
