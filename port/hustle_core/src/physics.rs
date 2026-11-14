//! `FGObject`: the game's native object physics, reconstructed.
//!
//! `lib/tbfg.so` exports `FGObject` (`bin/FGObject.gdns`) with 41
//! methods. `obj/BaseObj.gd` creates one per game object
//! (`var chara = FGObject.new()`) and every call in the game's
//! GDScript routes through it. This module is a pure reimplementation:
//! a plain `ObjectData` struct holding every field the real object
//! holds, and one function per method, each taking the old
//! `ObjectData` and returning the new one.
//!
//! The 41 methods were found by two steps, both in
//! `tools/fgobject_diff/README.md`:
//!
//! 1. `strings lib/tbfg.so | grep -oE "FGObject :: [a-zA-Z_]+"` lists
//!    every method's own panic-message name, the same way
//!    `tools/fixedmath_diff` found `FixedMath`'s 38 methods.
//! 2. Each method's formula was found by calling it from a headless
//!    Godot probe with a controlled starting state (`Investigate.gd`,
//!    not committed; see the README for the probe values and the
//!    reasoning). `tools/fgobject_diff` is the permanent, larger probe
//!    set that checks this module's result against the real call.
//!
//! This module follows the crate's Rust subset
//! (`docs/labbook/2026-11-13-core-game-scope-five-characters.md`,
//! decision 5; see also `port/hustle_core/README.md`): plain structs
//! and enums, no trait objects, no `unsafe`, every arithmetic step
//! through `fixed::Fixed`'s `wrapping_*` methods, no bare operator on
//! a `Fixed`.
//!
//! A method the real library can panic on (a zero divisor in
//! `apply_fric`, an invalid facing direction in `set_facing`) leaves
//! the object unchanged here, the same outcome `gdnative-core`'s
//! panic boundary gives the game: the call becomes Nil, and nothing
//! in the object changed. `set_facing` is the one method that
//! signals this with `Option<ObjectData>` (`None` on the invalid
//! input): every other such method just returns its own unchanged
//! `ObjectData` directly, since its one fallible step already folds
//! into a plain `if`/`match` with no other caller needing to
//! distinguish the two outcomes.

use crate::fixed::Fixed;

/// Left or right. Copies the native library's own `Facing` enum
/// (`turn_based_fighting_game_lib::obj::Facing`, found in
/// `lib/tbfg.so`'s symbol table). `get_facing()` returns this as a
/// single-key Godot dictionary (`{"Right": {}}` or `{"Left": {}}`);
/// `tools/fgobject_diff` encodes it the same way for the comparison.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Facing {
    Right,
    Left,
}

/// `facing_sign(Right) = 1`, `facing_sign(Left) = -1`. Used by the
/// `_relative` force and movement methods, which scale their `x`
/// argument by the object's own facing.
fn facing_sign(facing: Facing) -> i64 {
    if facing == Facing::Right {
        1
    } else {
        -1
    }
}

/// Every field `FGObject` holds. Field order and names follow
/// `get_data()`'s own `object_data` keys where one exists
/// (`position_x`, `position_y`, `vel_x`, `vel_y`, `facing`,
/// `grounded`, `snap_to_ground`); the rest (the physics
/// configuration, the pending-force accumulator, and the pushback
/// magnitude) are internal state `get_data()` does not expose, found
/// by probing their effect instead of reading them directly.
///
/// `position_x`/`position_y` are stored here at full `Fixed`
/// precision, not the rounded integer `get_data()` returns:
/// `get_data`'s own body rounds them on the way out (see that
/// function). `position_float`, the `Vector2`-shaped string
/// `get_data()` also returns, is not reproduced: no GDScript reads it
/// (`tools/fgobject_diff/README.md`, "Fields not ported"), so it
/// carries no risk for `step`.
#[derive(Clone, Copy, PartialEq, Debug)]
pub struct ObjectData {
    pub id: i64,
    pub position_x: Fixed,
    pub position_y: Fixed,
    pub vel_x: Fixed,
    pub vel_y: Fixed,
    pub facing: Facing,
    pub grounded: bool,
    pub snap_to_ground: bool,
    pub gravity: Fixed,
    pub gravity_modifier: Fixed,
    pub ground_friction: Fixed,
    pub air_friction: Fixed,
    pub max_ground_speed: Fixed,
    pub max_air_speed: Fixed,
    pub max_fall_speed: Fixed,
    /// The force queued by `apply_force`/`apply_force_relative`/
    /// `apply_grav`/`apply_grav_custom`, consumed and cleared by
    /// `apply_forces`/`apply_forces_no_limit`. Not part of
    /// `get_data()`'s output; found by calling `apply_force` then
    /// `apply_forces` and reading the velocity change.
    pub pending_force_x: Fixed,
    pub pending_force_y: Fixed,
    /// The magnitude `add_pushback` accumulates and `apply_pushback`
    /// spends down. Not part of `get_data()`'s output.
    pub pushback: Fixed,
    /// Whether `pushback` has already been decayed once since the
    /// last `add_pushback`. Confirmed by probing
    /// (`tools/fgobject_diff/README.md`, "apply_pushback"): three
    /// consecutive `apply_pushback` calls with no new `add_pushback`
    /// between them moved the object by the full pushback, then by
    /// `PUSHBACK_DECAY` times that, then by the *same* decayed
    /// amount again — the decay applies once per `add_pushback`, not
    /// once per `apply_pushback` call.
    pub pushback_decayed: bool,
}

