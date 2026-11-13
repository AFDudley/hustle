// Rust reimplementation of FixedMath (bin/FixedMath.gdns, backed by
// lib/tbfg.so), for a bit-exact diff against the real game. See
// tools/fixedmath_diff/README.md.
//
// Reads one JSON object per line from argv[1] (written by
// FixedMathDiff.gd): {"method", "args", "result", "error"}. Ignores
// "result" and "error": those are the game's own answer. Recomputes with
// the same method and args, and writes a line with the same shape to
// argv[2]. compare.py lines the two files up by position and compares
// "result" and "error".
//
// The fixed-point type is I48F16 (FixedI64 with 16 fractional bits),
// found empirically (README.md, "The fixed-point type"). Every arithmetic
// method below calls an explicit wrapping_* or checked_* function, never a
// bare operator: tbfg.so wraps on overflow in add/sub/mul/powu (a release
// build has overflow checks off), and errors (caught by gdnative-core, seen
// by GDScript as Nil) only on a parse failure or on division by zero.

use fixed::types::I48F16 as Fx;
use fixed_sqrt::FixedSqrt;
use serde_json::{json, Value};
use std::io::{BufRead, Write};

/// One game-literal π, at the same precision the game's own GDScript uses
/// for its "6.28318530" (2π) and "0.01745329251" (π/180) constants. No
/// FixedMath method exposes the constant it uses internally, so this is a
/// reconstruction, not a read value (README.md, "Reconstructed, not read").
const PI: Fx = Fx::unwrapped_from_str("3.14159265358979");

fn parse(s: &str) -> Option<Fx> {
    Fx::from_str(s).ok()
}

fn to_s(v: Fx) -> Value {
    Value::String(v.to_string())
}

fn vec2(x: Fx, y: Fx) -> Value {
    json!({"x": x.to_string(), "y": y.to_string()})
}

fn vec2_int(x: i64, y: i64) -> Value {
    json!({"x": x, "y": y})
}

/// round() and vec_round(): half away from zero, as an integer (found
/// empirically: round(2.5) = 3, round(-2.5) = -3, not round-half-to-even).
/// The standard floor(v+0.5) trick for v >= 0, ceil(v-0.5) for v < 0.
fn round_half_away(v: Fx) -> i64 {
    let half = Fx::from_num(1).wrapping_div_int(2);
    if v >= Fx::ZERO {
        v.wrapping_add(half).floor().to_num::<i64>()
    } else {
        v.wrapping_sub(half).ceil().to_num::<i64>()
    }
}

/// FixedMath's integer-returning methods (ceil, floor, round, vec_round,
/// vec_scale) truncate their result to i32 before handing it to Godot,
/// found empirically: ceil("140737488355327") = -1, the low 32 bits of
/// 2^47 - 1 read as a signed i32 (README.md, "Integers truncate to i32").
fn wrap_i32(v: i64) -> i64 {
    (v as i32) as i64
}

/// wrap_phase: normalize an angle to the half-open range (-pi, pi]. An
/// EDGE_VALUES probe can be as large as the type's own range (README.md,
/// "Edge values"), so this reduces with one division first: a loop of
/// plain `a -= two_pi` would need on the order of 10^13 iterations for
/// such a value.
fn wrap_phase(a: Fx) -> Fx {
    let two_pi = PI.wrapping_add(PI);
    let n = a.checked_div(two_pi).map_or(0i64, |q| q.round().to_num::<i64>());
    let mut r = a.wrapping_sub(two_pi.wrapping_mul_int(n));
    while r > PI {
        r = r.wrapping_sub(two_pi);
    }
    while r <= -PI {
        r = r.wrapping_add(two_pi);
    }
    r
}

fn vec_len(x: Fx, y: Fx) -> Fx {
    let sq = x.wrapping_mul(x).wrapping_add(y.wrapping_mul(y));
    // fixed-sqrt's sqrt() panics on a negative input (its own documented
    // behaviour). sq can go negative from a wrapped overflow of x*x + y*y;
    // let that panic propagate, caught by main()'s catch_unwind, the same
    // way gdnative-core catches tbfg.so's panics (README.md, "Panics are
    // caught, not crashes").
    sq.sqrt()
}

fn normalized_vec(x: Fx, y: Fx) -> (Fx, Fx) {
    let len = vec_len(x, y);
    if len == Fx::ZERO {
        (Fx::ZERO, Fx::ZERO)
    } else {
        // Found empirically (README.md, "div wraps, it does not check"):
        // dividing by a vector length wraps on overflow, the same as
        // div() itself, not checked_div's None.
        (x.wrapping_div(len), y.wrapping_div(len))
    }
}

fn powu(base: Fx, exp: i64) -> Fx {
    let mut result = Fx::from_num(1);
    for _ in 0..exp.max(0) {
        result = result.wrapping_mul(base);
    }
    result
}

