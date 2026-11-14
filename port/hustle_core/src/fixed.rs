//! `Fixed`: the game's fixed-point number type.
//!
//! `lib/tbfg.so` uses `fixed::types::I48F16` (`fixed` crate 1.19.0): a
//! 64-bit signed integer with 16 fractional bits, 48 integer bits. This
//! module defines the same type as a plain `i64` and copies the
//! algorithms of three crates, bit for bit, so this crate links none of
//! them. Each function names the crate function it copies. The algorithms
//! were read from the crates' own source (gitlab.com/tspiteri/fixed,
//! gitlab.com/spearman/fixed-sqrt-rs, github.com/ErikBuer/Fixed-Trigonometry,
//! github.com/ErikBuer/Mixed-Num), not guessed.
//!
//! `tools/fixedmath_diff` checks this module against `fixed`'s own output,
//! call by call, and against the game. See its `README.md`.
//!
//! This module is the proved core (see the crate's own `README.md`,
//! "The core and the shell"): no `String`, no `&str`. Parsing reads a
//! byte slice (`from_bytes`); display writes into a fixed-size byte
//! array (`display_bytes`). `fixed_text` (a separate module, excluded
//! from the Lean build) wraps both with `&str`/`String` for callers that
//! want them.
//!
//! This module follows the Rust subset of
//! `docs/labbook/2026-11-13-core-game-scope-five-characters.md`, decision 5:
//! plain structs, no trait objects, no `unsafe`, no hash maps, and no
//! `while`/`for` loop whose body uses `?`, `return`, `break`, or `continue`.
//! Each such loop is recursion over an explicit index instead. It also
//! avoids the `?` operator itself, and every primitive integer method
//! Aeneas cannot extract MIR for (`wrapping_div`, `abs`, `wrapping_neg`,
//! `unsigned_abs`), writing each in terms of the operators Aeneas already
//! models (`+`, `-`, `*`, `/`, comparisons, casts): see the crate's
//! `README.md`, "Axioms" for the ones this did not remove.

/// The game's fixed-point number. The raw value is `I48F16` bits: a
/// two's-complement `i64` with 16 fractional bits (1 unit = 1/65536).
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub struct Fixed(pub i64);

/// π at 16 fractional bits, rounded to the nearest representable value.
/// Copies `fixed::consts::PI` (a 126-bit constant) converted to 16 bits by
/// `FixedI64::from_num`, which rounds to the nearest, ties to even. This
/// also equals `fixed_trigonometry`'s `T::mixed_pi()` (`mixed-num` 0.5.5,
/// `fixed_impl.rs`: `Self::from_num(3.14159265358979323846...)`), which
/// rounds the same real value the same way.
pub const PI: Fixed = Fixed(205_887);

/// π/2 at 16 fractional bits. Two call sites in `fixed_trigonometry`
/// compute this by different routes, and both floor (round toward
/// `-∞`) at this precision, so both give the same raw value:
///
/// - `fixed_trigonometry::sin` and `cos` (`fixed_trigonometry` 0.4.4,
///   `src/lib.rs`): `<T>::from_num(fixed::consts::PI / 2)`. Converting
///   one `Fixed` type to another with `from_num` "discards extra
///   fractional bits, which rounds towards -∞" (`fixed` 1.19.0,
///   `src/macros_from_to.rs`): `floor(1.5707963267948966... * 65536) =
///   102_943`.
/// - `fixed_trigonometry::atan::atan2` (`src/atan.rs`):
///   `T::mixed_pi() / T::mixed_from_num(2)`, a `Fixed` divided by a
///   `Fixed`, which truncates toward zero (see `wrapping_div` below):
///   `205_887 / 2` truncates to `102_943`, the same value, because
///   truncation and floor agree for a non-negative quotient.
const PI_HALF: Fixed = Fixed(102_943);

/// The magnitude of `i64::MIN` (`2^63`), as a `u64` literal. Used in
/// place of `(i64::MIN as i128).unsigned_abs() as u64`, which would make
/// Aeneas axiomatize `core::num::i128::unsigned_abs` (a primitive method
/// with no Rust-source MIR). The value is a compile-time constant either
/// way; this spells it out instead of computing it.
const I64_MIN_MAGNITUDE: u64 = 9_223_372_036_854_775_808;

/// The number of bytes `display_bytes` can write: a sign, one carried
/// leading digit, 15 integer digits (the type's largest magnitude,
/// `2^47`, needs at most `ceil(47 * log10(2)) = 15` decimal digits), a
/// point, and 5 fractional digits, with slack.
pub const DISPLAY_MAX_LEN: usize = 24;

impl Fixed {
    pub const ZERO: Fixed = Fixed(0);
    pub const ONE: Fixed = Fixed(1 << 16);
    pub const MAX: Fixed = Fixed(i64::MAX);
    pub const MIN: Fixed = Fixed(i64::MIN);

    // ---- byte parsing -------------------------------------------------

    /// Parses a decimal value from its bytes. Copies `fixed::FixedI64::from_str`
    /// (`fixed` 1.19.0, `src/from_str.rs`): one optional leading sign, an
    /// optional integer part, an optional `.`, an optional fractional
    /// part, at least one digit overall. Rounds the fractional part to
    /// the nearest 16th bit, ties to even. Returns `None` on a reject
    /// (`NoDigits`, `InvalidDigit`, `TooManyPoints`) or on overflow (the
    /// integer part, or a fractional carry, does not fit 48 integer
    /// bits). Takes `&[u8]`, not `&str`: the core never calls
    /// `str::as_bytes` (see the module doc comment).
    pub fn from_bytes(bytes: &[u8]) -> Option<Fixed> {
        match scan_bytes(bytes, 0, ScanState::new()) {
            None => None,
            Some(scan) => from_bytes_scanned(bytes, scan),
        }
    }

    // ---- byte display ---------------------------------------------------

