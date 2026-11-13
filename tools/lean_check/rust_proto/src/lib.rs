//! A small, loosely modelled prototype of the game's core step function, in
//! the Rust subset that decision 5 of
//! `docs/labbook/2026-11-13-core-game-scope-five-characters.md` names:
//! plain structs and enums, objects in a `Vec` indexed by position, a pure
//! step function, no `unsafe`, no hash maps, and checked fixed-point
//! arithmetic through `i128`.
//!
//! The shapes here loosely follow the real game:
//! - `Fixed` mirrors the `fixed::types::I48F16` layout that `tbfg.so` uses
//!   (`characters/BaseChar.gd`, `obj/BaseObj.gd`): 48 integer bits and 16
//!   fractional bits packed into one `i64`, scaled by `2^16`.
//! - `CharState` mirrors one state enum entry per GDScript state script
//!   under `characters/states/` (`Idle.gd`, `Jump.gd`, and so on).
//! - `Obj` mirrors the common fields of `obj/BaseObj.gd`: position,
//!   velocity, hitbox size, HP, current state and frame counter.
//! - `Rng` mirrors `framework/BetterRng.gd`'s role (a seeded generator that
//!   feeds the logic), with the SplitMix64 algorithm standing in for
//!   Godot's `RandomNumberGenerator`.
//! - `aabb_overlap` mirrors `addons/collision_box/CollisionBox.gd`'s
//!   `overlaps` method.
//! - `step_pure` and `step_in_place` mirror `BaseObj.gd`'s `tick()`: one
//!   fighter update, gravity, a projectile spawn, and a hit resolution
//!   pass, over one tick for both players.
//!
//! This file does not port real character data. It exists only to measure
//! what Aeneas (through Charon) and hax accept from this style, before the
//! real port writes the same style at scale.

/// Number of fractional bits in `Fixed`, matching `fixed::types::I48F16`.
pub const FRAC_BITS: u32 = 16;
/// `2^FRAC_BITS`, the scale factor between a `Fixed` and its raw `i64`.
pub const SCALE: i64 = 1 << FRAC_BITS;

/// Every arithmetic failure the core can hit. No panic, no silent wraparound
/// on game-state values: every operation that can overflow returns this.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Error {
    /// A checked add, sub or mul left the `i64` range.
    Overflow,
    /// `step` was called on a state with fewer than two fighters.
    BadIndex,
}

/// A fixed-point number in Q48.16, matching `tbfg.so`'s `fixed::types::I48F16`.
/// The raw `i64` is the value multiplied by `SCALE`.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug)]
pub struct Fixed(pub i64);

impl Fixed {
    pub const ZERO: Fixed = Fixed(0);

    /// Multiplies through `i128` so the intermediate product never
    /// overflows `i64` before the shift back down to Q48.16.
    pub fn checked_mul(self, other: Fixed) -> Result<Fixed, Error> {
        let wide: i128 = (self.0 as i128) * (other.0 as i128);
        let shifted = wide >> FRAC_BITS;
        if shifted > i64::MAX as i128 || shifted < i64::MIN as i128 {
            return Err(Error::Overflow);
        }
        Ok(Fixed(shifted as i64))
    }

    pub fn checked_add(self, other: Fixed) -> Result<Fixed, Error> {
        match self.0.checked_add(other.0) {
            Some(v) => Ok(Fixed(v)),
            None => Err(Error::Overflow),
        }
    }

    pub fn checked_sub(self, other: Fixed) -> Result<Fixed, Error> {
        match self.0.checked_sub(other.0) {
            Some(v) => Ok(Fixed(v)),
            None => Err(Error::Overflow),
        }
    }
}

/// A seeded generator for the logic RNG, standing in for
/// `framework/BetterRng.gd`. SplitMix64 is deterministic and needs no
/// lookup table, so its state is one `u64`.
#[derive(Clone, Copy, Debug)]
pub struct Rng(pub u64);

