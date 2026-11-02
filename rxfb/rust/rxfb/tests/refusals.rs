//! What the safe face refuses without any image file: bytes that are not an image, an unaligned
//! mapping, and the handle word. (The real image is proven by `tests/test_rxfb_rust.py`, which
//! compares the Rust example with the C one byte for byte.)

use rxfb::{Error, Handle, Image};

/// 8-byte aligned storage for hand-made images.
#[repr(C, align(8))]
struct Aligned<const N: usize>([u8; N]);

#[test]
fn nothing_is_not_an_image() {
    assert_eq!(Image::attach(&[]).unwrap_err(), Error::BadImage);
}

#[test]
fn zeros_are_not_an_image() {
    let bytes = Aligned([0_u8; 8192]);
    assert_eq!(Image::attach(&bytes.0).unwrap_err(), Error::BadImage);
}

#[test]
fn a_superblock_alone_is_not_an_image() {
    let mut bytes = Aligned([0_u8; 8192]);
    bytes.0[..4].copy_from_slice(b"RXFA");
    bytes.0[4..8].copy_from_slice(&1_u32.to_le_bytes());
    assert_eq!(Image::attach(&bytes.0).unwrap_err(), Error::BadImage);
}

#[test]
fn a_misaligned_mapping_is_refused() {
    let bytes = Aligned([0_u8; 8200]);
    assert_eq!(Image::attach(&bytes.0[1..]).unwrap_err(), Error::BadImage);
}

#[test]
fn the_refusals_keep_their_c_status() {
    assert_eq!(Error::BadImage.status(), 1);
    assert_eq!(Error::BadHandle.status(), 2);
    assert_eq!(Error::BadName.status(), 3);
    assert_eq!(Error::Protected.status(), 4);
    assert_eq!(Error::NoRoom { needed: 9 }.status(), 5);
    assert_eq!(Error::NotFound.status(), 6);
}

#[test]
fn a_handle_is_generation_and_index() {
    assert_eq!(Handle::from_raw(0xFFFF_FFFF), None);
    let handle = Handle::from_raw((3 << 20) | 77).expect("a handle");
    assert_eq!((handle.generation(), handle.index()), (3, 77));
    assert_eq!(handle.raw(), (3 << 20) | 77);
}