/// A fresh `FGObject.new()`: facing right, grounded, snapped to the
/// ground, at rest at the origin, every physics configuration at
/// zero. `gravity_modifier` defaults to `ONE`, not zero: it is a
/// multiplier, and the game calls `set_gravity` without ever calling
/// `set_gravity_modifier` for most objects, so a zero default would
/// silently cancel gravity everywhere `apply_grav` runs.
pub fn new_object() -> ObjectData {
    ObjectData {
        id: 0,
        position_x: Fixed::ZERO,
        position_y: Fixed::ZERO,
        vel_x: Fixed::ZERO,
        vel_y: Fixed::ZERO,
        facing: Facing::Right,
        grounded: true,
        snap_to_ground: true,
        gravity: Fixed::ZERO,
        gravity_modifier: Fixed::ONE,
        ground_friction: Fixed::ZERO,
        air_friction: Fixed::ZERO,
        max_ground_speed: Fixed::ZERO,
        max_air_speed: Fixed::ZERO,
        max_fall_speed: Fixed::ZERO,
        pending_force_x: Fixed::ZERO,
        pending_force_y: Fixed::ZERO,
        pushback: Fixed::ZERO,
        pushback_decayed: false,
    }
}

/// `n << 16` as a `Fixed`, through `wrapping_mul_int` (never a bare
/// shift or multiply on the raw `i64`). The integer-argument methods
/// (`set_x`, `apply_force`, `move_directly`, and their `_relative`
/// siblings) take a plain Godot `int` and store it at this scale, the
/// same conversion `fixed::FixedI64::from_num::<i64>` performs for an
/// exact integer with no fractional bits to round.
fn fixed_from_int(n: i64) -> Fixed {
    Fixed::ONE.wrapping_mul_int(n)
}

/// Clamps `v` to `[-limit, limit]`. `apply_forces` uses this for the
/// horizontal speed cap, confirmed by probing: a force that would
/// push `vel_x` to 100 with `max_ground_speed` "15" left it at
/// exactly 15 (`tools/fgobject_diff/README.md`, "apply_forces").
fn clamp_sym(v: Fixed, limit: Fixed) -> Fixed {
    if v.0 > limit.0 {
        limit
    } else if v.0 < -limit.0 {
        Fixed(-limit.0)
    } else {
        v
    }
}

/// The ground-contact check `move_directly`, `move_directly_relative`,
/// and their two `_str` siblings apply after moving. Confirmed by
/// probing (`tools/fgobject_diff/README.md`, "The ground"): a raw Y
/// position at or past 0 (the game's downward axis, 0 being the
/// stage floor) marks the object grounded, zeroes its vertical
/// velocity, and — only when `snap_to_ground` is set — clamps the
/// position back to exactly 0. This check is one-directional: it can
/// only turn `grounded` on (landing), never off. Moving to a
/// position above the ground (Y still negative) leaves `grounded`
/// exactly as it was before the call — a real recorded case moved an
/// already-grounded object to Y = -1 and it stayed grounded.
/// `apply_forces`/`apply_forces_no_limit` use a different,
/// bidirectional rule; see `ground_clamp_bidirectional` below.
fn ground_clamp_landing_only(
    pos_y: Fixed,
    vel_y: Fixed,
    grounded: bool,
    snap_to_ground: bool,
) -> (Fixed, Fixed, bool) {
    if pos_y.0 >= 0 {
        if snap_to_ground {
            (Fixed::ZERO, Fixed::ZERO, true)
        } else {
            (pos_y, Fixed::ZERO, true)
        }
    } else {
        (pos_y, vel_y, grounded)
    }
}