impl Rng {
    pub fn new(seed: u64) -> Rng {
        Rng(seed)
    }

    /// Advances the generator and returns the next 64-bit output. The
    /// additions and multiplications wrap by design: this is the generator
    /// itself, not a game-state value, so SplitMix64's published constants
    /// apply unchanged.
    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E3779B97F4A7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58476D1CE4E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D049BB133111EB);
        z ^ (z >> 31)
    }

    /// Returns a value in `0..bound`, or `0` when `bound` is `0`.
    pub fn next_range(&mut self, bound: u64) -> u64 {
        if bound == 0 {
            return 0;
        }
        self.next_u64() % bound
    }
}

/// One variant per character state script under `characters/states/`.
/// The real port has about 30 of these; this prototype keeps six.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum CharState {
    Idle,
    Walk,
    Jump,
    Attack,
    Hurt,
    Knockdown,
}

/// The tick at which a state exits on its own, mirroring the exported
/// `anim_length` field that every `CharState.gd` subclass sets. `Idle` and
/// `Walk` have no such tick: an input, not a frame count, ends them.
fn state_exit_frame(state: CharState) -> u32 {
    match state {
        CharState::Idle => u32::MAX,
        CharState::Walk => u32::MAX,
        CharState::Jump => 20,
        CharState::Attack => 12,
        CharState::Hurt => 8,
        CharState::Knockdown => 24,
    }
}

/// The frame callback every `CharState.gd` subclass gets: given the current
/// state and its elapsed frame count, return the next state, or `None` to
/// stay. Mirrors the state machine's per-tick transition check.
fn on_state_frame(state: CharState, frame: u32) -> Option<CharState> {
    if frame < state_exit_frame(state) {
        return None;
    }
    match state {
        CharState::Idle | CharState::Walk => None,
        CharState::Jump | CharState::Attack | CharState::Hurt | CharState::Knockdown => {
            Some(CharState::Idle)
        }
    }
}

/// One object kind. The real game's `obj/BaseObj.gd` is the base of both
/// the fighter and every projectile; this prototype keeps that split as an
/// enum tag instead of a class hierarchy.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum ObjKind {
    Fighter,
    Projectile,
}

/// One object: a fighter or a projectile. Mirrors the common fields of
/// `obj/BaseObj.gd` (position, velocity, hitbox size, HP, state, frame).
#[derive(Clone, Copy, Debug)]
pub struct Obj {
    pub kind: ObjKind,
    /// For a fighter, its own index. For a projectile, the index of the
    /// fighter that spawned it, so a projectile never hits its owner.
    pub owner: usize,
    pub x: Fixed,
    pub y: Fixed,
    pub vx: Fixed,
    pub vy: Fixed,
    pub w: Fixed,
    pub h: Fixed,
    pub hp: i64,
    pub state: CharState,
    pub frame: u32,
    /// `1` for facing right, `-1` for facing left.
    pub facing: i64,
}

/// One player's input for one tick. Mirrors the `{"action", "data", "extra"}`
/// shape that `BaseChar.gd` writes into a replay, reduced to the three axes
/// this prototype's state machine reads.
#[derive(Clone, Copy, Debug)]
pub struct Input {
    /// `-1`, `0` or `1`.
    pub move_x: i64,
    pub jump: bool,
    pub attack: bool,
}

#[derive(Clone, Copy, Debug)]
pub struct Inputs {
    pub p1: Input,
    pub p2: Input,
}

/// The whole match state: both fighters and every spawned projectile in one
/// `Vec`, indexed by position, plus the logic RNG and the tick count.
#[derive(Clone, Debug)]
pub struct State {
    pub objs: Vec<Obj>,
    pub rng: Rng,
    pub tick: u64,
}

