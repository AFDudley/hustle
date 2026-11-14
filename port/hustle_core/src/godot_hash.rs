// Port of Godot 3.5.1's `hash()` builtin, for the two Variant types the
// game's logic RNG seeds on: Variant::INT and Variant::STRING.
//
// Sources (Godot 3.5.1-stable, github.com/godotengine/godot):
// - `core/variant.cpp`, `Variant::hash()`, case INT: returns `_data._int`
//   from a function that returns `uint32_t`. The C++ narrowing conversion
//   from `int64_t` to `uint32_t` keeps the low 32 bits of the two's
//   complement value; `hash_int` below does the same with `as u32`.
// - `core/variant.cpp`, `Variant::hash()`, case STRING: calls
//   `String::hash()`.
// - `core/ustring.cpp`, `String::hash() const`: djb2 over the string's
//   `CharType` code units (`hashv = hashv*33 + c`, seed 5381). Godot 3's
//   `CharType` holds one Unicode code point per character (UCS-4 on
//   Linux).
// - `modules/gdscript/gdscript_functions.cpp`, case HASH:
//   `r_ret = p_args[0]->hash();`. The GDScript builtin `hash()` returns
//   whatever `Variant::hash()` returns, boxed as a Variant::INT through the
//   `Variant(unsigned int)` / `Variant(uint32_t)`-family constructor, so a
//   GDScript `hash(...)` call is always a non-negative 64-bit int in
//   `0 ..= 0xFFFFFFFF`. This module returns the raw `u32` instead; a
//   caller that needs the GDScript-visible value widens it with
//   `as i64` (zero-extend).
//
// `hash_bytes` folds over `&[u8]`, not `&str`: a `for c in s.chars()`
// loop over a real `&str` made Aeneas fail with `[Error] Unreachable`
// (checked against port/hustle_core, 2026-11-14; see the crate's own
// README.md, "Axioms" and the lab book). `.chars()` is a UTF-8-decoding
// iterator with internal control flow Aeneas's symbolic execution could
// not follow; recursion over a plain byte index, the same pattern this
// crate's other loops use, has no such iterator.
//
// `hash_bytes` treats every byte as one code point. This is exactly
// right for ASCII input, and wrong for anything else (a multi-byte
// UTF-8 sequence would hash as several separate code points instead of
// one). Every string the game logic hashes is ASCII:
//   - A `FixedMath` display string: a sign, digits, and `.` (see
//     `fixed::Fixed::display_bytes`).
//   - A state name: `characters/BaseChar.gd`'s `get_state_hash()` hashes
//     `current_state().state_name`, which is a Godot node's `.name`
//     (`framework/FiniteStateMachine/StateInterface.gd`:
//     `onready var state_name = name`), set by each state node's name in
//     its character's `.tscn` scene. Checked: every direct child of a
//     `StateMachine` node, across the five official characters'
//     `.tscn` files, is ASCII (293 distinct names, 2026-11-14).

/// `Variant::hash()` for an `INT` value, bit for bit:
/// `core/variant.cpp`, case INT, narrowed from `int64_t` to `uint32_t`.
pub fn hash_int(value: i64) -> u32 {
    value as u32
}

/// `String::hash()`: djb2 over the string's bytes, seed 5381, `hash =
/// hash*33 + c` (`core/ustring.cpp`). Correct for ASCII input only (see
/// the module doc comment). Recursion over the byte index, not a `for`
/// loop over an iterator (see the module doc comment).
pub fn hash_bytes(bytes: &[u8]) -> u32 {
    hash_bytes_rec(bytes, 0, bytes.len(), 5381)
}

/// Like `hash_bytes`, but over only the first `len` bytes of `bytes`
/// (`bytes` may be a fixed-size buffer with unused trailing zeros, as
/// `fixed::Fixed::display_bytes` returns). `len` must not exceed
/// `bytes.len()`.
pub fn hash_bytes_prefix(bytes: &[u8], len: usize) -> u32 {
    hash_bytes_rec(bytes, 0, len, 5381)
}

fn hash_bytes_rec(bytes: &[u8], i: usize, end: usize, acc: u32) -> u32 {
    if i >= end {
        acc
    } else {
        let b = bytes[i];
        hash_bytes_rec(bytes, i + 1, end, acc.wrapping_mul(33).wrapping_add(b as u32))
    }
}

/// Shell wrapper over `hash_bytes`, for a real `&str` (ASCII only; see
/// the module doc comment). Not part of the Lean-checked core: see
/// port/hustle_core/README.md, "The core and the shell".
#[cfg(feature = "text")]
pub fn hash_string(value: &str) -> u32 {
    hash_bytes(value.as_bytes())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_int_truncates_to_low_32_bits() {
        assert_eq!(hash_int(0), 0);
        assert_eq!(hash_int(1), 1);
        assert_eq!(hash_int(-1), 0xFFFF_FFFF);
        assert_eq!(hash_int(0x1_0000_0000), 0);
    }

    #[test]
    fn hash_bytes_empty_is_the_djb2_seed() {
        assert_eq!(hash_bytes(b""), 5381);
    }

    #[test]
    fn hash_bytes_prefix_ignores_the_tail() {
        let buf = *b"abc\0\0\0";
        assert_eq!(hash_bytes_prefix(&buf, 3), hash_bytes(b"abc"));
    }
}
