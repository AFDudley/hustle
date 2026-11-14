extends Node

# Headless probe for the game's logic RNG and Godot's hash() builtin.
# See tools/rng_diff/README.md.
#
# Writes one JSON line per probe to the file named by --rngd-out=<path>.
# Four record kinds:
#   {"kind": "hash_int", "value": <int>, "result": <int>}
#   {"kind": "hash_string", "value": <string>, "result": <int>}
#   {"kind": "hash_rng", "state": <int>, "fighter_hash": <int>,
#     "opponent_hash": <int>, "result": <int>}
#   {"kind": "hash_static_rng", "state": <int>, "game_tick": <int>,
#     "result": <int>}
#   {"kind": "seq", "init": {"seed": <int>} or {"state": <int>},
#     "ops": [{"op": "randi"} | {"op": "randi_range", "from": int, "to": int}
#       | {"op": "choose", "len": int}
#       | {"op": "weighted_choice", "weights": [int, ...]}],
#     "result": [<int>, ...]}
#
# port/hustle_core/src/rng.rs and src/godot_hash.rs recompute every
# record. tools/rng_diff/rust/src/main.rs reads this file and ignores
# "result"; compare.py lines the two files up and compares "result".
#
# The "choose" op does not call BetterRng.choose(): it reads the index
# formula directly (raw randi() result, then "% len - 1"), because
# godot_hash.rs's choose_index returns the index, including -1, not the
# array element choose() would read with it. This is the same formula
# (framework/BetterRng.gd, choose()), read out instead of indexed.
#
# The "weighted_choice" op calls BetterRng.weighted_choice() directly: it
# already returns an index (framework/BetterRng.gd, the "return i" line),
# with a dummy array whose length equals len(weights) (weighted_choice()
# loops to len(array), not len(weight_array)).

const OUT_ARG = "--rngd-out="

var out = File.new()
var record_count = 0
var pool_rng = RandomNumberGenerator.new()

# GDScript cannot write the literal -9223372036854775808 (i64::MIN):
# the lexer parses "9223372036854775808" as a positive literal first,
# which already overflows i64, before the unary minus applies. Build it
# from i64::MAX - 1 instead.
const INT64_MIN = -9223372036854775807 - 1

const INT_VALUES = [0, 1, -1, 2, -2, 9223372036854775807, INT64_MIN,
	4294967295, 4294967296, -4294967296, 2147483647, -2147483648, 100, -100]

const STATE_NAME_STRINGS = ["Grabbed", "HurtDizzy", "WhiffInstantCancel",
	"Burst", "DefensiveBurst", "OffensiveBurst", "Idle", "NewParry"]

const FIXED_POINT_STRINGS = ["-12.34567", "0", "0.0", "-0.0", "1",
	"3.14159265358979", "140737488355327.99998", "-140737488355327.99998",
	"0.0000152587890625", "-1000.5"]

const PLAIN_STRINGS = ["", "a", "ABC", "abc123", " ", "Hello, HUSTLE!",
	"a string with spaces and punctuation: !?."]

# RandomPCG::DEFAULT_SEED is 12047754176567800795, a u64 above i64::MAX.
# core/variant.cpp's Variant(uint64_t) constructor reinterprets the bits
# into Variant::INT's int64_t, so the GDScript-visible value is that
# same bit pattern read as a signed int: 12047754176567800795 - 2^64.
const SEEDS = [0, 1, -1, -6398989897141750821, 9223372036854775807,
	INT64_MIN, 1337, 42, 1000000007]

const STATES = [0, 1, -1, 9223372036854775807, INT64_MIN,
	123456789, -987654321]

func write_record(record: Dictionary):
	out.store_line(JSON.print(record))
	record_count += 1

func run_hash_int_probes():
	var values = INT_VALUES.duplicate()
	for i in range(20):
		values.append(pool_rng.randi_range(-1000000000, 1000000000))
	for v in values:
		write_record({"kind": "hash_int", "value": v, "result": hash(v)})

func run_hash_string_probes():
	var values = []
	values += STATE_NAME_STRINGS
	values += FIXED_POINT_STRINGS
	values += PLAIN_STRINGS
	for v in values:
		write_record({"kind": "hash_string", "value": v, "result": hash(v)})