/// The ground-contact check `apply_forces`/`apply_forces_no_limit`
/// apply after integrating. Unlike `ground_clamp_landing_only`, this
/// recomputes `grounded` both ways: a recorded real call left an
/// already-grounded object's Y at -195 (no snap needed, already
/// above ground) and `grounded` came back `false`
/// (`tools/fgobject_diff/README.md`, "apply_forces"). Landing
/// (`pos_y >= 0`) still zeroes `vel_y` and, with `snap_to_ground`
/// set, clamps the position to 0, the same as the landing-only rule.
fn ground_clamp_bidirectional(
    pos_y: Fixed,
    vel_y: Fixed,
    snap_to_ground: bool,
) -> (Fixed, Fixed, bool) {
    if pos_y.0 >= 0 {
        if snap_to_ground {
            (Fixed::ZERO, Fixed::ZERO, true)
        } else {
            (pos_y, Fixed::ZERO, true)
        }
    } else {
        (pos_y, vel_y, false)
    }
}

// ============================================================================
// position and velocity, raw setters (no clamp, no ground check)
// ============================================================================

pub fn set_x(o: ObjectData, x: i64) -> ObjectData {
    let mut out = o;
    out.position_x = fixed_from_int(x);
    out
}

pub fn set_y(o: ObjectData, y: i64) -> ObjectData {
    let mut out = o;
    out.position_y = fixed_from_int(y);
    out
}

pub fn set_position(o: ObjectData, x: i64, y: i64) -> ObjectData {
    set_y(set_x(o, x), y)
}

pub fn set_position_str(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let mut out = o;
    out.position_x = x;
    out.position_y = y;
    out
}

pub fn set_vel(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let mut out = o;
    out.vel_x = x;
    out.vel_y = y;
    out
}

/// `get_facing()`'s inverse. Confirmed by probing: `set_facing(0)`
/// panics (`src/obj.rs:94`, "invalid facing direction"), caught as
/// Nil, with the object's own facing left unchanged (the probe set it
/// to `Left` beforehand, and it read back `Left` afterward). Returns
/// `None` for exactly that outcome; the caller keeps the old
/// `ObjectData` on `None`, the same as every other method a zero or
/// invalid argument can fail.
pub fn set_facing(o: ObjectData, dir: i64) -> Option<ObjectData> {
    if dir == 1 {
        let mut out = o;
        out.facing = Facing::Right;
        Some(out)
    } else if dir == -1 {
        let mut out = o;
        out.facing = Facing::Left;
        Some(out)
    } else {
        None
    }
}

pub fn get_facing(o: ObjectData) -> Facing {
    o.facing
}

/// Not one of the 41 `FGObject` methods: `obj/BaseObj.gd` sets `id`
/// with a plain property assignment (`chara.id = id`), not a method
/// call. Included for completeness, so `tools/fgobject_diff`'s
/// recording proxy (which intercepts the assignment through
/// GDScript's `_set`) has a matching pure function to replay against.
pub fn set_id(o: ObjectData, id: i64) -> ObjectData {
    let mut out = o;
    out.id = id;
    out
}

pub fn set_grounded(o: ObjectData, grounded: bool) -> ObjectData {
    let mut out = o;
    out.grounded = grounded;
    out
}

pub fn is_grounded(o: ObjectData) -> bool {
    o.grounded
}

pub fn set_snap_to_ground(o: ObjectData, snap: bool) -> ObjectData {
    let mut out = o;
    out.snap_to_ground = snap;
    out
}

pub fn get_snap_to_ground(o: ObjectData) -> bool {
    o.snap_to_ground
}

/// Confirmed by probing: `grounded` is a plain `position_y >= 0`
/// check, independent of velocity or `snap_to_ground`
/// (`tools/fgobject_diff/README.md`, "update_grounded").
pub fn update_grounded(o: ObjectData) -> ObjectData {
    let mut out = o;
    if o.position_y.0 >= 0 {
        out.grounded = true;
    } else {
        out.grounded = false;
    }
    out
}

/// Rounds `vel_x` to the nearest integer, ties away from zero (the
/// same convention as `FixedMath::round`), then narrows to the low
/// 32 bits — confirmed by probing: `5.7` rounds to `6`, but the
/// type's largest value rounds to `-1`, the low 32 bits of `2^47 - 1`
/// (see `position_pixels` below for the same truncation on the
/// position fields; `tools/fixedmath_diff/README.md`, "round" shows
/// the same contract on `FixedMath::round` itself).
pub fn get_x_vel_int(o: ObjectData) -> i64 {
    (o.vel_x.round_half_away() as i32) as i64
}

