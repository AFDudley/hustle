// Replays every FGObject call tools/fgobject_diff recorded (edge-value
// probes and real play) against port/hustle_core::physics, and
// compares the result exactly. See tools/fgobject_diff/README.md.
//
// Each input line is one call, in the shape FGObjectRecorder.gd
// writes: {"obj", "method", "args", "before", "after", "result"}, or,
// for copy_to, {"obj", "method": "copy_to", "target_obj",
// "source_before", "target_before", "target_after"}.
//
// This binary keeps one ObjectData per "obj" id, seeded at
// hustle_core::physics::new_object() the first time that id appears,
// and threads it forward call by call, in file order: the recorded
// "before"/"after" dictionaries are get_data()'s own lossy view
// (position rounded to an integer pixel, no hidden configuration or
// force accumulator), so a correct replay must carry this crate's own
// full-precision state across calls, not reconstruct it from the
// recording each time. Every call still checks the computed result
// against the recording before moving on, so a wrong formula is
// caught at the first call it affects.

use hustle_core::fixed::Fixed;
use hustle_core::fixed_text;
use hustle_core::physics::{self, Facing, ObjectData};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::collections::HashMap;
use std::fs;
use std::io::Write;

fn arg_fixed(args: &[Value], i: usize) -> Fixed {
    let s = args[i].as_str().unwrap_or_else(|| panic!("arg {i} not a string: {:?}", args));
    fixed_text::from_str(s).unwrap_or_else(|| panic!("not a valid Fixed: {s}"))
}

fn arg_i64(args: &[Value], i: usize) -> i64 {
    args[i].as_i64().unwrap_or_else(|| panic!("arg {i} not an int: {:?}", args))
}

fn arg_bool(args: &[Value], i: usize) -> bool {
    args[i].as_bool().unwrap_or_else(|| panic!("arg {i} not a bool: {:?}", args))
}

/// The same shape `get_data()` returns, minus `position_float` (see
/// `physics::ObjectData`'s doc comment: no GDScript reads it, so it is
/// excluded from this check rather than modeled).
fn project(o: ObjectData) -> Value {
    let facing_int = if o.facing == Facing::Right { 1 } else { -1 };
    let (position_x, position_y) = physics::position_pixels(o);
    json!({
        "object_data": {
            "position_x": position_x,
            "position_y": position_y,
            "vel_x": fixed_text::display(o.vel_x),
            "vel_y": fixed_text::display(o.vel_y),
            "facing": facing_int,
            "grounded": o.grounded,
            "snap_to_ground": o.snap_to_ground,
        }
    })
}

fn facing_variant(o: ObjectData) -> Value {
    if o.facing == Facing::Right {
        json!({"Right": {}})
    } else {
        json!({"Left": {}})
    }
}