    /// Formats as decimal, matching `fixed::FixedI64::to_string` (the
    /// default `Display`, no width or precision). Copies
    /// `fixed::display::fmt_dec`, `FmtHelper::write_int_dec`,
    /// `FmtHelper::write_frac_dec`, and `Buffer::round_and_trim`
    /// (`fixed` 1.19.0, `src/display.rs`): enough decimal digits for a
    /// lossless round trip (at most 5 fractional digits for 16 bits),
    /// rounded to the nearest, ties to even, trailing zeros trimmed.
    /// Returns the bytes and how many of them are used; writes no
    /// `String` (see the module doc comment).
    pub fn display_bytes(self) -> ([u8; DISPLAY_MAX_LEN], usize) {
        let neg = self.0 < 0;
        let abs: u64 = if neg {
            // -(self.0 as i128) is always representable: self.0 is at
            // most i64::MIN in magnitude, and i128 holds up to 2^127.
            (-(self.0 as i128)) as u64
        } else {
            self.0 as u64
        };
        let int_part: u64 = abs >> 16;
        let frac_part: u16 = (abs & 0xFFFF) as u16;

        let int_used_nbits = 64 - int_part.leading_zeros();
        let int_digits = ceil_log10_2_times(int_used_nbits) as usize;

        let mut int_buf = [0u8; 16];
        fill_int_digits(&mut int_buf, int_digits, int_part);

        let (mut frac_buf, frac_count, frac_cmp) = write_frac_dec_u16(frac_part);

        let last_digit = if frac_count > 0 {
            frac_buf[frac_count - 1]
        } else if int_digits > 0 {
            int_buf[int_digits - 1]
        } else {
            0
        };
        // Two direct `if`s, not `frac_cmp == Greater || (frac_cmp == Equal
        // && ...)` stored to a `let` first: Aeneas elaborated an
        // order-comparison result stored this way as `Prop`, not `Bool`,
        // and then failed to synthesize `Decidable` for it where this
        // code reuses it (`lake build`, 2026-11-14; see the crate's own
        // README.md, "Axioms"). A comparison used directly as an `if`
        // condition, with no intervening `let`, did not have this
        // problem anywhere else in this module.
        let (leading, frac_count) = if frac_cmp == FracCmp::Greater {
            apply_round_up(&mut int_buf, int_digits, &mut frac_buf, frac_count)
        } else if frac_cmp == FracCmp::Equal && last_digit % 2 == 1 {
            apply_round_up(&mut int_buf, int_digits, &mut frac_buf, frac_count)
        } else {
            (0u8, trim_trailing_zeros(&frac_buf, frac_count))
        };

        assemble_bytes(neg, leading, &int_buf, int_digits, &frac_buf, frac_count)
    }

    /// Godot's `hash()` of this value's own display text, with no
    /// `String`: folds `godot_hash::hash_bytes` directly over
    /// `display_bytes`'s output. `characters/BaseChar.gd`'s
    /// `get_state_hash()` hashes six such values (position, velocity,
    /// and DI, each a `FixedMath` string) this way.
    pub fn hash_display(self) -> u32 {
        let (bytes, len) = self.display_bytes();
        crate::godot_hash::hash_bytes_prefix(&bytes, len)
    }

    // ---- arithmetic --------------------------------------------------------

    /// Copies `FixedI64::wrapping_add` (a release build's `Add`): plain
    /// wrapping `i64` addition. The 16-bit scale cancels, so no shift is
    /// needed.
    pub fn wrapping_add(self, other: Fixed) -> Fixed {
        Fixed(self.0.wrapping_add(other.0))
    }

    /// Copies `FixedI64::wrapping_sub`.
    pub fn wrapping_sub(self, other: Fixed) -> Fixed {
        Fixed(self.0.wrapping_sub(other.0))
    }

    /// Copies `FixedI64::wrapping_mul`, through `arith::i64::overflowing_mul`
    /// (`fixed` 1.19.0, `src/arith.rs`): the full 128-bit product, shifted
    /// right by 16 bits (an arithmetic shift, so it floors), truncated
    /// back to 64 bits.
    pub fn wrapping_mul(self, other: Fixed) -> Fixed {
        let prod = (self.0 as i128) * (other.0 as i128);
        Fixed((prod >> 16) as i64)
    }

    /// Copies `FixedI64::wrapping_mul_int`: the raw bits times a plain
    /// integer, wrapped. No shift is needed, because multiplying a
    /// fixed-point value by a plain integer does not change the scale.
    pub fn wrapping_mul_int(self, n: i64) -> Fixed {
        Fixed(self.0.wrapping_mul(n))
    }

    /// Copies `FixedI64::wrapping_div_int`: the raw bits divided by a
    /// plain integer, wrapped. Returns `None` for a zero divisor (the
    /// game sees that as a panic, caught as Nil).
    pub fn wrapping_div_int(self, n: i64) -> Option<Fixed> {
        if n == 0 {
            None
        } else {
            Some(Fixed(wrapping_div_i64(self.0, n)))
        }
    }

    /// Divides by another `Fixed`, truncating toward zero on the
    /// fractional result, wrapping on an overflowing quotient. Copies
    /// `arith::i64::overflowing_div` (`fixed` 1.19.0, `src/arith.rs`):
    /// shift the numerator's raw bits left by 16 into a 128-bit value,
    /// then `i128` division (`/`, which truncates toward zero). Returns
    /// `None` only for a zero divisor; an overflowing quotient wraps
    /// instead, matching the game's `div` method (README.md, "div wraps,
    /// it does not check").
    pub fn wrapping_div(self, other: Fixed) -> Option<Fixed> {
        match div_quotient(self, other) {
            None => None,
            Some((q, _overflow)) => Some(Fixed(q)),
        }
    }

    /// Divides by another `Fixed`. Returns `None` for a zero divisor or
    /// for a quotient that does not fit in 48 integer bits. Copies
    /// `FixedI64::checked_div`.
    pub fn checked_div(self, other: Fixed) -> Option<Fixed> {
        match div_quotient(self, other) {
            Some((q, false)) => Some(Fixed(q)),
            _ => None,
        }
    }