const GRAVITY: Fixed = Fixed(3277); // ~0.05 px/tick^2 in Q48.16.
const MOVE_ACCEL: Fixed = Fixed(13107); // ~0.2 px/tick^2 in Q48.16.
const FRICTION: Fixed = Fixed(58982); // ~0.9 in Q48.16, applied by checked_mul.
const JUMP_VELOCITY: Fixed = Fixed(-196608); // -3 px/tick in Q48.16.
const GROUND_Y: Fixed = Fixed(0);
const PROJECTILE_SPEED: Fixed = Fixed(327680); // 5 px/tick in Q48.16.
const PROJECTILE_SIZE: Fixed = Fixed(655360); // 10 px in Q48.16.
const HITBOX_DAMAGE: i64 = 10;

/// Builds a new match with two fighters, 50 px apart, facing each other.
pub fn new_match(seed: u64) -> State {
    let fighter_w = Fixed(1310720); // 20 px.
    let fighter_h = Fixed(3276800); // 50 px.
    let p1 = Obj {
        kind: ObjKind::Fighter,
        owner: 0,
        x: Fixed(0),
        y: GROUND_Y,
        vx: Fixed::ZERO,
        vy: Fixed::ZERO,
        w: fighter_w,
        h: fighter_h,
        hp: 100,
        state: CharState::Idle,
        frame: 0,
        facing: 1,
    };
    let p2 = Obj {
        x: Fixed(3276800), // 50 px.
        owner: 1,
        facing: -1,
        ..p1
    };
    State {
        objs: vec![p1, p2],
        rng: Rng::new(seed),
        tick: 0,
    }
}

/// One fighter's update for one tick: input, gravity, state-frame advance,
/// and a projectile spawn on the attack's last active frame. Returns the
/// fighter's new fields; writes a spawned projectile into `spawn` when the
/// attack completes.
fn step_fighter(
    mut o: Obj,
    input: Input,
    rng: &mut Rng,
    spawn: &mut Option<Obj>,
) -> Result<Obj, Error> {
    let move_delta = Fixed(
        input
            .move_x
            .checked_mul(MOVE_ACCEL.0)
            .ok_or(Error::Overflow)?,
    );
    o.vx = o.vx.checked_add(move_delta)?;
    o.vx = o.vx.checked_mul(FRICTION)?;

    if input.jump && o.state == CharState::Idle && o.y == GROUND_Y {
        o.vy = JUMP_VELOCITY;
        o.state = CharState::Jump;
        o.frame = 0;
    } else if input.attack && o.state == CharState::Idle {
        o.state = CharState::Attack;
        o.frame = 0;
    } else {
        o.frame = o.frame.checked_add(1).ok_or(Error::Overflow)?;
    }

    o.vy = o.vy.checked_add(GRAVITY)?;
    o.y = o.y.checked_add(o.vy)?;
    if o.y.0 > GROUND_Y.0 {
        o.y = GROUND_Y;
        o.vy = Fixed::ZERO;
    }
    o.x = o.x.checked_add(o.vx)?;

    if let Some(next) = on_state_frame(o.state, o.frame) {
        if o.state == CharState::Attack {
            let dir = Fixed(o.facing.checked_mul(SCALE).ok_or(Error::Overflow)?);
            let spread = Fixed((rng.next_range(4) as i64).checked_mul(1024).ok_or(Error::Overflow)?);
            let speed = PROJECTILE_SPEED.checked_add(spread)?;
            *spawn = Some(Obj {
                kind: ObjKind::Projectile,
                owner: o.owner,
                x: o.x,
                y: o.y,
                vx: dir.checked_mul(speed)?,
                vy: Fixed::ZERO,
                w: PROJECTILE_SIZE,
                h: PROJECTILE_SIZE,
                hp: 1,
                state: CharState::Idle,
                frame: 0,
                facing: o.facing,
            });
        }
        o.state = next;
        o.frame = 0;
    }

    Ok(o)
}

/// A projectile's update for one tick: straight-line travel, then despawn
/// once its frame count passes its lifetime.
fn step_projectile(mut o: Obj) -> Result<Obj, Error> {
    o.x = o.x.checked_add(o.vx)?;
    o.y = o.y.checked_add(o.vy)?;
    o.frame = o.frame.checked_add(1).ok_or(Error::Overflow)?;
    if o.frame > 90 {
        o.hp = 0;
    }
    Ok(o)
}

