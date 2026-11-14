// Recomputes every probe record RngDiff.gd wrote, with hustle_core's
// rng.rs and godot_hash.rs, for a bit-exact diff against the real game.
// See tools/rng_diff/README.md.
//
// Reads one JSON object per line from argv[1]. Ignores "result": that is
// the game's own answer. Recomputes the same probe and writes a line
// with the same shape (plus the recomputed "result") to argv[2].
// compare.py lines the two files up by position and compares "result".
//
// Every GDScript `int` the probe records is widened the same way
// GDScript widens a native return value into a Variant::INT (see
// hustle_core::godot_hash's doc comment): a `hash()` result zero-extends
// from `u32`; a `randi()` result zero-extends from `u32`; `randi_range`,
// `choose_index` and `weighted_choice_index` already return the
// GDScript-visible `i64`.

use hustle_core::godot_hash::{hash_int, hash_string};
use hustle_core::rng::{
    choose_index, new_rng, randi, randi_range, set_seed, set_state, weighted_choice_index, Rng,
};
use serde_json::{json, Value};
use std::io::{BufRead, Write};

fn as_i64(v: &Value) -> i64 {
    v.as_i64().unwrap_or_else(|| panic!("not an int: {v}"))
}

fn as_str(v: &Value) -> &str {
    v.as_str().unwrap_or_else(|| panic!("not a string: {v}"))
}

/// Runs one op against `rng`, returning the next `Rng` and the
/// GDScript-visible `i64` result. Mirrors RngDiff.gd's `run_op`.
fn run_op(rng: Rng, op: &Value) -> (Rng, i64) {
    match op["op"].as_str().expect("op") {
        "randi" => {
            let (next, raw) = randi(rng);
            (next, raw as i64)
        }
        "randi_range" => {
            let from = as_i64(&op["from"]);
            let to = as_i64(&op["to"]);
            randi_range(rng, from, to)
        }
        "choose" => {
            let len = as_i64(&op["len"]);
            choose_index(rng, len)
        }
        "weighted_choice" => {
            let weights: Vec<i64> = op["weights"]
                .as_array()
                .expect("weights")
                .iter()
                .map(as_i64)
                .collect();
            weighted_choice_index(rng, &weights)
        }
        other => panic!("unknown op {other}"),
    }
}

fn run_sequence(init: &Value, ops: &[Value]) -> Vec<i64> {
    let mut rng = if let Some(seed) = init.get("seed") {
        set_seed(new_rng(), as_i64(seed) as u64)
    } else {
        set_state(new_rng(), as_i64(&init["state"]) as u64)
    };
    let mut results = Vec::with_capacity(ops.len());
    for op in ops {
        let (next_rng, result) = run_op(rng, op);
        rng = next_rng;
        results.push(result);
    }
    results
}

fn recompute(record: &Value) -> Value {
    match record["kind"].as_str().expect("kind") {
        "hash_int" => json!(hash_int(as_i64(&record["value"])) as i64),
        "hash_string" => json!(hash_string(as_str(&record["value"])) as i64),
        "hash_rng" => {
            let state = as_i64(&record["state"]) as u64;
            let fighter_hash = as_i64(&record["fighter_hash"]);
            let opponent_hash = as_i64(&record["opponent_hash"]);
            json!(hustle_core::rng::hash_rng(state, fighter_hash, opponent_hash) as i64)
        }
        "hash_static_rng" => {
            let state = as_i64(&record["state"]) as u64;
            let game_tick = as_i64(&record["game_tick"]);
            json!(hustle_core::rng::hash_static_rng(state, game_tick) as i64)
        }
        "seq" => {
            let ops = record["ops"].as_array().expect("ops").clone();
            json!(run_sequence(&record["init"], &ops))
        }
        other => panic!("unknown kind {other}"),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() != 3 {
        eprintln!("usage: rng_diff <in.jsonl> <out.jsonl>");
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
        let record: Value = serde_json::from_str(&line).expect("parse jsonl");
        let result = recompute(&record);
        let mut out_record = record.clone();
        out_record["result"] = result;
        writeln!(out_file, "{out_record}").expect("write output");
    }
}