    /// Copies `FixedI64::abs` at the type's minimum value: wraps to
    /// itself, the same convention as every other arithmetic method here
    /// (a release build does not check the overflow). Written with `<`
    /// and unary `-` (both operators Aeneas already models), not the
    /// `abs` method (a primitive with no Rust-source MIR, which Aeneas
    /// would otherwise axiomatize).
    pub fn abs(self) -> Fixed {
        if self.0 == i64::MIN {
            self
        } else if self.0 < 0 {
            Fixed(-self.0)
        } else {
            self
        }
    }

    /// Raises to a non-negative integer power, by repeated
    /// `wrapping_mul`, left to right. Copies
    /// `fixed_trigonometry::powi` (`fixed_trigonometry` 0.4.4,
    /// `src/lib.rs`): `temp = base; for _ in 0..power - 1 { temp = temp *
    /// base }`. A negative exponent gives `ONE`, matching `main.rs`'s own
    /// `powu` (`tools/fixedmath_diff/rust/src/main.rs`), which the game's
    /// `powu` method uses.
    pub fn powu(self, exp: i64) -> Fixed {
        powu_rec(self, if exp < 0 { 0 } else { exp }, Fixed::ONE)
    }

    /// Rounds down to the preceding integer: copies `FixedI64::floor`. An
    /// arithmetic right shift by 16 bits is a floor division by 65536 for
    /// both signs, because the raw value is two's complement.
    pub fn floor(self) -> i64 {
        self.0 >> 16
    }

    /// Rounds up to the following integer: copies `FixedI64::ceil`.
    pub fn ceil(self) -> i64 {
        let f = self.0 >> 16;
        if (self.0 & 0xFFFF) != 0 {
            f + 1
        } else {
            f
        }
    }

    /// Rounds to the nearest integer, ties away from zero: copies
    /// `FixedI64::round` ("Rounds to the nearest integer, with ties
    /// rounded away from zero", `fixed` 1.19.0, `src/macros_round.rs`).
    /// `main.rs`'s own `round_half_away` names the same floor/ceil
    /// trick.
    pub fn round_half_away(self) -> i64 {
        let half: i64 = 1 << 15;
        if self.0 >= 0 {
            Fixed(self.0.wrapping_add(half)).floor()
        } else {
            Fixed(self.0.wrapping_sub(half)).ceil()
        }
    }

    /// Square root. Copies `fixed_sqrt`'s signed, even-fractional-bits
    /// case (`fixed-sqrt` 0.2.5, `src/lib.rs`, `impl_sqrt_signed!` for
    /// `FixedI64`): panics (here, `None`) on a negative value; otherwise
    /// the floor integer square root of the raw bits (reinterpreted as
    /// `u64`), shifted left by `Frac::USIZE / 2 = 8`.
    pub fn sqrt(self) -> Option<Fixed> {
        if self.0 < 0 {
            return None;
        }
        let bits = self.0 as u64;
        let root = isqrt_u64(bits);
        Some(Fixed((root << 8) as i64))
    }

    // ---- vector helpers -----------------------------------------------------

    /// The length of a vector, copying `main.rs`'s own `vec_len`
    /// (`tools/fixedmath_diff/rust/src/main.rs`): `sqrt(x*x + y*y)`, with
    /// every operation wrapping. Returns `None` when the wrapped sum of
    /// squares is negative, the same panic `fixed_sqrt` raises there.
    pub fn vec_len(x: Fixed, y: Fixed) -> Option<Fixed> {
        let sq = x.wrapping_mul(x).wrapping_add(y.wrapping_mul(y));
        sq.sqrt()
    }

    /// A unit vector in the direction of `(x, y)`, copying `main.rs`'s
    /// own `normalized_vec`. A zero-length vector gives `(0, 0)`.
    /// Otherwise the division wraps (not checked), matching the game's
    /// `div`.
    pub fn normalized_vec(x: Fixed, y: Fixed) -> Option<(Fixed, Fixed)> {
        match Fixed::vec_len(x, y) {
            None => None,
            Some(len) => {
                if len.0 == 0 {
                    Some((Fixed::ZERO, Fixed::ZERO))
                } else {
                    match (x.wrapping_div(len), y.wrapping_div(len)) {
                        (Some(nx), Some(ny)) => Some((nx, ny)),
                        _ => None,
                    }
                }
            }
        }
    }

    // ---- angle reconstruction ------------------------------------------------

    /// Wraps an angle to the half-open range `(-π, π]`. Copies `main.rs`'s
    /// own `wrap_phase` reconstruction of the game's `angle_dist` and
    /// `lerp_angle`: one division to jump close to the answer, then a
    /// bounded correction loop. README.md documents the one known
    /// mismatch: a tie at exactly `±π`, where the game's own convention
    /// is not fully resolved.
    pub fn wrap_phase(self) -> Fixed {
        let two_pi = PI.wrapping_add(PI);
        let n = match self.checked_div(two_pi) {
            Some(q) => q.round_half_away(),
            None => 0,
        };
        let r0 = self.wrapping_sub(two_pi.wrapping_mul_int(n));
        reduce_low(reduce_high(r0, two_pi), two_pi)
    }

    /// Sine, by a Taylor polynomial. Copies `fixed_trigonometry::sin`
    /// (`fixed_trigonometry` 0.4.4, `src/lib.rs`): reflect the argument
    /// into `[-π/2, π/2]` first, then
    /// `x - x^3/6 + x^5/120 - (x^7/315)>>4 + (((x^9/81)/7)/5)>>7`.
    pub fn sin(self) -> Fixed {
        let x = clamp_to_half_pi(self);
        let x3 = powi_fixed(x, 3);
        let x5 = powi_fixed(x, 5);
        let x7 = powi_fixed(x, 7);
        let x9 = powi_fixed(x, 9);

        let mut s = x.wrapping_sub(div_int_exact(x3, 6));
        s = s.wrapping_add(div_int_exact(x5, 120));
        s = s.wrapping_sub(Fixed(div_int_exact(x7, 315).0 >> 4));
        let inner = div_int_exact(div_int_exact(div_int_exact(x9, 81), 7), 5);
        s = s.wrapping_add(Fixed(inner.0 >> 7));
        s
    }