/// `get_data()`'s `position_x`/`position_y`: the raw position,
/// rounded to the nearest pixel (ties away from zero, the same
/// convention as `get_x_vel_int`), then narrowed to the low 32 bits.
/// Confirmed by probing (`tools/fixedmath_diff/README.md`,
/// "round"/"ceil"/"floor" truncate their result to a 32-bit
/// integer"; the same truncation applies here): a raw position of
/// exactly `2^47 - 1` (the type's largest value, every low bit set)
/// read back as `-1`, the low 32 bits of that value read as a signed
/// `i32`. This never matters at game scale (no stage is anywhere
/// near `2^31` pixels wide), but a port that returned the
/// mathematical rounded value instead of this narrower contract would
/// diverge the first time a search or a bug produces a position
/// outside `i32` range.
pub fn position_pixels(o: ObjectData) -> (i32, i32) {
    let x = o.position_x.round_half_away();
    let y = o.position_y.round_half_away();
    ((x as i32), (y as i32))
}

pub fn reset_momentum(o: ObjectData) -> ObjectData {
    let mut out = o;
    out.vel_x = Fixed::ZERO;
    out.vel_y = Fixed::ZERO;
    out
}

// ============================================================================
// physics configuration setters
// ============================================================================

pub fn set_gravity(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.gravity = v;
    out
}

pub fn set_gravity_modifier(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.gravity_modifier = v;
    out
}

pub fn set_ground_friction(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.ground_friction = v;
    out
}

pub fn set_air_friction(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.air_friction = v;
    out
}

pub fn set_max_ground_speed(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.max_ground_speed = v;
    out
}

pub fn set_max_air_speed(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.max_air_speed = v;
    out
}

pub fn set_max_fall_speed(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.max_fall_speed = v;
    out
}

// ============================================================================
// forces: accumulate now, integrate later
// ============================================================================

/// Queues a force. `apply_forces`/`apply_forces_no_limit` consumes and
/// clears it; nothing else observes it (confirmed by probing:
/// `get_data()` is unchanged immediately after `apply_force`, only
/// after the following `apply_forces`).
pub fn apply_force(o: ObjectData, x: i64, y: i64) -> ObjectData {
    apply_force_str(o, fixed_from_int(x), fixed_from_int(y))
}

pub fn apply_force_str(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let mut out = o;
    out.pending_force_x = o.pending_force_x.wrapping_add(x);
    out.pending_force_y = o.pending_force_y.wrapping_add(y);
    out
}

/// Like `apply_force`, but `x` is scaled by the object's own facing
/// first (confirmed by probing: with `facing = Left`, `apply_force_relative(5,
/// 0)` moved the object by `-5` once integrated). `y` is not scaled:
/// no probe showed facing affecting the vertical component, and no
/// live call site passes a nonzero `y` to a `_relative` force method.
pub fn apply_force_relative(o: ObjectData, x: i64, y: i64) -> ObjectData {
    apply_force_relative_str(o, fixed_from_int(x), fixed_from_int(y))
}

pub fn apply_force_relative_str(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let signed_x = x.wrapping_mul_int(facing_sign(o.facing));
    apply_force_str(o, signed_x, y)
}

/// Queues `gravity * gravity_modifier` as a downward (`y`) force,
/// but only while the object is airborne and `gravity_modifier` is
/// positive. Confirmed by probing: with `gravity_modifier` left at
/// its default (`ONE`), one call while airborne added exactly
/// `gravity`; with `gravity_modifier` set to `"2.0"`, one call added
/// `2 * gravity`; with a negative `gravity_modifier` (a small one
/// and the type's minimum), real calls added nothing at all, not
/// the negative product — the real method appears to treat a
/// non-positive modifier as "no gravity this call", not a direction
/// flip. The grounded gate itself was confirmed on
/// `apply_grav_custom` (see below), which shares the same shape;
/// no probed call exercised plain `apply_grav` while grounded, so
/// this one side is inferred, not directly observed.
pub fn apply_grav(o: ObjectData) -> ObjectData {
    if o.grounded {
        return o;
    }
    if o.gravity_modifier.0 > 0 {
        let g = o.gravity.wrapping_mul(o.gravity_modifier);
        apply_force_str(o, Fixed::ZERO, g)
    } else {
        o
    }
}