/// Arduino-style map(): confirmed empirically against int_map (README.md),
/// with floor_div below, not plain `/`.
fn int_map(v: i64, in_min: i64, in_max: i64, out_min: i64, out_max: i64) -> Option<i64> {
    let denom = in_max - in_min;
    if denom == 0 {
        return None;
    }
    Some(out_min + floor_div((v - in_min) * (out_max - out_min), denom))
}

/// Floor division (rounds toward negative infinity), confirmed empirically
/// against int_map (README.md, "int_map floors, Rust's / truncates"):
/// int_map(-76785, -100, 100, -50, 50) needs floor division to match.
fn floor_div(a: i64, b: i64) -> i64 {
    let q = a / b;
    let r = a % b;
    if r != 0 && (r < 0) != (b < 0) {
        q - 1
    } else {
        q
    }
}

struct Call {
    method: String,
    args: Vec<Value>,
}

fn as_str(v: &Value) -> Option<&str> {
    v.as_str()
}

fn as_i64(v: &Value) -> Option<i64> {
    v.as_i64()
}

/// Runs one call. Returns (result, error). error is true exactly when the
/// game-side call would see Nil: an unparsable argument, or an operation
/// tbfg.so itself panics on (division by zero, a negative square root).
fn dispatch(call: &Call) -> (Value, bool) {
    let a = &call.args;
    macro_rules! p {
        ($i:expr) => {
            match as_str(&a[$i]).and_then(parse) {
                Some(v) => v,
                None => return (Value::Null, true),
            }
        };
    }
    macro_rules! n {
        ($i:expr) => {
            match as_i64(&a[$i]) {
                Some(v) => v,
                None => return (Value::Null, true),
            }
        };
    }
    match call.method.as_str() {
        "abs" => (to_s(p!(0).abs()), false),
        "add" => (to_s(p!(0).wrapping_add(p!(1))), false),
        "angle_dist" => {
            let (x, y) = (p!(0), p!(1));
            (to_s(wrap_phase(y.wrapping_sub(x))), false)
        }
        "angle_to_vec" => {
            let t = p!(0);
            let (s, c) = (fixed_trigonometry::sin(t), fixed_trigonometry::cos(t));
            (vec2(c, s), false)
        }
        "ceil" => (json!(wrap_i32(p!(0).ceil().to_num::<i64>())), false),
        "deg2rad" => (to_s(p!(0).wrapping_mul(PI).wrapping_div_int(180)), false),
        "div" => {
            let b = p!(1);
            if b == Fx::ZERO {
                (Value::Null, true)
            } else {
                // Found empirically (README.md, "div wraps, it does not
                // check"): MIN / -1 wraps back to MIN instead of erroring,
                // unlike checked_div's documented behaviour.
                (to_s(p!(0).wrapping_div(b)), false)
            }
        }
        "eq" => (json!(p!(0) == p!(1)), false),
        "floor" => (json!(wrap_i32(p!(0).floor().to_num::<i64>())), false),
        "ge" => (json!(p!(0) >= p!(1)), false),
        "get_x_intercept" => {
            // Reconstructed, not read (README.md): x-intercept of the line
            // through (x1,y1)-(x2,y2) at y=0, with i32::MAX as the sentinel
            // for a horizontal line (y1 == y2), found empirically.
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            if y1 == y2 {
                (to_s(Fx::from_num(i32::MAX)), false)
            } else {
                let t = (Fx::ZERO.wrapping_sub(y1)).checked_div(y2.wrapping_sub(y1));
                match t {
                    Some(t) => (to_s(x1.wrapping_add(t.wrapping_mul(x2.wrapping_sub(x1)))), false),
                    None => (Value::Null, true),
                }
            }
        }
        "gt" => (json!(p!(0) > p!(1)), false),
        "int_map" => match int_map(n!(0), n!(1), n!(2), n!(3), n!(4)) {
            Some(v) => (json!(wrap_i32(v)), false),
            None => (Value::Null, true),
        },
        "le" => (json!(p!(0) <= p!(1)), false),
        "lerp_angle" => {
            let (x, y, t) = (p!(0), p!(1), p!(2));
            (to_s(x.wrapping_add(wrap_phase(y.wrapping_sub(x)).wrapping_mul(t))), false)
        }
        "lerp_int" => {
            // Reconstructed, not read: a:String, b:String, max:i32, f:i32,
            // signature found empirically (README.md). f/max is an
            // integer fraction, found empirically against
            // lerp_int("0","1",10,1) = 0, not 1 (README.md, "lerp_int").
            let (av, bv) = (p!(0), p!(1));
            let (max_v, f) = (n!(2), n!(3));
            if max_v == 0 {
                return (Value::Null, true);
            }
            let t = Fx::from_num(f).wrapping_div(Fx::from_num(max_v));
            (json!(wrap_i32(av.wrapping_add((bv.wrapping_sub(av)).wrapping_mul(t)).to_num::<i64>())), false)
        }
        "lerp_string" => {
            let (x, y, t) = (p!(0), p!(1), p!(2));
            let one_minus_t = Fx::from_num(1).wrapping_sub(t);
            (to_s(x.wrapping_mul(one_minus_t).wrapping_add(y.wrapping_mul(t))), false)
        }
        "lt" => (json!(p!(0) < p!(1)), false),
        "mul" => (to_s(p!(0).wrapping_mul(p!(1))), false),
        "normalized_vec" => {
            let (x, y) = normalized_vec(p!(0), p!(1));
            (vec2(x, y), false)
        }
        "normalized_vec_times" => {
            let (x, y) = normalized_vec(p!(0), p!(1));
            let s = p!(2);
            (vec2(x.wrapping_mul(s), y.wrapping_mul(s)), false)
        }
        "powu" => (to_s(powu(p!(0), n!(1))), false),
        "rotate_vec" => {
            let (x, y, t) = (p!(0), p!(1), p!(2));
            let (s, c) = (fixed_trigonometry::sin(t), fixed_trigonometry::cos(t));
            let nx = x.wrapping_mul(c).wrapping_sub(y.wrapping_mul(s));
            let ny = x.wrapping_mul(s).wrapping_add(y.wrapping_mul(c));
            (vec2(nx, ny), false)
        }
        "round" => (json!(wrap_i32(round_half_away(p!(0)))), false),
        "sign" => {
            let v = p!(0);
            let s = if v > Fx::ZERO {
                1
            } else if v < Fx::ZERO {
                -1
            } else {
                0
            };
            (json!(s), false)
        }
        "sub" => (to_s(p!(0).wrapping_sub(p!(1))), false),
        "vec_add" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            (vec2(x1.wrapping_add(x2), y1.wrapping_add(y2)), false)
        }
        "vec_dist" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            (to_s(vec_len(x2.wrapping_sub(x1), y2.wrapping_sub(y1))), false)
        }
        "vec_dist_squared" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            let (dx, dy) = (x2.wrapping_sub(x1), y2.wrapping_sub(y1));
            (to_s(dx.wrapping_mul(dx).wrapping_add(dy.wrapping_mul(dy))), false)
        }
        "vec_div" => {
            let (x, y, d) = (p!(0), p!(1), p!(2));
            match (x.checked_div(d), y.checked_div(d)) {
                (Some(rx), Some(ry)) => (vec2(rx, ry), false),
                _ => (Value::Null, true),
            }
        }
        "vec_dot" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            (to_s(x1.wrapping_mul(x2).wrapping_add(y1.wrapping_mul(y2))), false)
        }
        "vec_len" => (to_s(vec_len(p!(0), p!(1))), false),
        "vec_len_squared" => {
            let (x, y) = (p!(0), p!(1));
            (to_s(x.wrapping_mul(x).wrapping_add(y.wrapping_mul(y))), false)
        }
        "vec_mul" => {
            let (x, y, s) = (p!(0), p!(1), p!(2));
            (vec2(x.wrapping_mul(s), y.wrapping_mul(s)), false)
        }
        "vec_round" => {
            let (x, y) = (p!(0), p!(1));
            (
                vec2_int(wrap_i32(round_half_away(x)), wrap_i32(round_half_away(y))),
                false,
            )
        }
        "vec_scale" => {
            let (x, y, s) = (p!(0), p!(1), p!(2));
            (
                vec2_int(
                    wrap_i32(round_half_away(x.wrapping_mul(s))),
                    wrap_i32(round_half_away(y.wrapping_mul(s))),
                ),
                false,
            )
        }
        "vec_sub" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            (vec2(x1.wrapping_sub(x2), y1.wrapping_sub(y2)), false)
        }
        "vec_to_angle" => {
            let (x, y) = (p!(0), p!(1));
            (to_s(fixed_trigonometry::atan::atan2(y, x)), false)
        }
        other => panic!("unknown method {other}"),
    }
}

fn main() {
    // Division by zero, a negative square root, and an unparsable argument
    // all panic by design (README.md, "Panics are caught, not crashes").
    // Each is caught just below; a default panic hook would otherwise
    // print one "thread panicked" line per such call.
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: fixedmath_diff <in.jsonl> <out.jsonl>");
        std::process::exit(2);
    }
    let in_file = std::fs::File::open(&args[1]).expect("open input");
    let reader = std::io::BufReader::new(in_file);
    let mut out_file = std::fs::File::create(&args[2]).expect("create output");

    for line in reader.lines() {
        let line = line.expect("read line");
        if line.trim().is_empty() {
            continue;
        }
        let v: Value = serde_json::from_str(&line).expect("parse jsonl");
        let method = v["method"].as_str().expect("method").to_string();
        let call_args = v["args"].as_array().expect("args").clone();
        let call = Call {
            method: method.clone(),
            args: call_args.clone(),
        };
        let (result, error) =
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| dispatch(&call))) {
                Ok(r) => r,
                Err(_) => (Value::Null, true),
            };
        let out_line = json!({
            "method": method,
            "args": call_args,
            "result": result,
            "error": error,
        });
        writeln!(out_file, "{out_line}").expect("write output");
    }
}
