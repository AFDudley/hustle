//! `&str`/`String` wrappers around `fixed::Fixed`'s byte-based core API.
//!
//! This module is the shell, not the core (see the crate's own
//! `README.md`, "The core and the shell"). It exists only because
//! `tools/fixedmath_diff` and other callers outside the Lean-checked
//! core want ordinary Rust strings. It is gated behind the `text`
//! feature, off by default, so a plain `cargo build` (the one Charon
//! runs) never compiles it: Charon sees no `String`, `&str`, or
//! `core::str::as_bytes` call anywhere in the crate, so Aeneas never
//! needs to axiomatize them.

use crate::fixed::Fixed;

/// Parses a decimal string. Thin wrapper over `Fixed::from_bytes`.
pub fn from_str(s: &str) -> Option<Fixed> {
    Fixed::from_bytes(s.as_bytes())
}

/// Formats as decimal. Thin wrapper over `Fixed::display_bytes`: copies
/// the used bytes (always ASCII: digits, `-`, `.`) into a `String`.
pub fn display(v: Fixed) -> String {
    let (bytes, len) = v.display_bytes();
    let mut s = String::with_capacity(len);
    for i in 0..len {
        s.push(bytes[i] as char);
    }
    s
}