/// Queues `grav` as a downward force, but only while the object is
/// airborne and its current `vel_y` is still below `fall_speed`.
/// Confirmed by probing: from rest and airborne,
/// `apply_grav_custom("1.5", "6")` added `1.5` to the queue (`vel_y`
/// integrated to `1.5`); from `vel_y = "20"` (already past
/// `fall_speed`), the same call added nothing, and the following
/// `apply_forces` left `vel_y` at the object's own stored
/// `max_fall_speed` ("15" in the probe), not at `fall_speed` ("6") —
/// so `fall_speed` gates whether gravity is queued at all, and does
/// not change the cap `apply_forces` applies. A grounded object
/// (the default state, not explicitly set) ignored the call entirely:
/// `vel_y` carried a preset value of 5 straight through unchanged,
/// where the ungated formula would have added `1.5`
/// (`tools/fgobject_diff/README.md`, "apply_grav_custom"). The
/// boundary case `vel_y == fall_speed` was not probed; this follows
/// the observed `vel_y < fall_speed` side.
pub fn apply_grav_custom(o: ObjectData, grav: Fixed, fall_speed: Fixed) -> ObjectData {
    if o.grounded {
        return o;
    }
    if o.vel_y.0 < fall_speed.0 {
        apply_force_str(o, Fixed::ZERO, grav)
    } else {
        o
    }
}

/// Integrates the queued force into velocity, clamps it, moves the
/// object by the new velocity, and applies the ground check.
/// Confirmed by probing (`tools/fgobject_diff/README.md`,
/// "apply_forces"):
/// - `vel_x` is clamped to `[-max_ground_speed, max_ground_speed]`
///   when grounded, `[-max_air_speed, max_air_speed]` when not.
/// - `vel_y` is clamped only on the downward (positive) side, to
///   `max_fall_speed`; an upward (negative) velocity is never capped.
/// - the clamp is on the final velocity, not only the queued force:
///   a `vel_y` already past the cap (set directly, with no pending
///   force at all) is pulled back to the cap by a plain
///   `apply_forces` call.
/// - position moves by the clamped velocity, not the raw queued
///   force.
pub fn apply_forces(o: ObjectData) -> ObjectData {
    let x_limit = if o.grounded {
        o.max_ground_speed
    } else {
        o.max_air_speed
    };
    let raw_vel_x = o.vel_x.wrapping_add(o.pending_force_x);
    let new_vel_x = clamp_sym(raw_vel_x, x_limit);
    let raw_vel_y = o.vel_y.wrapping_add(o.pending_force_y);
    let capped_vel_y = if raw_vel_y.0 > o.max_fall_speed.0 {
        o.max_fall_speed
    } else {
        raw_vel_y
    };
    let new_pos_x = o.position_x.wrapping_add(new_vel_x);
    let raw_pos_y = o.position_y.wrapping_add(capped_vel_y);
    let (new_pos_y, new_vel_y, grounded) = ground_clamp_bidirectional(raw_pos_y, capped_vel_y, o.snap_to_ground);
    let mut out = o;
    out.position_x = new_pos_x;
    out.position_y = new_pos_y;
    out.vel_x = new_vel_x;
    out.vel_y = new_vel_y;
    out.grounded = grounded;
    out.pending_force_x = Fixed::ZERO;
    out.pending_force_y = Fixed::ZERO;
    out
}

/// Like `apply_forces`, but skips both speed clamps: the queued force
/// integrates into velocity unclamped, in either axis. The ground
/// check still applies, the same as `move_directly`: both are
/// position-changing methods, and the ground check was confirmed on
/// every one of them independently.
pub fn apply_forces_no_limit(o: ObjectData) -> ObjectData {
    let new_vel_x = o.vel_x.wrapping_add(o.pending_force_x);
    let raw_vel_y = o.vel_y.wrapping_add(o.pending_force_y);
    let new_pos_x = o.position_x.wrapping_add(new_vel_x);
    let raw_pos_y = o.position_y.wrapping_add(raw_vel_y);
    let (new_pos_y, new_vel_y, grounded) = ground_clamp_bidirectional(raw_pos_y, raw_vel_y, o.snap_to_ground);
    let mut out = o;
    out.position_x = new_pos_x;
    out.position_y = new_pos_y;
    out.vel_x = new_vel_x;
    out.vel_y = new_vel_y;
    out.grounded = grounded;
    out.pending_force_x = Fixed::ZERO;
    out.pending_force_y = Fixed::ZERO;
    out
}

