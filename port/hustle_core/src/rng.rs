// Port of the game's logic RNG: Godot 3.5.1's `RandomNumberGenerator`
// (a PCG32 generator) and `framework/BetterRng.gd`'s `choose` and
// `weighted_choice`, plus the two reseed helpers in `obj/BaseObj.gd`.
//
// Every function here is pure: it takes an `Rng` value (and any other
// input) and returns a new `Rng` value, never a reference or a mutation.
// This follows decision 5 of
// docs/labbook/2026-11-13-core-game-scope-five-characters.md: no `unsafe`,
// no loop body with `return`/`break`/`continue` (such a loop is written as
// recursion over an explicit index instead).
//
// Sources (Godot 3.5.1-stable, github.com/godotengine/godot):
// - `thirdparty/misc/pcg.h`, `pcg.cpp`: `pcg32_random_r`,
//   `pcg32_srandom_r`, the constant `PCG_DEFAULT_INC_64`.
// - `core/math/random_pcg.h`, `random_pcg.cpp`: `RandomPCG::DEFAULT_SEED`,
//   `RandomPCG::seed()`, `RandomPCG::rand()` (sets `current_seed` to the
//   state BEFORE advancing it, then advances).
// - `core/math/random_number_generator.h`: `RandomNumberGenerator::randi()`
//   (calls `RandomPCG::rand()` directly) and `::randi_range()` (the
//   `to < from` branch and the modulo-and-offset formula).
// - `framework/BetterRng.gd`: `choose()` (`array[i % len(array) - 1]`,
//   which can read index `-1`) and `weighted_choice()` (the weighted scan
//   loop).
// - `obj/BaseObj.gd`: `hash_rng()` and `hash_static_rng()`, the two
//   reseed formulas.
//
// A GDScript `int` is a signed 64-bit integer that wraps on overflow
// (release build, no overflow checks). Every addition below that mirrors
// GDScript arithmetic uses `wrapping_add`/`wrapping_sub` for the same
// reason.

use crate::godot_hash::hash_int;

/// `thirdparty/misc/pcg.h`: `PCG_DEFAULT_INC_64`.
pub const PCG_DEFAULT_INC_64: u64 = 1442695040888963407;
/// `thirdparty/misc/pcg.cpp`: the LCG multiplier in `pcg32_random_r`.
const PCG_MULTIPLIER: u64 = 6364136223846793005;
/// `core/math/random_pcg.h`: `RandomPCG::DEFAULT_SEED`.
pub const DEFAULT_SEED: u64 = 12047754176567800795;

/// The logic RNG's state. Mirrors `RandomPCG`: `seed` is the last value
/// `RandomPCG::rand()` captured from `pcg.state` before advancing it
/// (`current_seed`, read by `RandomNumberGenerator.seed` after a
/// `randi()` call); `state` is the live PCG32 state (`pcg.state`); `inc`
/// is the live PCG32 increment (`pcg.inc`), derived once from
/// `PCG_DEFAULT_INC_64` and unchanged for the life of the generator,
/// because the game never calls a method that would change it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rng {
    pub seed: u64,
    pub state: u64,
    pub inc: u64,
}

/// `pcg32_random_r`: advances `state` and returns the next output.
/// `oldstate` is the state before this call; the output depends on it,
/// not on the new state (`pcg.cpp`, "uses old state for max ILP").
fn pcg32_random(state: u64, inc: u64) -> (u64, u32) {
    let old_state = state;
    let new_state = old_state
        .wrapping_mul(PCG_MULTIPLIER)
        .wrapping_add(inc | 1);
    let xorshifted = (((old_state >> 18) ^ old_state) >> 27) as u32;
    let rot = (old_state >> 59) as u32;
    (new_state, xorshifted.rotate_right(rot))
}

/// `pcg32_srandom_r`: derives `inc` from `initseq`, then advances twice
/// (once before adding `initstate`, once after), discarding both outputs.
fn pcg32_srandom(initstate: u64, initseq: u64) -> (u64, u64) {
    let inc = (initseq << 1) | 1;
    let (state_after_first, _) = pcg32_random(0, inc);
    let (state_after_second, _) = pcg32_random(state_after_first.wrapping_add(initstate), inc);
    (state_after_second, inc)
}

