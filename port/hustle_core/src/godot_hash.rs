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
//   Linux), so `hash_string` below sums over `char`, not UTF-16 or UTF-8
//   code units.
// - `modules/gdscript/gdscript_functions.cpp`, case HASH:
//   `r_ret = p_args[0]->hash();`. The GDScript builtin `hash()` returns
//   whatever `Variant::hash()` returns, boxed as a Variant::INT through the
//   `Variant(unsigned int)` / `Variant(uint32_t)`-family constructor, so a
//   GDScript `hash(...)` call is always a non-negative 64-bit int in
//   `0 ..= 0xFFFFFFFF`. This module returns the raw `u32` instead; a
//   caller that needs the GDScript-visible value widens it with
//   `as i64` (zero-extend).

/// `Variant::hash()` for an `INT` value, bit for bit:
/// `core/variant.cpp`, case INT, narrowed from `int64_t` to `uint32_t`.
pub fn hash_int(value: i64) -> u32 {
    value as u32
}

/// `String::hash()`: djb2 over the string's Unicode code points, seed
/// 5381, `hash = hash*33 + c` (`core/ustring.cpp`). A plain `for` loop
/// with no early exit, so it stays in the Aeneas subset (decision 5).
pub fn hash_string(value: &str) -> u32 {
    let mut hash_value: u32 = 5381;
    for c in value.chars() {
        hash_value = hash_value.wrapping_mul(33).wrapping_add(c as u32);
    }
    hash_value
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
    fn hash_string_empty_is_the_djb2_seed() {
        assert_eq!(hash_string(""), 5381);
    }
}