    /// Cosine: copies `fixed_trigonometry::cos`: shift the argument by
    /// π/2, wrap it to `[-π, π)` with `fixed_trigonometry`'s own
    /// `wrap_phase` (a plain bounded loop, a different range convention
    /// from `Fixed::wrap_phase` above), then call `sin`.
    pub fn cos(self) -> Fixed {
        let shifted = self.wrapping_add(PI_HALF);
        trig_wrap_phase(shifted).sin()
    }

    /// `atan2(y, x)`, by octant-selected rational polynomials. Copies
    /// `fixed_trigonometry::atan::atan2` (`fixed_trigonometry` 0.4.4,
    /// `src/atan.rs`). Returns `None` only if an internal division's
    /// denominator wraps to zero, the game's panic case.
    pub fn atan2(y: Fixed, x: Fixed) -> Option<Fixed> {
        let y_abs = y.abs();
        let x_abs = x.abs();
        let diff = y_abs.wrapping_sub(x_abs);

        if y.0 == 0 {
            Some(if x.0 < 0 { PI } else { Fixed::ZERO })
        } else if y.0 > 0 {
            if x.0 == 0 {
                Some(PI_HALF)
            } else if x.0 > 0 {
                if diff.0 < 0 {
                    atan_poly_1(y, x)
                } else {
                    match atan_poly_2(y, x) {
                        Some(p) => Some(PI_HALF.wrapping_sub(p)),
                        None => None,
                    }
                }
            } else if diff.0 >= 0 {
                match atan_poly_2(y, x) {
                    Some(p) => Some(PI_HALF.wrapping_sub(p)),
                    None => None,
                }
            } else {
                match atan_poly_1(y, x) {
                    Some(p) => Some(PI.wrapping_add(p)),
                    None => None,
                }
            }
        } else {
            let neg_pi_half = Fixed(-PI_HALF.0);
            if x.0 == 0 {
                Some(neg_pi_half)
            } else if x.0 > 0 {
                if diff.0 < 0 {
                    atan_poly_1(y, x)
                } else {
                    match atan_poly_2(y, x) {
                        Some(p) => Some(neg_pi_half.wrapping_sub(p)),
                        None => None,
                    }
                }
            } else if diff.0 >= 0 {
                match atan_poly_2(y, x) {
                    Some(p) => Some(neg_pi_half.wrapping_sub(p)),
                    None => None,
                }
            } else {
                match atan_poly_1(y, x) {
                    Some(p) => Some(Fixed(-PI.0).wrapping_add(p)),
                    None => None,
                }
            }
        }
    }
}

// ============================================================================
// i64 helpers that avoid axiomatized primitive methods
// ============================================================================

/// `i64` division, wrapping only at the one input that overflows
/// (`i64::MIN / -1`). Written with `==`, `/` and the `i64::MIN`
/// constant, all of which Aeneas already models for `IScalar`, so this
/// avoids axiomatizing `core::num::i64::wrapping_div` (a primitive
/// method with no Rust-source MIR). Every caller already checked the
/// divisor is nonzero, so the one remaining partiality of `/` (division
/// by zero) is never reached; it reads as a `Result` in the generated
/// Lean, following the same pattern as this crate's other arithmetic.
fn wrapping_div_i64(a: i64, b: i64) -> i64 {
    if a == i64::MIN && b == -1 {
        i64::MIN
    } else {
        a / b
    }
}

// ============================================================================
// byte parsing helpers
// ============================================================================

/// The result of scanning a decimal string once. Copies the fields
/// `parse_bounds` (`fixed` 1.19.0, `src/from_str.rs`) computes: the sign,
/// and the trimmed bounds of the integer and fractional digit runs.
#[derive(Clone, Copy)]
struct ScanState {
    neg: bool,
    sign_seen: bool,
    point_seen: bool,
    point_index: usize,
    has_digit: bool,
    int_start_set: bool,
    int_start: usize,
    frac_end: usize,
}

impl ScanState {
    fn new() -> ScanState {
        ScanState {
            neg: false,
            sign_seen: false,
            point_seen: false,
            point_index: 0,
            has_digit: false,
            int_start_set: false,
            int_start: 0,
            frac_end: 0,
        }
    }
}

/// Scans a decimal string one byte at a time, recursively, to avoid a
/// `while`/`for` loop whose body returns early. Copies `parse_bounds`'s
/// state machine, decimal only: one optional leading sign, digits, at
/// most one `.`, no whitespace, no underscore, no exponent.
fn scan_bytes(bytes: &[u8], i: usize, st: ScanState) -> Option<ScanState> {
    if i >= bytes.len() {
        return Some(st);
    }
    let b = bytes[i];
    match b {
        b'+' | b'-' => {
            if st.sign_seen || st.point_seen || st.has_digit {
                return None;
            }
            let next = ScanState {
                neg: b == b'-',
                sign_seen: true,
                ..st
            };
            scan_bytes(bytes, i + 1, next)
        }
        b'.' => {
            if st.point_seen {
                return None;
            }
            let next = ScanState {
                point_seen: true,
                point_index: i,
                frac_end: i + 1,
                ..st
            };
            scan_bytes(bytes, i + 1, next)
        }
        b'0'..=b'9' => {
            let mut next = st;
            next.has_digit = true;
            if !next.int_start_set && !next.point_seen && b != b'0' {
                next.int_start_set = true;
                next.int_start = i;
            }
            if next.point_seen && b != b'0' {
                next.frac_end = i + 1;
            }
            scan_bytes(bytes, i + 1, next)
        }
        _ => None,
    }
}