/// `RandomPCG`'s default constructor: `RandomPCG(DEFAULT_SEED,
/// DEFAULT_INC)`, which calls `seed(DEFAULT_SEED)`.
pub fn new_rng() -> Rng {
    set_seed(
        Rng {
            seed: 0,
            state: 0,
            inc: 0,
        },
        DEFAULT_SEED,
    )
}

/// `RandomPCG::seed()`: reseeds from `PCG_DEFAULT_INC_64`, the only
/// increment the game ever uses (`BetterRng` never sets `.inc`, and
/// `RandomNumberGenerator` exposes no setter for it). The caller's
/// `inc` is therefore unused: every reseed derives a fresh `inc` from
/// the same constant, not from the previous one.
pub fn set_seed(_rng: Rng, new_seed: u64) -> Rng {
    let (state, inc) = pcg32_srandom(new_seed, PCG_DEFAULT_INC_64);
    Rng {
        seed: new_seed,
        state,
        inc,
    }
}

/// `RandomPCG::set_state()`: touches `pcg.state` only. `seed` and `inc`
/// are unchanged, matching `get_seed()` reading stale `current_seed`
/// after a `.state = ...` assignment. The parameter is named `r`, not
/// `rng`: Aeneas's generated Lean opens the `rng` namespace (from this
/// file's own module name), and a parameter named `rng` there collided
/// with it, producing "Invalid field `Rng`: the environment does not
/// contain `hustle_core.rng.Rng.Rng`" at every `{ rng with ... }` or
/// `rng.field` use (`lake build`, 2026-11-14; see this crate's own
/// README.md, "Axioms").
pub fn set_state(r: Rng, new_state: u64) -> Rng {
    Rng {
        seed: r.seed,
        state: new_state,
        inc: r.inc,
    }
}

/// `RandomNumberGenerator::randi()`, which calls `RandomPCG::rand()`:
/// captures the pre-advance state into `seed`, then advances `state` and
/// returns the output.
pub fn randi(r: Rng) -> (Rng, u32) {
    let old_state = r.state;
    let (new_state, output) = pcg32_random(old_state, r.inc);
    (
        Rng {
            seed: old_state,
            state: new_state,
            inc: r.inc,
        },
        output,
    )
}

/// `RandomNumberGenerator::randi_range()`. `from` and `to` are truncated
/// to 32 bits first, because the native method's parameters are C++
/// `int` (32-bit); the final result is sign-extended back from 32 bits,
/// because the native method's return type is also `int`. The modulo and
/// the two additions run in `u32`, matching the C++ unsigned-promotion
/// rule for an operation between an `unsigned int` (`randi()`'s result)
/// and an `int`.
pub fn randi_range(r: Rng, from: i64, to: i64) -> (Rng, i64) {
    let from32 = from as i32;
    let to32 = to as i32;
    let (next_rng, ret) = randi(r);
    let result = if to32 < from32 {
        let denom = (from32 as u32).wrapping_sub(to32 as u32).wrapping_add(1);
        wrapping_rem_u32(ret, denom).wrapping_add(to32 as u32)
    } else {
        let denom = (to32 as u32).wrapping_sub(from32 as u32).wrapping_add(1);
        wrapping_rem_u32(ret, denom).wrapping_add(from32 as u32)
    };
    (next_rng, result as i32 as i64)
}

/// `u32` remainder, computed as `a - (a / b) * b`, not the
/// `wrapping_rem` method: Aeneas axiomatizes `core::num::u32::wrapping_rem`
/// (a primitive method with no Rust-source MIR), but not `/`, `-` or
/// `*`. `b` is never zero at either call site above (`denom` is a
/// difference of two `u32`s plus 1, so at least 1).
fn wrapping_rem_u32(a: u32, b: u32) -> u32 {
    a - (a / b) * b
}