// ============================================================================
// friction
// ============================================================================

/// `v - v * fric`, with no clamp: a `fric` above 1 can flip the sign
/// (confirmed by probing: `apply_x_fric("2.5")` from `vel_x = "5"`
/// gave `-7.5 = 5 * (1 - 2.5)`, not a value clamped to `0`).
pub fn apply_x_fric(o: ObjectData, fric: Fixed) -> ObjectData {
    let mut out = o;
    let keep = Fixed::ONE.wrapping_sub(fric);
    out.vel_x = o.vel_x.wrapping_mul(keep);
    out
}

pub fn apply_y_fric(o: ObjectData, fric: Fixed) -> ObjectData {
    let mut out = o;
    let keep = Fixed::ONE.wrapping_sub(fric);
    out.vel_y = o.vel_y.wrapping_mul(keep);
    out
}

/// Decrements `vel_x` by `(|vel_x| / max_speed) * friction`, signed to
/// match `vel_x`, where `max_speed` is `max_ground_speed` when grounded,
/// `max_air_speed` otherwise (and `friction` is `ground_friction` or
/// `air_friction` the same way). `vel_y` is left alone: a recorded real
/// call with `vel_x = "5"`, `vel_y = "-3"` changed only `vel_x`, for
/// every probed friction and speed-cap combination
/// (`tools/fgobject_diff/README.md`, "apply_fric"). Vertical friction is
/// `apply_y_fric`'s own job, called separately where the game wants it.
///
/// This is **not** `apply_x_fric` with an effective `friction /
/// max_speed` argument: disassembling `BaseObject::apply_fric`
/// (`lib/tbfg.so`) shows the division happens first, against
/// `max_speed`, and the multiply by `friction` second — the reverse
/// order from computing a ratio and multiplying `vel_x` by it once.
/// The two orders give the same real number but round differently at
/// each step; only the disassembled order reproduces the engine
/// exactly: `vel_x = "5"`, `ground_friction = "2.5"`,
/// `max_ground_speed = "15"` gave raw `273068` from the division-first
/// order (matching the engine) and raw `273070` from the
/// ratio-first order (this function's own earlier, wrong version).
/// A zero `max_ground_speed`/`max_air_speed` is a division by zero in
/// the real library, caught as Nil; this returns the object
/// unchanged for that case, the same policy as every other method
/// the real library can panic on.
pub fn apply_fric(o: ObjectData) -> ObjectData {
    let (friction, max_speed) = if o.grounded {
        (o.ground_friction, o.max_ground_speed)
    } else {
        (o.air_friction, o.max_air_speed)
    };
    match o.vel_x.abs().wrapping_div(max_speed) {
        None => o,
        Some(scaled) => {
            let magnitude = scaled.wrapping_mul(friction);
            let decrement = if o.vel_x.0 < 0 {
                Fixed(-magnitude.0)
            } else {
                magnitude
            };
            let mut out = o;
            out.vel_x = o.vel_x.wrapping_sub(decrement);
            out
        }
    }
}

// ============================================================================
// direct movement (no force accumulator, same ground check as apply_forces)
// ============================================================================

pub fn move_directly(o: ObjectData, x: i64, y: i64) -> ObjectData {
    move_directly_str(o, fixed_from_int(x), fixed_from_int(y))
}

pub fn move_directly_str(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let new_pos_x = o.position_x.wrapping_add(x);
    let raw_pos_y = o.position_y.wrapping_add(y);
    let (new_pos_y, new_vel_y, grounded) = ground_clamp_landing_only(raw_pos_y, o.vel_y, o.grounded, o.snap_to_ground);
    let mut out = o;
    out.position_x = new_pos_x;
    out.position_y = new_pos_y;
    out.vel_y = new_vel_y;
    out.grounded = grounded;
    out
}

/// Like `move_directly`, but `x` is scaled by the object's own facing
/// first (confirmed by probing, the same way as
/// `apply_force_relative`). `y` is not scaled.
pub fn move_directly_relative(o: ObjectData, x: i64, y: i64) -> ObjectData {
    move_directly_relative_str(o, fixed_from_int(x), fixed_from_int(y))
}

pub fn move_directly_relative_str(o: ObjectData, x: Fixed, y: Fixed) -> ObjectData {
    let signed_x = x.wrapping_mul_int(facing_sign(o.facing));
    move_directly_str(o, signed_x, y)
}

// ============================================================================
// pushback
// ============================================================================