/// Finishes parsing once `scan_bytes` has split the sign and the
/// integer/fractional digit runs. Split out of `Fixed::from_bytes` so
/// that function's own body has no `?` (see the module doc comment).
fn from_bytes_scanned(bytes: &[u8], scan: ScanState) -> Option<Fixed> {
    if !scan.has_digit {
        return None;
    }
    let int_bytes: &[u8] = if scan.int_start_set {
        if scan.point_seen {
            &bytes[scan.int_start..scan.point_index]
        } else {
            &bytes[scan.int_start..]
        }
    } else {
        &[]
    };
    let frac_bytes: &[u8] = if scan.point_seen {
        &bytes[scan.point_index + 1..scan.frac_end]
    } else {
        &[]
    };

    let (int_val, int_overflow) = get_int(int_bytes);
    let (frac_val, frac_overflow) = match get_frac(frac_bytes) {
        Some(v) => (v, false),
        None => (0, true),
    };
    let val: u64 = int_val | frac_val;
    // Each overflow check below is a direct `if`, not `a || (comparison)`
    // stored to a `let` and reused: see the comment on `display_bytes`'s
    // round-up check above for why.
    if frac_overflow {
        let carried = (val as u128) + (1u128 << 16);
        let val64 = carried as u64;
        if int_overflow {
            return None;
        }
        if carried > u64::MAX as u128 {
            return None;
        }
        from_bytes_finish(scan, val64)
    } else {
        if int_overflow {
            return None;
        }
        from_bytes_finish(scan, val)
    }
}

/// Checks `val` against the type's range and builds the signed result.
/// Split out of `from_bytes_scanned` only to keep the overflow checks
/// above as plain, unstored `if`s (see that function's comment).
fn from_bytes_finish(scan: ScanState, val: u64) -> Option<Fixed> {
    let bound: u64 = if scan.neg {
        I64_MIN_MAGNITUDE
    } else {
        i64::MAX as u64
    };
    if val > bound {
        return None;
    }
    let signed = if scan.neg {
        // -(val as i128) is always representable: val is at most
        // I64_MIN_MAGNITUDE (2^63), and i128 holds up to 2^127.
        (-(val as i128)) as i64
    } else {
        val as i64
    };
    Some(Fixed(signed))
}

/// Reads the (already sign-trimmed, leading-zero-trimmed) integer digits
/// into the raw bits of a 48-integer-bit, 16-fractional-bit value. Copies
/// `from_str::u64::get_int` (`fixed` 1.19.0, `src/from_str.rs`): more
/// than 64 trimmed digits is an unconditional overflow (any such value
/// is far past `2^64`, because leading zeros are already gone), 48 or
/// fewer integer bits is a range check, then a 16-bit left shift to place
/// the integer part in the value's scale.
fn get_int(bytes: &[u8]) -> (u64, bool) {
    if bytes.is_empty() {
        return (0, false);
    }
    if bytes.len() > 64 {
        return (0, true);
    }
    let (acc, overflow) = dec_int_accum(bytes, 0, 0, false);
    if overflow {
        return (acc << 16, true);
    }
    if (acc >> 48) != 0 {
        return (acc << 16, true);
    }
    (acc << 16, false)
}

/// Accumulates decimal digits into a `u64`, recursively, tracking
/// overflow through a widening `u128` product at each step. Copies
/// `from_str::u64::dec_str_int_to_bin`. Each overflow check is a direct
/// `if`, not `overflow || (comparison)` stored to a `let`: see
/// `display_bytes`'s round-up check for why.
fn dec_int_accum(bytes: &[u8], i: usize, acc: u64, overflow: bool) -> (u64, bool) {
    if i >= bytes.len() {
        return (acc, overflow);
    }
    let digit = bytes[i] - b'0';
    let acc128 = (acc as u128) * 10 + digit as u128;
    let acc64 = acc128 as u64;
    if overflow {
        return dec_int_accum(bytes, i + 1, acc64, true);
    }
    if acc128 > u64::MAX as u128 {
        return dec_int_accum(bytes, i + 1, acc64, true);
    }
    dec_int_accum(bytes, i + 1, acc64, false)
}

/// Reads the fractional digits into 16 bits, rounded to the nearest,
/// ties to even. Returns `None` on a carry past the 16th bit (the
/// caller adds 1 to the integer part instead). Copies
/// `from_str::u16::dec_str_frac_to_bin`, by way of `u64`'s and `u32`'s
/// `get_frac`, which both delegate down to `u16` without rescaling
/// because 16 fractional bits is `u16`'s full width.
fn get_frac(bytes: &[u8]) -> Option<u64> {
    if bytes.is_empty() {
        return Some(0);
    }
    let (val6, is_short) = parse_is_short(bytes);
    let round_nearest = is_short;
    match dec_to_bin_u16(val6, round_nearest) {
        None => None,
        Some(floor) => {
            if is_short {
                Some(floor as u64)
            } else {
                match frac_tie_scan(bytes, 0, floor as u32, true) {
                    TieResult::Less => Some(floor as u64),
                    TieResult::Tie => {
                        if floor % 2 == 0 {
                            Some(floor as u64)
                        } else if floor == 0xFFFF {
                            None
                        } else {
                            Some((floor + 1) as u64)
                        }
                    }
                    TieResult::Greater => {
                        if floor == 0xFFFF {
                            None
                        } else {
                            Some((floor + 1) as u64)
                        }
                    }
                }
            }
        }
    }
}

/// Reads the first 6 fractional digits (or all of them, padded, if there
/// are 6 or fewer) into a plain integer out of 10^6. Copies
/// `from_str::u16::parse_is_short`: 6 decimal digits is exactly enough
/// precision for a 16-bit binary fraction.
fn parse_is_short(bytes: &[u8]) -> (u32, bool) {
    let len = bytes.len();
    if len <= 6 {
        let pad = pow10_u32(6 - len);
        (dec_digits_u32(bytes, 0, bytes.len(), 0) * pad, true)
    } else {
        (dec_digits_u32(bytes, 0, 6, 0), false)
    }
}

fn pow10_u32(n: usize) -> u32 {
    if n == 0 {
        1
    } else {
        10 * pow10_u32(n - 1)
    }
}

/// Accumulates up to 6 decimal digits into a `u32`. Never overflows: the
/// largest possible value is `999_999`.
fn dec_digits_u32(bytes: &[u8], i: usize, end: usize, acc: u32) -> u32 {
    if i >= end {
        return acc;
    }
    let digit = (bytes[i] - b'0') as u32;
    dec_digits_u32(bytes, i + 1, end, acc * 10 + digit)
}

