// Recomputes every FixedMath call with hustle_core::fixed::Fixed, in
// place of the fixed/fixed-sqrt/fixed_trigonometry crates. See
// tools/fixedmath_diff/README.md and port/hustle_core/README.md.
//
// Same input and output format as main.rs: reads argv[1]
// (FixedMathDiff.gd's output), ignores "result"/"error", recomputes, and
// writes argv[2] in the same shape. Run both binaries on the same input
// and the two outputs must be identical (checked by compare.py).
//
// int_map, lerp_int, floor_div, round_half_away's i32 truncation, and
// the JSON glue below are FixedMath-interop details, not properties of
// the number type, so they live here rather than in hustle_core::fixed.

use hustle_core::fixed::Fixed;
use serde_json::{json, Value};
use std::io::{BufRead, Write};

fn parse(s: &str) -> Option<Fixed> {
    hustle_core::fixed_text::from_str(s)
}

fn to_s(v: Fixed) -> Value {
    Value::String(hustle_core::fixed_text::display(v))
}

fn vec2(x: Fixed, y: Fixed) -> Value {
    json!({"x": hustle_core::fixed_text::display(x), "y": hustle_core::fixed_text::display(y)})
}

fn vec2_int(x: i64, y: i64) -> Value {
    json!({"x": x, "y": y})
}

/// FixedMath's integer-returning methods truncate their result to i32
/// before handing it to Godot. Copied from main.rs's `wrap_i32`; see
/// README.md, "Integers truncate to i32".
fn wrap_i32(v: i64) -> i64 {
    (v as i32) as i64
}

/// Arduino-style map(): reconstructed, not read (README.md, "Reconstructed,
/// not read"). Copied from main.rs's `int_map`.
fn int_map(v: i64, in_min: i64, in_max: i64, out_min: i64, out_max: i64) -> Option<i64> {
    let denom = in_max - in_min;
    if denom == 0 {
        return None;
    }
    Some(out_min + floor_div((v - in_min) * (out_max - out_min), denom))
}

/// Floor division (rounds toward negative infinity). Copied from
/// main.rs's `floor_div`.
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

/// Runs one call. Returns (result, error), the same contract as
/// main.rs's `dispatch`.
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
    macro_rules! opt {
        ($e:expr) => {
            match $e {
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
            (to_s(y.wrapping_sub(x).wrap_phase()), false)
        }
        "angle_to_vec" => {
            let t = p!(0);
            (vec2(t.cos(), t.sin()), false)
        }
        "ceil" => (json!(wrap_i32(p!(0).ceil())), false),
        "deg2rad" => (
            to_s(opt!(p!(0).wrapping_mul(hustle_core::fixed::PI).wrapping_div_int(180))),
            false,
        ),
        "div" => match p!(0).wrapping_div(p!(1)) {
            Some(v) => (to_s(v), false),
            None => (Value::Null, true),
        },
        "eq" => (json!(p!(0) == p!(1)), false),
        "floor" => (json!(wrap_i32(p!(0).floor())), false),
        "ge" => (json!(p!(0) >= p!(1)), false),
        "get_x_intercept" => {
            let (x1, y1, x2, y2) = (p!(0), p!(1), p!(2), p!(3));
            if y1 == y2 {
                (to_s(Fixed((i32::MAX as i64) << 16)), false)
            } else {
                match Fixed::ZERO.wrapping_sub(y1).checked_div(y2.wrapping_sub(y1)) {
                    Some(t) => (
                        to_s(x1.wrapping_add(t.wrapping_mul(x2.wrapping_sub(x1)))),
                        false,
                    ),
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
            (
                to_s(x.wrapping_add(y.wrapping_sub(x).wrap_phase().wrapping_mul(t))),
                false,
            )
        }
        "lerp_int" => {
            let (av, bv) = (p!(0), p!(1));
            let (max_v, f) = (n!(2), n!(3));
            if max_v == 0 {
                return (Value::Null, true);
            }
            let t = opt!(Fixed(f << 16).wrapping_div(Fixed(max_v << 16)));
            (
                json!(wrap_i32(av.wrapping_add(bv.wrapping_sub(av).wrapping_mul(t)).floor())),
                false,
            )
        }
        "lerp_string" => {
            let (x, y, t) = (p!(0), p!(1), p!(2));
            let one_minus_t = Fixed::ONE.wrapping_sub(t);
            (
                to_s(x.wrapping_mul(one_minus_t).wrapping_add(y.wrapping_mul(t))),
                false,
            )
        }
        "lt" => (json!(p!(0) < p!(1)), false),
        "mul" => (to_s(p!(0).wrapping_mul(p!(1))), false),
        "normalized_vec" => {
            let (x, y) = opt!(Fixed::normalized_vec(p!(0), p!(1)));
            (vec2(x, y), false)
        }
        "normalized_vec_times" => {
            let (x, y) = opt!(Fixed::normalized_vec(p!(0), p!(1)));
            let s = p!(2);
            (vec2(x.wrapping_mul(s), y.wrapping_mul(s)), false)
        }
        "powu" => (to_s(p!(0).powu(n!(1))), false),
        "rotate_vec" => {
            let (x, y, t) = (p!(0), p!(1), p!(2));
            let (s, c) = (t.sin(), t.cos());
            let nx = x.wrapping_mul(c).wrapping_sub(y.wrapping_mul(s));
            let ny = x.wrapping_mul(s).wrapping_add(y.wrapping_mul(c));
            (vec2(nx, ny), false)
        }
        "round" => (json!(wrap_i32(p!(0).round_half_away())), false),
        "sign" => {
            let v = p!(0);
            let s = if v.0 > 0 {
                1
            } else if v.0 < 0 {
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
            let v = opt!(Fixed::vec_len(x2.wrapping_sub(x1), y2.wrapping_sub(y1)));
            (to_s(v), false)
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
        "vec_len" => {
            let v = opt!(Fixed::vec_len(p!(0), p!(1)));
            (to_s(v), false)
        }
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
                vec2_int(wrap_i32(x.round_half_away()), wrap_i32(y.round_half_away())),
                false,
            )
        }
        "vec_scale" => {
            let (x, y, s) = (p!(0), p!(1), p!(2));
            (
                vec2_int(
                    wrap_i32(x.wrapping_mul(s).round_half_away()),
                    wrap_i32(y.wrapping_mul(s).round_half_away()),
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
            let v = opt!(Fixed::atan2(y, x));
            (to_s(v), false)
        }
        other => panic!("unknown method {other}"),
    }
}

fn main() {
    std::panic::set_hook(Box::new(|_| {}));
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: hustle_core_diff <in.jsonl> <out.jsonl>");
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