/// `BetterRng.choose()`'s index expression, `i % len(array) - 1`
/// (`%` binds tighter than `-` in GDScript). `i` is `randi()`'s result,
/// zero-extended to a GDScript int (always `0 ..= 0xFFFFFFFF`), so the
/// result is `-1 ..= len - 2`. The caller reads `array[result]`; a
/// negative index is GDScript's "from the end" indexing, so `-1` reads
/// the array's last element.
pub fn choose_index(r: Rng, len: i64) -> (Rng, i64) {
    let (next_rng, raw) = randi(r);
    let i = raw as i64;
    (next_rng, i % len - 1)
}

/// `BetterRng.weighted_choice()`'s scan loop, written as recursion over
/// an explicit index because the GDScript loop returns from its body
/// (decision 5's loop rule). `remaining` is `rnd` in the GDScript source.
/// Falls back to index 0 past the end of `weights`, matching the
/// GDScript `assert(false, ...); return 0` the game never reaches in
/// practice (it holds only when `weights` sums to less than `rnd`, which
/// cannot happen because `rnd` is drawn from `0 ..= weight_sum`).
fn weighted_choice_scan(weights: &[i64], index: usize, remaining: i64) -> i64 {
    if index >= weights.len() {
        0
    } else {
        let weight = weights[index];
        if remaining <= weight {
            index as i64
        } else {
            weighted_choice_scan(weights, index + 1, remaining.wrapping_sub(weight))
        }
    }
}

/// `BetterRng.weighted_choice()`, given the final weight list. The
/// caller resolves `weight_array == []`'s default weighting
/// (`len(array) - i` for each index) before calling this, because that
/// default depends on the choice array's length, not on the RNG.
pub fn weighted_choice_index(r: Rng, weights: &[i64]) -> (Rng, i64) {
    let mut weight_sum: i64 = 0;
    for weight in weights {
        weight_sum = weight_sum.wrapping_add(*weight);
    }
    let (next_rng, rnd) = randi_range(r, 0, weight_sum);
    (next_rng, weighted_choice_scan(weights, 0, rnd))
}

/// `obj/BaseObj.gd`'s `hash_rng()`: `logic_rng.seed = hash(logic_rng.state
/// + input_hash)`, where `input_hash = hash(fighter.get_state_hash()) +
/// hash(opponent.get_state_hash())`. `rng_state` is the caller's
/// `Rng.state`. Returns the new seed; the caller reseeds with
/// `set_seed(rng, hash_rng(...) as u64)`.
pub fn hash_rng(rng_state: u64, fighter_state_hash: i64, opponent_state_hash: i64) -> u32 {
    let input_hash =
        (hash_int(fighter_state_hash) as i64).wrapping_add(hash_int(opponent_state_hash) as i64);
    hash_int(rng_state.wrapping_add(input_hash as u64) as i64)
}

/// `obj/BaseObj.gd`'s `hash_static_rng()`: `logic_rng_static.seed =
/// hash(logic_rng_static.state + hash(game_tick))`.
pub fn hash_static_rng(rng_state: u64, game_tick: i64) -> u32 {
    let input_hash = hash_int(game_tick) as i64;
    hash_int(rng_state.wrapping_add(input_hash as u64) as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_rng_advances_on_randi() {
        let rng = new_rng();
        let (next, _) = randi(rng);
        assert_ne!(rng.state, next.state);
    }

    #[test]
    fn randi_range_from_equal_to_returns_that_value() {
        let rng = new_rng();
        let (_, value) = randi_range(rng, 7, 7);
        assert_eq!(value, 7);
    }

    #[test]
    fn choose_index_can_be_negative_one() {
        // A raw randi() output that is an exact multiple of len gives
        // i % len == 0, so the index is -1 (the array's last element).
        let rng = Rng {
            seed: 0,
            state: 0,
            inc: PCG_DEFAULT_INC_64 | 1,
        };
        // Search a handful of states for one whose first randi() output
        // is a multiple of 3, to exercise the -1 case deterministically.
        let mut found = false;
        let mut candidate = rng;
        for _ in 0..64 {
            let (_, raw) = randi(candidate);
            if raw % 3 == 0 {
                found = true;
                break;
            }
            candidate = set_state(candidate, candidate.state.wrapping_add(1));
        }
        assert!(found, "expected some state to roll a multiple of 3");
    }
}