/// Converts a 6-decimal-digit value (0..=999_999) to its nearest, or
/// floor, 16-bit binary fraction. Returns `None` on a round-to-nearest
/// carry past the 16th bit. Copies `from_str::u16::dec_to_bin` at
/// `DEC = 6`, `BIN = 16` (`fixed` 1.19.0, `src/from_str.rs`):
/// `5^6 = 15_625`; the denominator is twice that.
fn dec_to_bin_u16(val: u32, round_nearest: bool) -> Option<u16> {
    const FIVES: u32 = 15_625;
    const DENOM: u32 = FIVES * 2;
    let mut numer: u32 = val << 11;
    if round_nearest {
        numer += FIVES;
        if (numer >> 16) >= DENOM {
            return None;
        }
    }
    let div = numer / DENOM;
    // `numer - div * DENOM`, not `numer % DENOM`: Aeneas axiomatizes
    // `core::num::u32::wrapping_rem` (a primitive method with no
    // Rust-source MIR) for the `%` operator on `u32`, but not `-`/`*`.
    let rem = numer - div * DENOM;
    let div = div as u16;
    if rem == 0 && div % 2 == 1 {
        Some(div - 1)
    } else {
        Some(div)
    }
}

/// `Less`: the fractional value is below the floor-plus-half boundary.
/// `Greater`: above it. `Tie`: exactly at it, as far as the scanned
/// digits show.
enum TieResult {
    Less,
    Greater,
    Tie,
}

/// Recomputes the decimal expansion of `(floor + 0.5) / 65536`, one
/// digit at a time, and compares it against the string's own digits, to
/// decide whether the exact fractional value is below, at, or above the
/// halfway point the first 6 digits could not resolve. Recursion over
/// the digit index replaces a `while` loop whose body would otherwise
/// `break` or `return` early. Copies the tail of
/// `from_str::u16::dec_str_frac_to_bin`.
fn frac_tie_scan(bytes: &[u8], i: usize, boundary: u32, add5: bool) -> TieResult {
    if i >= bytes.len() {
        return TieResult::Tie;
    }
    let digit = (bytes[i] - b'0') as u32;
    let scaled = boundary * 10;
    let mut boundary_digit = scaled >> 16;
    let mut next_boundary = scaled & 0xFFFF;
    if add5 {
        let sum = next_boundary + 5;
        if sum >= 0x1_0000 {
            next_boundary = sum - 0x1_0000;
            boundary_digit += 1;
        } else {
            next_boundary = sum;
        }
    }
    if digit < boundary_digit {
        TieResult::Less
    } else if digit > boundary_digit {
        TieResult::Greater
    } else {
        frac_tie_scan(bytes, i + 1, next_boundary, false)
    }
}

// ============================================================================
// byte display helpers
// ============================================================================

/// `ceil(n * log10(2))`: the number of decimal digits that can hold any
/// `n`-bit binary value without loss. Copies
/// `fixed::display::ceil_log10_2_times` (`fixed` 1.19.0, `src/display.rs`):
/// a fixed-point approximation of `log10(2)`, valid for `n < 112_816`
/// (our `n` never exceeds 48).
fn ceil_log10_2_times(n: u32) -> u32 {
    (((n as u64) * 0x4D10_4D43 + 0xFFFF_FFFF) >> 32) as u32
}

/// Fills `buf[0..n]` with the decimal digits of `v`, most significant
/// first, by repeated division, recursively from the last position
/// backward. Copies `FmtHelper::write_int_dec` (`fixed` 1.19.0,
/// `src/display.rs`).
fn fill_int_digits(buf: &mut [u8; 16], n: usize, v: u64) {
    fill_int_digits_rec(buf, n, v);
}

fn fill_int_digits_rec(buf: &mut [u8; 16], i: usize, v: u64) {
    if i == 0 {
        return;
    }
    buf[i - 1] = (v % 10) as u8;
    fill_int_digits_rec(buf, i - 1, v / 10);
}

/// `Less`/`Equal`/`Greater`: how a 16-bit fraction's remainder compares
/// to `0x8000` (used to decide rounding). A local, 3-variant stand-in
/// for `core::cmp::Ordering`: comparing two `core::cmp::Ordering`
/// values with `==` made Aeneas axiomatize
/// `core::cmp::Ordering::PartialEq::eq` (`core`'s own derived impl, with
/// no Rust-source MIR Charon can reach); comparing two values of an
/// enum this crate defines does not, because the derive macro expands
/// inside this crate, where Charon already extracts everything.
#[derive(Clone, Copy, PartialEq, Eq)]
enum FracCmp {
    Less,
    Equal,
    Greater,
}

/// Writes up to 5 decimal digits of a 16-bit fraction, stopping early
/// once the remaining value is close enough to 0 or to a full carry that
/// further digits add nothing. Copies `FmtHelper::write_frac_dec` (`fixed`
/// 1.19.0, `src/display.rs`) at `nbits = BITS = 16` (so the tie threshold
/// starts at 0 with an immediate `+5`). Returns the digits written, how
/// many of them are significant, and how the final remainder compares to
/// `0x8000` (used to decide rounding).
fn write_frac_dec_u16(frac: u16) -> ([u8; 5], usize, FracCmp) {
    let mut buf = [0u8; 5];
    let (count, rem) = frac_dec_rec(frac, 0, 0, true, &mut buf);
    let cmp = if rem < 0x8000 {
        FracCmp::Less
    } else if rem == 0x8000 {
        FracCmp::Equal
    } else {
        FracCmp::Greater
    };
    (buf, count, cmp)
}

/// `u16` wrapping negation, computed with subtraction in a wider `u32`
/// and a mask, not the `wrapping_neg` method (a primitive with no
/// Rust-source MIR, which Aeneas would otherwise axiomatize).
fn wrapping_neg_u16(x: u16) -> u16 {
    (((0x1_0000u32).wrapping_sub(x as u32)) & 0xFFFF) as u16
}