func run_hash_rng_probes():
	for s in STATES:
		for pair in [[0, 0], [1, -1], [9223372036854775807, INT64_MIN],
				[123, 456], [-123, -456]]:
			var rng = BetterRng.new()
			rng.state = s
			var input_hash = hash(pair[0]) + hash(pair[1])
			rng.seed = hash(rng.state + input_hash)
			write_record({"kind": "hash_rng", "state": s, "fighter_hash": pair[0],
				"opponent_hash": pair[1], "result": rng.seed})

func run_hash_static_rng_probes():
	for s in STATES:
		for tick in [0, 1, -1, 3000, 9223372036854775807]:
			var rng = BetterRng.new()
			rng.state = s
			var input_hash = hash(tick)
			rng.seed = hash(rng.state + input_hash)
			write_record({"kind": "hash_static_rng", "state": s, "game_tick": tick,
				"result": rng.seed})

func choose_index_op(rng: BetterRng, length: int) -> int:
	var raw = rng.randi()
	return raw % length - 1

func run_op(rng: BetterRng, op: Dictionary) -> int:
	match op.op:
		"randi":
			return rng.randi()
		"randi_range":
			return rng.randi_range(op.from, op.to)
		"choose":
			return choose_index_op(rng, op.len)
		"weighted_choice":
			var weights = op.weights
			var dummy = []
			for i in range(len(weights)):
				dummy.append(i)
			return rng.weighted_choice(dummy, weights)
	return 0

func op_sequence() -> Array:
	# One op of each kind, in a fixed order, covering the edge ranges and
	# array lengths the brief names: randi_range with from > to and
	# from == to, choose on lengths 1 through 5, and weighted_choice.
	return [
		{"op": "randi"},
		{"op": "randi_range", "from": 0, "to": 0},
		{"op": "randi_range", "from": 5, "to": 5},
		{"op": "randi_range", "from": 5, "to": -5},
		{"op": "randi_range", "from": -5, "to": 5},
		{"op": "randi_range", "from": 10, "to": 1},
		{"op": "randi"},
		{"op": "choose", "len": 1},
		{"op": "choose", "len": 2},
		{"op": "choose", "len": 3},
		{"op": "choose", "len": 4},
		{"op": "choose", "len": 5},
		{"op": "randi"},
		{"op": "weighted_choice", "weights": [1]},
		{"op": "weighted_choice", "weights": [3, 2, 1]},
		{"op": "weighted_choice", "weights": [0, 0, 5]},
		{"op": "weighted_choice", "weights": [1, 1, 1, 1, 1]},
		# Not i32::MIN to i32::MAX: the native randi_range() computes
		# `to - from + 1` in 32-bit `int` before the modulo. That
		# difference is 4294967295, which overflows `int`, wraps to -1,
		# and + 1 makes the modulo's divisor 0: a native SIGFPE crash
		# (confirmed; see README.md, "A native crash, not a port bug").
		# This range is still four orders of magnitude past the game's
		# own usage and triggers no such overflow.
		{"op": "randi_range", "from": -1000000000, "to": 1000000000},
		{"op": "randi"},
	]

func run_sequence_probe(init: Dictionary):
	var rng = BetterRng.new()
	if init.has("seed"):
		rng.seed = init.seed
	else:
		rng.state = init.state
	var ops = op_sequence()
	var results = []
	for op in ops:
		results.append(run_op(rng, op))
	write_record({"kind": "seq", "init": init, "ops": ops, "result": results})

func run_sequence_probes():
	for s in SEEDS:
		run_sequence_probe({"seed": s})
	for s in STATES:
		run_sequence_probe({"state": s})

func _ready():
	pool_rng.seed = 1337
	var out_path = ""
	for arg in OS.get_cmdline_args():
		if arg.begins_with(OUT_ARG):
			out_path = arg.substr(OUT_ARG.length())
	if out_path.empty():
		printerr("RNGD_FAIL: missing " + OUT_ARG + "<path>")
		get_tree().quit(1)
		return
	if out.open(out_path, File.WRITE) != OK:
		printerr("RNGD_FAIL: cannot write " + out_path)
		get_tree().quit(1)
		return
	run_hash_int_probes()
	run_hash_string_probes()
	run_hash_rng_probes()
	run_hash_static_rng_probes()
	run_sequence_probes()
	out.close()
	print("RNGD_DONE " + JSON.print({"records": record_count}))
	get_tree().quit(0)