/// Runs one call against `o`, matching `FGObjectRecorder.gd`'s method
/// table. Returns the new state and the value to compare against the
/// record's "result" (`Value::Null` for every method that returns
/// Nil/void in the game).
fn apply_call(o: ObjectData, method: &str, args: &[Value]) -> (ObjectData, Value) {
    match method {
        "set_x" => (physics::set_x(o, arg_i64(args, 0)), Value::Null),
        "set_y" => (physics::set_y(o, arg_i64(args, 0)), Value::Null),
        "set_position" => (
            physics::set_position(o, arg_i64(args, 0), arg_i64(args, 1)),
            Value::Null,
        ),
        "set_position_str" => (
            physics::set_position_str(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "set_vel" => (
            physics::set_vel(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "set_facing" => match physics::set_facing(o, arg_i64(args, 0)) {
            Some(next) => (next, Value::Null),
            None => (o, Value::Null),
        },
        "get_facing" => {
            let result = facing_variant(o);
            (o, result)
        }
        "set_grounded" => (physics::set_grounded(o, arg_bool(args, 0)), Value::Null),
        "is_grounded" => {
            let result = json!(physics::is_grounded(o));
            (o, result)
        }
        "set_snap_to_ground" => (physics::set_snap_to_ground(o, arg_bool(args, 0)), Value::Null),
        "get_snap_to_ground" => {
            let result = json!(physics::get_snap_to_ground(o));
            (o, result)
        }
        "update_grounded" => (physics::update_grounded(o), Value::Null),
        "get_x_vel_int" => {
            let result = json!(physics::get_x_vel_int(o));
            (o, result)
        }
        "reset_momentum" => (physics::reset_momentum(o), Value::Null),
        "set_gravity" => (physics::set_gravity(o, arg_fixed(args, 0)), Value::Null),
        "set_gravity_modifier" => (
            physics::set_gravity_modifier(o, arg_fixed(args, 0)),
            Value::Null,
        ),
        "set_ground_friction" => (
            physics::set_ground_friction(o, arg_fixed(args, 0)),
            Value::Null,
        ),
        "set_air_friction" => (physics::set_air_friction(o, arg_fixed(args, 0)), Value::Null),
        "set_max_ground_speed" => (
            physics::set_max_ground_speed(o, arg_fixed(args, 0)),
            Value::Null,
        ),
        "set_max_air_speed" => (
            physics::set_max_air_speed(o, arg_fixed(args, 0)),
            Value::Null,
        ),
        "set_max_fall_speed" => (
            physics::set_max_fall_speed(o, arg_fixed(args, 0)),
            Value::Null,
        ),
        "apply_force" => (
            physics::apply_force(o, arg_i64(args, 0), arg_i64(args, 1)),
            Value::Null,
        ),
        "apply_force_str" => (
            physics::apply_force_str(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "apply_force_relative" => (
            physics::apply_force_relative(o, arg_i64(args, 0), arg_i64(args, 1)),
            Value::Null,
        ),
        "apply_force_relative_str" => (
            physics::apply_force_relative_str(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "apply_forces" => (physics::apply_forces(o), Value::Null),
        "apply_forces_no_limit" => (physics::apply_forces_no_limit(o), Value::Null),
        "apply_grav" => (physics::apply_grav(o), Value::Null),
        "apply_grav_custom" => (
            physics::apply_grav_custom(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "apply_fric" => (physics::apply_fric(o), Value::Null),
        "apply_x_fric" => (physics::apply_x_fric(o, arg_fixed(args, 0)), Value::Null),
        "apply_y_fric" => (physics::apply_y_fric(o, arg_fixed(args, 0)), Value::Null),
        "move_directly" => (
            physics::move_directly(o, arg_i64(args, 0), arg_i64(args, 1)),
            Value::Null,
        ),
        "move_directly_str" => (
            physics::move_directly_str(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "move_directly_relative" => (
            physics::move_directly_relative(o, arg_i64(args, 0), arg_i64(args, 1)),
            Value::Null,
        ),
        "move_directly_relative_str" => (
            physics::move_directly_relative_str(o, arg_fixed(args, 0), arg_fixed(args, 1)),
            Value::Null,
        ),
        "add_pushback" => (physics::add_pushback(o, arg_fixed(args, 0)), Value::Null),
        "apply_pushback" => (physics::apply_pushback(o, arg_i64(args, 0)), Value::Null),
        "reset_pushback" => (physics::reset_pushback(o), Value::Null),
        "get_data" => {
            let result = project(o);
            (o, result)
        }
        "set_id" => (physics::set_id(o, arg_i64(args, 0)), Value::Null),
        other => panic!("unknown method: {other}"),
    }
}

struct Report {
    counts: BTreeMap<String, (u64, u64)>, // method -> (total, matched)
    mismatches: Vec<Value>,
}

impl Report {
    fn new() -> Self {
        Report {
            counts: BTreeMap::new(),
            mismatches: Vec::new(),
        }
    }

    fn record(&mut self, method: &str, ok: bool) {
        let entry = self.counts.entry(method.to_string()).or_insert((0, 0));
        entry.0 += 1;
        if ok {
            entry.1 += 1;
        }
    }
}

/// Compares the fields this crate models (see `project`'s doc
/// comment) between a recorded get_data()-shaped `Value` and the
/// computed one. Returns the names of every field that differs.
fn diff_fields(recorded: &Value, computed: &Value) -> Vec<String> {
    let mut out = Vec::new();
    let fields = [
        "position_x",
        "position_y",
        "vel_x",
        "vel_y",
        "facing",
        "grounded",
        "snap_to_ground",
    ];
    let r = &recorded["object_data"];
    let c = &computed["object_data"];
    for field in fields {
        if r[field] != c[field] {
            out.push(field.to_string());
        }
    }
    out
}

fn run_file(path: &str, report: &mut Report) {
    let text = fs::read_to_string(path).unwrap_or_else(|e| panic!("reading {path}: {e}"));
    let mut states: HashMap<i64, ObjectData> = HashMap::new();
    for (line_no, line) in text.lines().enumerate() {
        if line.is_empty() {
            continue;
        }
        let rec: Value = serde_json::from_str(line)
            .unwrap_or_else(|e| panic!("{path}:{}: {e}", line_no + 1));
        let method = rec["method"].as_str().unwrap_or_else(|| panic!("no method"));
        if method == "copy_to" {
            run_copy_to(path, line_no, &rec, &mut states, report);
            continue;
        }
        let obj_id = rec["obj"].as_i64().unwrap_or_else(|| panic!("no obj"));
        let args = rec["args"].as_array().cloned().unwrap_or_default();
        let before_state = *states.entry(obj_id).or_insert_with(physics::new_object);
        let (after_state, result) = apply_call(before_state, method, &args);
        let computed_after = project(after_state);
        let recorded_after = &rec["after"];
        let mut bad_fields = diff_fields(recorded_after, &computed_after);
        let result_ok = result_matches(method, &result, &rec["result"]);
        let ok = bad_fields.is_empty() && result_ok;
        report.record(method, ok);
        if !ok {
            if !result_ok {
                bad_fields.push("result".to_string());
            }
            report.mismatches.push(json!({
                "file": path,
                "line": line_no + 1,
                "obj": obj_id,
                "method": method,
                "args": rec["args"],
                "before": rec["before"],
                "recorded_after": recorded_after,
                "computed_after": computed_after,
                "recorded_result": rec["result"],
                "computed_result": result,
                "bad_fields": bad_fields,
            }));
        }
        states.insert(obj_id, after_state);
    }
}

/// `get_facing`, `is_grounded`, `get_snap_to_ground`, `get_x_vel_int`
/// and `get_data` return a value the game reads; every other method
/// returns Nil, recorded as JSON `null`. `get_data`'s own "result" is
/// the same dictionary as "after", so it is compared the same way
/// (field by field) rather than by strict JSON equality, which would
/// also flag the excluded `position_float` field as a difference.
fn result_matches(method: &str, computed: &Value, recorded: &Value) -> bool {
    if method == "get_data" {
        return diff_fields(recorded, computed).is_empty();
    }
    computed == recorded
}

fn run_copy_to(
    path: &str,
    line_no: usize,
    rec: &Value,
    states: &mut HashMap<i64, ObjectData>,
    report: &mut Report,
) {
    let source_id = rec["obj"].as_i64().unwrap_or_else(|| panic!("no obj"));
    let target_id = rec["target_obj"]
        .as_i64()
        .unwrap_or_else(|| panic!("no target_obj"));
    let source_state = *states.entry(source_id).or_insert_with(physics::new_object);
    let computed_target = physics::copy_to(source_state);
    let computed_after = project(computed_target);
    let recorded_after = &rec["target_after"];
    let bad_fields = diff_fields(recorded_after, &computed_after);
    let ok = bad_fields.is_empty();
    report.record("copy_to", ok);
    if !ok {
        report.mismatches.push(json!({
            "file": path,
            "line": line_no + 1,
            "obj": source_id,
            "target_obj": target_id,
            "method": "copy_to",
            "target_before": rec["target_before"],
            "recorded_after": recorded_after,
            "computed_after": computed_after,
            "bad_fields": bad_fields,
        }));
    }
    states.insert(target_id, computed_target);
}

fn main() {
    let argv: Vec<String> = std::env::args().collect();
    if argv.len() < 3 {
        eprintln!("usage: fgobject_diff <out_report.json> <input1.jsonl> [input2.jsonl ...]");
        std::process::exit(2);
    }
    let out_path = &argv[1];
    let inputs = &argv[2..];

    let mut report = Report::new();
    for path in inputs {
        run_file(path, &mut report);
    }

    let mut total: u64 = 0;
    let mut matched: u64 = 0;
    let mut by_method = serde_json::Map::new();
    for (method, (count, ok)) in &report.counts {
        total += count;
        matched += ok;
        by_method.insert(
            method.clone(),
            json!({"calls": count, "matched": ok}),
        );
    }
    let summary = json!({
        "total_calls": total,
        "matched_calls": matched,
        "by_method": by_method,
        "mismatches": report.mismatches,
    });
    let mut f = fs::File::create(out_path).unwrap_or_else(|e| panic!("{out_path}: {e}"));
    f.write_all(serde_json::to_string_pretty(&summary).unwrap().as_bytes())
        .unwrap();

    println!("fgobject_diff: {matched}/{total} calls matched across {} input file(s)", inputs.len());
    for (method, (count, ok)) in &report.counts {
        if *ok != *count {
            println!("  {method}: {ok}/{count} matched");
        }
    }
    if matched != total {
        std::process::exit(1);
    }
}