/// Returns `true` when `a` and `b`'s hitboxes overlap, mirroring
/// `addons/collision_box/CollisionBox.gd`'s `overlaps`.
pub fn aabb_overlap(a: &Obj, b: &Obj) -> Result<bool, Error> {
    let a_right = a.x.checked_add(a.w)?;
    let b_right = b.x.checked_add(b.w)?;
    let a_bottom = a.y.checked_add(a.h)?;
    let b_bottom = b.y.checked_add(b.h)?;
    Ok(a.x < b_right && b.x < a_right && a.y < b_bottom && b.y < a_bottom)
}

/// Applies one projectile's hit against every object from index `j` up,
/// mirroring the inner pass of `resolve_hits`. Written as recursion over
/// `j`, not a `while` loop: Aeneas (nightly-2026.11.13-0d21a9e) rejects a
/// `while` loop whose body uses `?` to return early, even when the loop
/// does not nest (see podman/README notes and tools/lean_check/README.md).
/// Recursion over an explicit index has no such restriction.
fn apply_projectile_hit(mut objs: Vec<Obj>, i: usize, j: usize) -> Result<Vec<Obj>, Error> {
    if j >= objs.len() {
        return Ok(objs);
    }
    if objs[j].kind == ObjKind::Fighter && objs[j].owner != objs[i].owner {
        if aabb_overlap(&objs[i], &objs[j])? {
            objs[j].hp = objs[j].hp.checked_sub(HITBOX_DAMAGE).ok_or(Error::Overflow)?;
            objs[i].hp = 0;
        }
    }
    apply_projectile_hit(objs, i, j + 1)
}

/// Walks every object from index `i` up, running `apply_projectile_hit`
/// for each live projectile. Recursion over `i`, for the same reason as
/// `apply_projectile_hit`.
fn resolve_projectiles(mut objs: Vec<Obj>, i: usize) -> Result<Vec<Obj>, Error> {
    if i >= objs.len() {
        return Ok(objs);
    }
    if objs[i].kind == ObjKind::Projectile && objs[i].hp > 0 {
        objs = apply_projectile_hit(objs, i, 0)?;
    }
    resolve_projectiles(objs, i + 1)
}

/// Builds `acc` by keeping every fighter and every object with `hp > 0`
/// from `objs`, in order, from index `i` up. A fighter is always kept, so
/// `step_pure` and `step_in_place` never drop one: see
/// `fighter_count_eq_two_after_step` in `generated/src/HustleCheck.lean`.
fn keep_fighters_and_live(objs: &[Obj], i: usize, mut acc: Vec<Obj>) -> Vec<Obj> {
    if i >= objs.len() {
        return acc;
    }
    let o = objs[i];
    if o.kind == ObjKind::Fighter || o.hp > 0 {
        acc.push(o);
    }
    keep_fighters_and_live(objs, i + 1, acc)
}

/// Resolves one tick's hits: a projectile that overlaps a fighter it does
/// not own deals damage and despawns. No fighter is ever dropped: a
/// fighter is kept unconditionally by `keep_fighters_and_live`.
fn resolve_hits(objs: Vec<Obj>) -> Result<Vec<Obj>, Error> {
    let hit = resolve_projectiles(objs, 0)?;
    Ok(keep_fighters_and_live(&hit, 0, Vec::new()))
}

/// Returns the number of `ObjKind::Fighter` entries in `objs`, from index
/// `i` up. Recursion, not a `while` loop: the loop form of this function
/// (reading `objs[i]` behind a shared slice reference on every iteration)
/// hit a different Aeneas limitation, "Could not match the contexts" (a
/// borrow-checking fixed-point failure), even with no `?` anywhere in the
/// body. See tools/lean_check/README.md.
fn fighter_count_from(objs: &[Obj], i: usize) -> usize {
    if i >= objs.len() {
        return 0;
    }
    let rest = fighter_count_from(objs, i + 1);
    if objs[i].kind == ObjKind::Fighter {
        rest + 1
    } else {
        rest
    }
}

