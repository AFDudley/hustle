//! Parses decimal strings with `hustle_core::fixed::Fixed::from_bytes`,
//! the exact parser the port's tables must agree with (round half to
//! even, I48F16). One string per line on stdin, one raw `i64` per line
//! on stdout, in the same order. `tools/tablegen/gen.py` calls this
//! binary so the generator never reimplements the rounding rule.
//!
//! A line `from_bytes` rejects prints the literal `INVALID` instead of
//! an `i64`: two of the five characters' own `.tscn` files hold a
//! "fixed" field that is not a decimal at all (`dir_y = ""` on two
//! Wizard `ConfusingTouch2` hitboxes; `block_pushback_modifier =
//! "False"` on a Robot `JumpGrab` `ThrowBox`). The generator keeps that
//! as an explicit invalid value (`Option<Fixed>::None`), not a guessed
//! substitute; see `port/hustle_core/src/tables/wizard.rs` and
//! `robot.rs` for the exact fields this marks.

use std::io::{self, BufRead, Write};

fn main() {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut out = stdout.lock();
    for line in stdin.lock().lines() {
        let line = line.expect("reading stdin");
        match hustle_core::fixed::Fixed::from_bytes(line.as_bytes()) {
            Some(value) => writeln!(out, "{}", value.0).expect("writing stdout"),
            None => writeln!(out, "INVALID").expect("writing stdout"),
        }
    }
}