/// The pushback accumulator's decay, applied by `apply_pushback`
/// exactly once per `add_pushback` (see `ObjectData::pushback_decayed`).
/// Confirmed by probing (`tools/fgobject_diff/README.md`,
/// "apply_pushback"): a pushback of `5`, applied three times with no
/// new `add_pushback` between the calls, moved the object by `5`,
/// then by `4 = 5 * 0.8`, then by `4` again — not `3.2`. One data
/// point does not fix the constant exactly; this is the nearest
/// `Fixed` value to the decimal `0.8` that produced it, flagged as
/// open in the README.
const PUSHBACK_DECAY: Fixed = Fixed(52_429);

pub fn add_pushback(o: ObjectData, v: Fixed) -> ObjectData {
    let mut out = o;
    out.pushback = o.pushback.wrapping_add(v);
    out.pushback_decayed = false;
    out
}

/// Moves the object horizontally by the accumulated pushback times
/// `dir`. The first call after an `add_pushback` then decays the
/// accumulator by `PUSHBACK_DECAY`; every call after that leaves it
/// alone, until the next `add_pushback` resets the one-time decay.
/// Confirmed by probing: `dir` is applied directly, with no
/// dependence on the object's own facing (a pushback applied with
/// `facing = Left` moved the object the same way as with `facing =
/// Right`, for the same `dir`). Only the `x` position moves; no probe
/// showed a `y` effect.
pub fn apply_pushback(o: ObjectData, dir: i64) -> ObjectData {
    let delta = o.pushback.wrapping_mul_int(dir);
    let mut out = o;
    out.position_x = o.position_x.wrapping_add(delta);
    if !o.pushback_decayed {
        out.pushback = o.pushback.wrapping_mul(PUSHBACK_DECAY);
        out.pushback_decayed = true;
    }
    out
}

pub fn reset_pushback(o: ObjectData) -> ObjectData {
    let mut out = o;
    out.pushback = Fixed::ZERO;
    out.pushback_decayed = false;
    out
}

// ============================================================================
// copy
// ============================================================================