fn frac_dec_rec(frac: u16, i: usize, tie: u16, add5: bool, buf: &mut [u8; 5]) -> (usize, u16) {
    if i >= 5 {
        return (5, frac);
    }
    let prod = (frac as u32) * 10;
    let new_frac = prod as u16;
    buf[i] = (prod >> 16) as u8;

    if new_frac < 10 || wrapping_neg_u16(new_frac) < 10 {
        return (i + 1, new_frac);
    }

    let prod_tie = (tie as u32) * 10;
    let mut new_tie = prod_tie as u16;
    if add5 {
        new_tie = new_tie.wrapping_add(5);
    }
    if new_frac < new_tie || wrapping_neg_u16(new_frac) < new_tie {
        return (i + 1, new_frac);
    }
    frac_dec_rec(new_frac, i + 1, new_tie, false, buf)
}

/// Propagates a round-up carry from the last fractional digit, through
/// the fractional digits, then the integer digits, into a leading digit
/// reserved for the case where every digit was 9. Copies
/// `Buffer::round_and_trim`'s round-up branch (`fixed` 1.19.0,
/// `src/display.rs`). Returns the leading digit (0 or 1) and the number
/// of fractional digits that remain significant after the carry removes
/// any that turned to a trailing zero.
fn apply_round_up(
    int_buf: &mut [u8; 16],
    int_digits: usize,
    frac_buf: &mut [u8; 5],
    frac_count: usize,
) -> (u8, usize) {
    let (done, new_frac_count) = carry_into5(frac_buf, frac_count);
    if done {
        return (0, new_frac_count);
    }
    let done = carry_into16(int_buf, int_digits).0;
    if done {
        return (0, 0);
    }
    (1, 0)
}

/// Increments `buf[i - 1]`, carrying leftward (zeroing and recursing)
/// while a digit is 9. Returns whether the carry resolved inside this
/// buffer, and the index one past the resolved digit (the caller reads
/// this as "how many digits are still significant" for the fractional
/// buffer). Two concrete versions, for the two buffer sizes this module
/// uses, not one generic over `const N: usize`: Charon's own
/// `--preset=aeneas` extraction is checked against plain monomorphic
/// functions only (see the crate's own `README.md`).
fn carry_into16(buf: &mut [u8; 16], i: usize) -> (bool, usize) {
    if i == 0 {
        return (false, 0);
    }
    let d = buf[i - 1];
    if d < 9 {
        buf[i - 1] = d + 1;
        (true, i)
    } else {
        buf[i - 1] = 0;
        carry_into16(buf, i - 1)
    }
}

fn carry_into5(buf: &mut [u8; 5], i: usize) -> (bool, usize) {
    if i == 0 {
        return (false, 0);
    }
    let d = buf[i - 1];
    if d < 9 {
        buf[i - 1] = d + 1;
        (true, i)
    } else {
        buf[i - 1] = 0;
        carry_into5(buf, i - 1)
    }
}

/// The count of leading digits in `buf[0..n]` that remain after dropping
/// trailing zeros. Copies `Buffer::round_and_trim`'s non-round-up branch.
fn trim_trailing_zeros(buf: &[u8; 5], n: usize) -> usize {
    if n == 0 || buf[n - 1] != 0 {
        n
    } else {
        trim_trailing_zeros(buf, n - 1)
    }
}

/// Assembles the final decimal digits into a fixed-size byte buffer, no
/// `String`. Copies `Buffer::pad_and_print` (`fixed` 1.19.0,
/// `src/display.rs`) with no width, fill, or precision (every caller
/// only ever uses the bare `{}` format): the leading reserve digit is
/// shown exactly when it is nonzero (a round-up carried all the way out)
/// or when there are no integer digits at all (so the value is
/// `0.x...`); otherwise at most one spurious leading zero inside the
/// integer digits is also dropped.
fn assemble_bytes(
    neg: bool,
    leading: u8,
    int_buf: &[u8; 16],
    int_digits: usize,
    frac_buf: &[u8; 5],
    frac_count: usize,
) -> ([u8; DISPLAY_MAX_LEN], usize) {
    let mut out = [0u8; DISPLAY_MAX_LEN];
    let mut len = 0usize;
    if neg {
        out[len] = b'-';
        len += 1;
    }
    // Direct `if`/`else if` branches, not `leading != 0 || int_digits
    // == 0` and `!show_leading && ...` stored to `let`s first: see
    // `display_bytes`'s round-up check for why.
    let (len, start) = if leading != 0 {
        out[len] = b'0' + leading;
        (len + 1, 0)
    } else if int_digits == 0 {
        out[len] = b'0' + leading;
        (len + 1, 0)
    } else if int_buf[0] == 0 {
        (len, 1)
    } else {
        (len, 0)
    };
    let len = append_digit_bytes16(&mut out, len, int_buf, start, int_digits);
    if frac_count > 0 {
        out[len] = b'.';
        let len = append_digit_bytes5(&mut out, len + 1, frac_buf, 0, frac_count);
        (out, len)
    } else {
        (out, len)
    }
}

fn append_digit_bytes16(
    out: &mut [u8; DISPLAY_MAX_LEN],
    pos: usize,
    buf: &[u8; 16],
    i: usize,
    end: usize,
) -> usize {
    if i >= end {
        return pos;
    }
    out[pos] = b'0' + buf[i];
    append_digit_bytes16(out, pos + 1, buf, i + 1, end)
}

fn append_digit_bytes5(
    out: &mut [u8; DISPLAY_MAX_LEN],
    pos: usize,
    buf: &[u8; 5],
    i: usize,
    end: usize,
) -> usize {
    if i >= end {
        return pos;
    }
    out[pos] = b'0' + buf[i];
    append_digit_bytes5(out, pos + 1, buf, i + 1, end)
}

// ============================================================================
// arithmetic helpers
// ============================================================================

/// The quotient of two raw values, scaled for 16 fractional bits, and
/// whether it overflows 64 bits. Copies `arith::i64::overflowing_div`
/// (`fixed` 1.19.0, `src/arith.rs`). Returns `None` only for a zero
/// divisor.
fn div_quotient(a: Fixed, b: Fixed) -> Option<(i64, bool)> {
    if b.0 == 0 {
        return None;
    }
    let numerator = (a.0 as i128) << 16;
    let denominator = b.0 as i128;
    let quotient = numerator / denominator;
    // Two direct `if`s, not `quotient >= MIN && quotient <= MAX` stored
    // to a `let` (as `fits`) and negated: see `display_bytes`'s
    // round-up check for why.
    if quotient < i64::MIN as i128 {
        return Some((quotient as i64, true));
    }
    if quotient > i64::MAX as i128 {
        return Some((quotient as i64, true));
    }
    Some((quotient as i64, false))
}