pub fn fighter_count(objs: &[Obj]) -> usize {
    fighter_count_from(objs, 0)
}

/// Updates every object in `objs` from index `i` up, into `acc`, the same
/// way `step_pure`'s documentation describes. Recursion over `i` with an
/// accumulator, for the same reason as `apply_projectile_hit`.
fn step_objs(
    objs: &[Obj],
    inputs: &Inputs,
    i: usize,
    rng: &mut Rng,
    spawn: &mut Option<Obj>,
    mut acc: Vec<Obj>,
) -> Result<Vec<Obj>, Error> {
    if i >= objs.len() {
        return Ok(acc);
    }
    let o = objs[i];
    let updated = match o.kind {
        ObjKind::Fighter => {
            let input = if i == 0 { inputs.p1 } else { inputs.p2 };
            step_fighter(o, input, rng, spawn)?
        }
        ObjKind::Projectile => step_projectile(o)?,
    };
    acc.push(updated);
    step_objs(objs, inputs, i + 1, rng, spawn, acc)
}

/// The pure step function: `state` is read only, and the new state is
/// returned. Mirrors `BaseObj.gd`'s per-tick update, applied to every
/// object in `state.objs` in index order, then one hit-resolution pass.
pub fn step_pure(state: &State, inputs: &Inputs) -> Result<State, Error> {
    if state.objs.len() < 2 {
        return Err(Error::BadIndex);
    }
    let mut rng = state.rng;
    let mut spawn: Option<Obj> = None;
    let new_objs = step_objs(&state.objs, inputs, 0, &mut rng, &mut spawn, Vec::new())?;
    let mut new_objs = new_objs;
    if let Some(p) = spawn {
        new_objs.push(p);
    }
    let new_objs = resolve_hits(new_objs)?;
    let tick = state.tick.checked_add(1).ok_or(Error::Overflow)?;
    Ok(State {
        objs: new_objs,
        rng,
        tick,
    })
}

/// Updates `objs[i..]` in place, mirroring `step_objs` but writing through
/// `&mut Vec<Obj>` instead of building a new one. Recursion over `i`, for
/// the same reason as `step_objs`.
fn step_objs_in_place(
    objs: &mut Vec<Obj>,
    inputs: &Inputs,
    i: usize,
    rng: &mut Rng,
    spawn: &mut Option<Obj>,
) -> Result<(), Error> {
    if i >= objs.len() {
        return Ok(());
    }
    let o = objs[i];
    let updated = match o.kind {
        ObjKind::Fighter => {
            let input = if i == 0 { inputs.p1 } else { inputs.p2 };
            step_fighter(o, input, rng, spawn)?
        }
        ObjKind::Projectile => step_projectile(o)?,
    };
    objs[i] = updated;
    step_objs_in_place(objs, inputs, i + 1, rng, spawn)
}

/// The in-place step function: the same update as `step_pure`, written
/// against `&mut State`. Compaction after a despawn still builds a fresh
/// `Vec` (`resolve_hits` takes `objs` by value), since Rust has no
/// in-place removal by predicate without a closure, and decision 5 rules
/// closures out.
pub fn step_in_place(state: &mut State, inputs: &Inputs) -> Result<(), Error> {
    if state.objs.len() < 2 {
        return Err(Error::BadIndex);
    }
    let mut spawn: Option<Obj> = None;
    step_objs_in_place(&mut state.objs, inputs, 0, &mut state.rng, &mut spawn)?;
    if let Some(p) = spawn {
        state.objs.push(p);
    }
    let resolved = resolve_hits(state.objs.clone())?;
    state.objs = resolved;
    state.tick = state.tick.checked_add(1).ok_or(Error::Overflow)?;
    Ok(())
}