/// `copy_to`: the target object's new state is the source's state,
/// field for field. The real method takes the target as a GDScript
/// argument and mutates it in place; this crate's pure-function style
/// instead returns the value the caller assigns to the target.
pub fn copy_to(source: ObjectData) -> ObjectData {
    source
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixed(n: i64, frac_num: i64, frac_den: i64) -> Fixed {
        Fixed::ONE
            .wrapping_mul_int(n)
            .wrapping_add(Fixed::ONE.wrapping_mul_int(frac_num).wrapping_div_int(frac_den).unwrap())
    }

    /// Builds a `Fixed` from a decimal literal the same way the game's
    /// own strings would: `from_bytes`'s round-to-nearest parsing.
    /// `fixed()` above divides raw bits instead, which truncates for a
    /// value like "0.8" that is not an exact binary fraction — exactly
    /// the kind of rounding difference `tools/fixedmath_diff/README.md`
    /// documents for `lerp_string`. Tests that check an exact probed
    /// value (a non-terminating binary fraction like 0.8 or the
    /// pushback decay constant) use this instead.
    fn dec(s: &str) -> Fixed {
        Fixed::from_bytes(s.as_bytes()).unwrap()
    }

    fn configured() -> ObjectData {
        let mut o = new_object();
        o.gravity = dec("0.8");
        o.ground_friction = dec("2.5");
        o.air_friction = dec("0.2");
        o.max_ground_speed = fixed(15, 0, 1);
        o.max_air_speed = fixed(10, 0, 1);
        o.max_fall_speed = fixed(15, 0, 1);
        o
    }

    #[test]
    fn apply_fric_ground_matches_probed_ratio() {
        // tools/fgobject_diff/README.md, "apply_fric": grounded,
        // ground_friction/max_ground_speed = 2.5/15, vel_x "5" decayed
        // to "4.16669" on the real object.
        let mut o = configured();
        o.grounded = true;
        o.vel_x = fixed(5, 0, 1);
        let after = apply_fric(o);
        let expected = Fixed::from_bytes(b"4.16669").unwrap();
        assert_eq!(after.vel_x, expected);
    }

    #[test]
    fn apply_x_fric_can_flip_sign_with_no_clamp() {
        // apply_x_fric("2.5") from vel_x "5": 5 * (1 - 2.5) = -7.5.
        let o = set_vel(configured(), fixed(5, 0, 1), Fixed::ZERO);
        let after = apply_x_fric(o, fixed(2, 5, 10));
        assert_eq!(after.vel_x, Fixed(-7 * 65536 - 32768));
    }

    #[test]
    fn apply_grav_queues_then_apply_forces_integrates() {
        let mut o = configured();
        o.position_y = fixed(-100, 0, 1);
        o.grounded = false;
        let after = apply_forces(apply_grav(o));
        assert_eq!(after.vel_y, dec("0.8"));
        // Position integrates by the new velocity, in raw bits, not by
        // a re-parsed decimal: "0.8" is not an exact binary fraction,
        // so a decimal literal for the expected position would carry
        // its own independent rounding and could pass or fail for a
        // reason unrelated to this function.
        assert_eq!(after.position_y, fixed(-100, 0, 1).wrapping_add(dec("0.8")));
    }

    #[test]
    fn apply_forces_clamps_fall_speed_down_but_not_up() {
        let mut o = configured();
        o.position_y = fixed(-100, 0, 1);
        o.vel_y = fixed(20, 0, 1);
        let fell = apply_forces(o);
        assert_eq!(fell.vel_y, fixed(15, 0, 1));

        let mut up = configured();
        up.position_y = Fixed::ZERO;
        up.vel_y = fixed(-20, 0, 1);
        let rose = apply_forces(up);
        assert_eq!(rose.vel_y, fixed(-20, 0, 1));
    }

    #[test]
    fn landing_zeroes_velocity_and_snaps_to_ground() {
        let mut o = configured();
        o.position_y = Fixed::ZERO;
        o.grounded = false;
        o.snap_to_ground = true;
        let after = apply_forces(apply_grav(o));
        assert_eq!(after.position_y, Fixed::ZERO);
        assert_eq!(after.vel_y, Fixed::ZERO);
        assert_eq!(after.grounded, true);
    }

    #[test]
    fn apply_force_relative_flips_with_facing_left() {
        let mut o = configured();
        o.facing = Facing::Left;
        let after = apply_forces(apply_force_relative(o, 5, 0));
        assert_eq!(after.vel_x, fixed(-5, 0, 1));
    }

    #[test]
    fn move_directly_applies_the_same_ground_clamp() {
        let mut o = configured();
        o.position_y = fixed(-100, 0, 1);
        o.snap_to_ground = true;
        let after = move_directly(o, 0, 200);
        assert_eq!(after.position_y, Fixed::ZERO);
        assert_eq!(after.grounded, true);
    }

    #[test]
    fn pushback_decays_after_each_application() {
        let mut o = configured();
        o.position_x = Fixed::ZERO;
        let after_add = add_pushback(o, fixed(5, 0, 1));
        let first = apply_pushback(after_add, 1);
        assert_eq!(first.position_x, fixed(5, 0, 1));
        // The second application moves the object by less than the
        // first: PUSHBACK_DECAY shrinks the accumulator strictly
        // between calls, not by a hardcoded amount (PUSHBACK_DECAY
        // itself is one probed data point, flagged open in its own
        // doc comment; this checks the decay property it is meant to
        // model, not a specific raw value).
        let decayed = fixed(5, 0, 1).wrapping_mul(PUSHBACK_DECAY);
        let second = apply_pushback(first, 1);
        assert_eq!(second.position_x, first.position_x.wrapping_add(decayed));
        assert!(decayed.0 > 0);
        assert!(decayed.0 < fixed(5, 0, 1).0);
    }

    #[test]
    fn reset_pushback_zeroes_the_accumulator() {
        let o = add_pushback(configured(), fixed(5, 0, 1));
        let after = apply_pushback(reset_pushback(o), 1);
        assert_eq!(after.position_x, Fixed::ZERO);
    }

    #[test]
    fn set_facing_rejects_anything_but_one_or_minus_one() {
        let o = configured();
        assert!(set_facing(o, 1).is_some());
        assert!(set_facing(o, -1).is_some());
        assert!(set_facing(o, 0).is_none());
    }

    #[test]
    fn get_x_vel_int_rounds_ties_away_from_zero() {
        let mut o = configured();
        o.vel_x = Fixed::from_bytes(b"5.7").unwrap();
        assert_eq!(get_x_vel_int(o), 6);
        o.vel_x = Fixed::from_bytes(b"-5.7").unwrap();
        assert_eq!(get_x_vel_int(o), -6);
    }

    #[test]
    fn copy_to_is_a_full_field_for_field_copy() {
        let mut o = configured();
        o.position_x = fixed(9, 0, 1);
        o.vel_x = fixed(3, 0, 1);
        let target = copy_to(o);
        assert_eq!(target, o);
    }
}
