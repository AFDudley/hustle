//! The core game port of Your Only Move Is HUSTLE.
//!
//! This crate has no dependencies. See `README.md` for the reason: Charon
//! must extract the MIR of every function a Lean proof uses, so the port
//! does not link `fixed`, `fixed-sqrt`, or `fixed_trigonometry`. It copies
//! their algorithms instead.
//!
//! Modules: `fixed`, the fixed-point number type; `godot_hash`, Godot
//! 3.5.1's `hash()` builtin; `rng`, the logic RNG (Godot's PCG32 plus
//! `BetterRng`'s `choose` and `weighted_choice`). See
//! `docs/labbook/2026-11-13-core-game-scope-five-characters.md`,
//! decisions 4 and 5.

pub mod fixed;
pub mod godot_hash;
pub mod rng;