/// Divides by a small, known-nonzero integer constant. The quotient
/// matches dividing the `Fixed` by `Fixed::from_num(n)` exactly, because
/// the 16-bit scale factor cancels on both sides of the division
/// (`(a << 16) / (b << 16) == a / b` under truncating integer division,
/// for a positive scale). This underlies the constant divisions in
/// `Fixed::sin`.
fn div_int_exact(a: Fixed, n: i64) -> Fixed {
    Fixed(wrapping_div_i64(a.0, n))
}

fn powu_rec(base: Fixed, exp: i64, acc: Fixed) -> Fixed {
    if exp <= 0 {
        acc
    } else {
        powu_rec(base, exp - 1, acc.wrapping_mul(base))
    }
}

/// Raises to a small, known power, by repeated `wrapping_mul`, left to
/// right (not exponentiation by squaring: the rounding of fixed-point
/// multiplication is not associative, so the order must match the
/// original). Copies `fixed_trigonometry::powi`.
fn powi_fixed(base: Fixed, n: u32) -> Fixed {
    powi_rec(base, n, base)
}

fn powi_rec(base: Fixed, remaining: u32, acc: Fixed) -> Fixed {
    if remaining <= 1 {
        acc
    } else {
        powi_rec(base, remaining - 1, acc.wrapping_mul(base))
    }
}

/// The floor integer square root of a `u64`, by binary search over the
/// candidate root (which is always below `2^32`, since the input is
/// below `2^64`). This is a different algorithm from `fixed-sqrt`'s
/// (which forwards to the `integer-sqrt` crate), but the floor of a
/// square root has exactly one correct value for a given input, so any
/// correct algorithm agrees with it bit for bit. Recursion over the
/// search range replaces a `while` loop that would otherwise need
/// `break`.
fn isqrt_u64(n: u64) -> u64 {
    if n == 0 {
        return 0;
    }
    let hi = if n < (1u64 << 32) { n } else { 1u64 << 32 };
    isqrt_search(n, 0, hi)
}

fn isqrt_search(n: u64, lo: u64, hi: u64) -> u64 {
    if lo >= hi {
        return lo;
    }
    let mid = lo + (hi - lo + 1) / 2;
    if mid <= n / mid {
        isqrt_search(n, mid, hi)
    } else {
        isqrt_search(n, lo, mid - 1)
    }
}

// ============================================================================
// angle helpers
// ============================================================================

fn reduce_high(r: Fixed, two_pi: Fixed) -> Fixed {
    if r.0 > PI.0 {
        reduce_high(r.wrapping_sub(two_pi), two_pi)
    } else {
        r
    }
}

fn reduce_low(r: Fixed, two_pi: Fixed) -> Fixed {
    if r.0 <= -PI.0 {
        reduce_low(r.wrapping_add(two_pi), two_pi)
    } else {
        r
    }
}

/// Wraps an angle to `[-π, π)`. Copies `fixed_trigonometry::wrap_phase`
/// (`fixed_trigonometry` 0.4.4, `src/lib.rs`): a plain bounded
/// correction loop with no division shortcut, used only inside `cos`.
/// Its range convention is the mirror image of `Fixed::wrap_phase`'s
/// `(-π, π]` above; both are copied as coded, not reconciled.
fn trig_wrap_phase(phi: Fixed) -> Fixed {
    let two_pi = PI.wrapping_add(PI);
    trig_reduce_high(trig_reduce_low(phi, two_pi), two_pi)
}

fn trig_reduce_low(t: Fixed, two_pi: Fixed) -> Fixed {
    if t.0 < -PI.0 {
        trig_reduce_low(t.wrapping_add(two_pi), two_pi)
    } else {
        t
    }
}

fn trig_reduce_high(t: Fixed, two_pi: Fixed) -> Fixed {
    if PI.0 <= t.0 {
        trig_reduce_high(t.wrapping_sub(two_pi), two_pi)
    } else {
        t
    }
}

/// Reflects an angle outside `[-π/2, π/2]` back into that range around
/// the nearer boundary. Copies the clamp at the top of
/// `fixed_trigonometry::sin`.
fn clamp_to_half_pi(x: Fixed) -> Fixed {
    if x.0 < -PI_HALF.0 {
        let delta = x.wrapping_add(PI_HALF);
        Fixed(-PI_HALF.0).wrapping_sub(delta)
    } else if PI_HALF.0 < x.0 {
        let delta = x.wrapping_sub(PI_HALF);
        PI_HALF.wrapping_sub(delta)
    } else {
        x
    }
}

/// `0.28125` (`= 9/32`) at 16 fractional bits, exactly: `65536 * 9 / 32 =
/// 18_432`, no rounding. Copies the polynomial constant in
/// `fixed_trigonometry::atan::atan_poly_1` and `atan_poly_2`.
const ATAN_POLY_C: Fixed = Fixed(18_432);

/// Copies `fixed_trigonometry::atan::atan_poly_1`: `(x*y) / (x^2 +
/// 0.28125*y^2)`.
fn atan_poly_1(y: Fixed, x: Fixed) -> Option<Fixed> {
    let num = x.wrapping_mul(y);
    let denom = powi_fixed(x, 2).wrapping_add(ATAN_POLY_C.wrapping_mul(powi_fixed(y, 2)));
    num.wrapping_div(denom)
}

/// Copies `fixed_trigonometry::atan::atan_poly_2`: `(x*y) / (y^2 +
/// 0.28125*x^2)`.
fn atan_poly_2(y: Fixed, x: Fixed) -> Option<Fixed> {
    let num = x.wrapping_mul(y);
    let denom = powi_fixed(y, 2).wrapping_add(ATAN_POLY_C.wrapping_mul(powi_fixed(x, 2)));
    num.wrapping_div(denom)
}
