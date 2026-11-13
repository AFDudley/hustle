extends Node

# Headless FixedMath probe. See tools/fixedmath_diff/README.md.
#
# Calls every FixedMath method on a generated input set, and writes one JSON
# line per call to the file named by --fmd-out=<path>:
#   {"method": "<name>", "args": [...], "result": <value>, "error": <bool>}
# "error" is true when the native call returned Nil (tbfg.so panicked, caught
# by gdnative-core's unwind boundary). args and result use plain JSON types:
# a GDScript String argument or result becomes a JSON string; a GDScript Int
# becomes a JSON number; a FixedVec2String-style Dictionary becomes a JSON
# object with the same keys.
#
# tools/fixedmath_diff/rust/src/main.rs reads this file, ignores "result",
# and recomputes with the fixed and fixed-sqrt crates.

const OUT_ARG = "--fmd-out="

var fixed = FixedMath.new()
var rng = RandomNumberGenerator.new()
var out = File.new()
var call_count = 0

# --- method table ------------------------------------------------------------
# kind characters: S = fixed-point string argument, I = plain integer argument.
# "live" methods have at least one uncommented call site in the game's
# GDScript. The rest are registered on FixedMath (bin/FixedMath.gdns, found
# by `strings lib/tbfg.so`) but the game never calls them.
const METHODS = [
	{"name": "abs", "kind": "S", "live": true},
	{"name": "add", "kind": "SS", "live": true},
	{"name": "angle_dist", "kind": "SS", "live": true},
	{"name": "angle_to_vec", "kind": "S", "live": true},
	{"name": "ceil", "kind": "S", "live": false},
	{"name": "deg2rad", "kind": "S", "live": true},
	{"name": "div", "kind": "SS", "live": true},
	{"name": "eq", "kind": "SS", "live": true},
	{"name": "floor", "kind": "S", "live": true},
	{"name": "ge", "kind": "SS", "live": true},
	{"name": "get_x_intercept", "kind": "SSSS", "live": false},
	{"name": "gt", "kind": "SS", "live": true},
	{"name": "int_map", "kind": "IIIII", "live": false},
	{"name": "le", "kind": "SS", "live": true},
	{"name": "lerp_angle", "kind": "SSS", "live": true},
	{"name": "lerp_int", "kind": "SSII", "live": false},
	{"name": "lerp_string", "kind": "SSS", "live": true},
	{"name": "lt", "kind": "SS", "live": true},
	{"name": "mul", "kind": "SS", "live": true},
	{"name": "normalized_vec", "kind": "SS", "live": true},
	{"name": "normalized_vec_times", "kind": "SSS", "live": true},
	{"name": "powu", "kind": "SI", "live": true},
	{"name": "rotate_vec", "kind": "SSS", "live": true},
	{"name": "round", "kind": "S", "live": true},
	{"name": "sign", "kind": "S", "live": true},
	{"name": "sub", "kind": "SS", "live": true},
	{"name": "vec_add", "kind": "SSSS", "live": true},
	{"name": "vec_dist", "kind": "SSSS", "live": true},
	{"name": "vec_dist_squared", "kind": "SSSS", "live": false},
	{"name": "vec_div", "kind": "SSS", "live": true},
	{"name": "vec_dot", "kind": "SSSS", "live": false},
	{"name": "vec_len", "kind": "SS", "live": true},
	{"name": "vec_len_squared", "kind": "SS", "live": false},
	{"name": "vec_mul", "kind": "SSS", "live": true},
	{"name": "vec_round", "kind": "SS", "live": false},
	{"name": "vec_scale", "kind": "SSS", "live": false},
	{"name": "vec_sub", "kind": "SSSS", "live": true},
	{"name": "vec_to_angle", "kind": "SS", "live": true},
]

# Every clean numeric literal passed to a fixed.* call anywhere in the game's
# GDScript (tools/fixedmath_diff/README.md, "Literal constants"), collected by
# grepping every `fixed.<method>(...)` call site and keeping the quoted
# arguments that parse as a plain signed decimal.
const LITERAL_CONSTANTS = ["0", "1", "2", "3", "4", "5", "8", "-1", "-2", "10",
	"12", "20", "24", "30", "-10", "0.0", "0.1", "0.2", "0.3", "0.4", "0.5",
	"0.6", "0.7", "1.0", "1.1", "1.2", "1.4", "1.5", "1.8", "100", "115",
	"2.0", "360", "4.0", "5.0", "8.0", "9.0", "-0.2", "-0.5", "-0.8", "-0.9",
	"-1.0", "0.01", "0.05", "0.15", "0.25", "0.33", "0.35", "0.51", "0.65",
	"0.75", "0.85", "0.90", "0.94", "1.15", "1.75", "10.0", "11.0", "12.0",
	"3000", "-0.25", "-0.34", "-0.55", "-0.75", "0.125", "0.707", "100.0",
	"0.666667", "6.28318530", "0.01745329251"]

# Edge values for an I48F16 fixed-point type (48 integer bits, 16 fractional
# bits; found empirically, see README.md "The fixed-point type"): 0, the
# smallest step (1 ulp = 1/65536), the sign boundary, the integer range
# boundary (+/-2^47), a value one step past each boundary, and a few values
# near the round-half-to-even tie point at the 17th fractional bit.
const EDGE_VALUES = ["0", "-0", "1", "-1", "0.5", "-0.5",
	"0.0000152587890625", "-0.0000152587890625",
	"0.0000076293945312", "0.0000076293945313",
	"0.00001", "-0.00001", "0.00002", "-0.00002",
	"140737488355327", "-140737488355328",
	"140737488355327.99998", "-140737488355327.99998",
	"140737488355326", "-140737488355327",
	"3.14159265358979", "-3.14159265358979", "1.57079632679",
	"1000", "-1000", "0.33333", "100000", "-100000", "65536", "-65536"]

# Strings the fixed crate's FromStr must reject (one unparsable string per
# ParseFixedError kind found in Discover*.gd, plus a few more syntax
# variants): empty, a second decimal point, a non-digit character, a second
# sign, interior whitespace, an exponent, and an underscore digit separator
# (which fixed 1.19.0 does not accept; a plain Rust integer literal does).
const INVALID_VALUES = ["", "abc", "1.2.3", "1e3", "--1", " 1", "1 ",
	"1_000", "+", ".", "-", "1.", ".1", "99999999999999999999"]

func fixed_str(v: float, digits: int) -> String:
	return String(("%." + str(digits) + "f") % v)

func build_random_pool() -> Array:
	var out_pool = []
	# "Coarse" randoms: 5 decimals, the type's own display precision.
	for i in range(40):
		var v = rng.randf_range(-2000.0, 2000.0)
		out_pool.append(fixed_str(v, 5))
	# "Fine" randoms: 9 decimals, more than the type holds (16 fractional
	# bits is about 4.8 decimal digits), to exercise from_str rounding.
	for i in range(40):
		var v = rng.randf_range(-2000.0, 2000.0)
		out_pool.append(fixed_str(v, 9))
	# Near the integer-range boundary, with a fractional part.
	for i in range(20):
		var sgn = 1.0 if rng.randi_range(0, 1) == 0 else -1.0
		var v = sgn * (140737488355327.0 - rng.randf_range(0.0, 5.0))
		out_pool.append(fixed_str(v, 5))
	return out_pool

func build_random_int_pool() -> Array:
	var out_pool = []
	for i in range(30):
		out_pool.append(rng.randi_range(-100000, 100000))
	for v in [0, 1, -1, 2, 10, -10, 47, 48, 62, 63, 64, 100,
			2147483647, -2147483648, 1000000]:
		out_pool.append(v)
	return out_pool

func build_powu_exponent_pool() -> Array:
	# powu is a loop of `exponent` wrapping multiplications on both sides of
	# the diff (FixedMathDiff.gd and rust/src/main.rs). A huge exponent from
	# build_random_int_pool() (it includes i32::MIN/MAX for int_map, which
	# never loops) would make that loop run for minutes to hours. The game's
	# own exponents are small combo and stack counters, so this pool stays
	# small, with the wraparound boundary (47, 48, 62, 63, 64; see
	# README.md, "Range and overflow") covered explicitly.
	return [0, 1, 2, 3, 5, 8, 10, 16, 20, 31, 32, 47, 48, 62, 63, 64, 100, 1000]

func cartesian_bounded(pools: Array, arity: int, cap: int) -> Array:
	# All pool combinations for `arity` positions, each position drawn from
	# the same pool list, in round-robin index order, capped at `cap` tuples
	# so a 4-argument method does not explode combinatorially.
	var combos = []
	var pool = pools[0]
	var n = pool.size()
	if n == 0:
		return combos
	var total = 1
	for i in range(arity):
		total *= n
	total = min(total, cap)
	for idx in range(total):
		var tuple = []
		var rem = idx
		for i in range(arity):
			tuple.append(pool[rem % n])
			rem = rem / n
		combos.append(tuple)
	return combos

func zipped_groups(pool: Array, arity: int) -> Array:
	# One tuple per pool element, taking the next `arity` elements
	# cyclically, so every pool value appears in some tuple.
	var combos = []
	var n = pool.size()
	if n == 0:
		return combos
	for start in range(n):
		var tuple = []
		for k in range(arity):
			tuple.append(pool[(start + k) % n])
		combos.append(tuple)
	return combos

func invalid_in_each_slot(arity: int) -> Array:
	var combos = []
	for invalid in INVALID_VALUES:
		for slot in range(arity):
			var tuple = []
			for k in range(arity):
				tuple.append(invalid if k == slot else "1")
			combos.append(tuple)
	return combos

func string_tuples(arity: int) -> Array:
	if arity == 1:
		var combos = []
		for v in EDGE_VALUES:
			combos.append([v])
		for v in LITERAL_CONSTANTS:
			combos.append([v])
		for v in build_random_pool():
			combos.append([v])
		for v in INVALID_VALUES:
			combos.append([v])
		return combos
	var combos = []
	combos += cartesian_bounded([EDGE_VALUES], arity, 1500 if arity <= 2 else 625)
	combos += zipped_groups(LITERAL_CONSTANTS, arity)
	combos += zipped_groups(build_random_pool(), arity)
	combos += invalid_in_each_slot(arity)
	return combos

func to_json_value(v):
	var t = typeof(v)
	match t:
		TYPE_NIL:
			return null
		TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return v
		TYPE_DICTIONARY:
			var d = {}
			for k in v.keys():
				d[str(k)] = to_json_value(v[k])
			return d
		_:
			return str(v)

func write_call(method: String, args: Array):
	var result = fixed.callv(method, args)
	var is_error = typeof(result) == TYPE_NIL
	var line = {
		"method": method,
		"args": args,
		"result": to_json_value(result),
		"error": is_error,
	}
	out.store_line(JSON.print(line))
	call_count += 1
	if call_count % 2000 == 0:
		out.flush()

func run_method(entry: Dictionary):
	var name = entry.name
	var kind = entry.kind
	var tuples = []
	if kind == "S":
		tuples = string_tuples(1)
	elif kind == "SS":
		tuples = string_tuples(2)
	elif kind == "SSS":
		tuples = string_tuples(3)
	elif kind == "SSSS":
		tuples = string_tuples(4)
	elif kind == "SI":
		var si_pool = EDGE_VALUES + LITERAL_CONSTANTS
		var si_random = build_random_pool()
		for i in range(20):
			si_pool.append(si_random[i])
		for s in si_pool:
			for n in build_powu_exponent_pool():
				tuples.append([s, n])
	elif kind == "IIIII":
		var ints = build_random_int_pool()
		for i in range(60):
			var v = ints[i % ints.size()]
			var imin = -100 - (i % 7)
			var imax = 100 + (i % 11)
			var omin = -50 + (i % 5)
			var omax = 50 + (i % 9)
			tuples.append([v, imin, imax, omin, omax])
		tuples.append([5, 0, 10, 0, 100])
		tuples.append([-5, -10, 10, 0, 100])
		tuples.append([15, 0, 10, 0, 100])
		tuples.append([1, 0, 3, 0, 10])
	elif kind == "SSII":
		var strs = ["0", "1", "2", "5", "10", "-5", "100"]
		var maxes = [1, 10, 100, 255]
		var fs = [0, 1, 2, 5, 10, 100, -5]
		for a in strs:
			for b in strs:
				for m in maxes:
					for f in fs:
						tuples.append([a, b, m, f])
	for tuple in tuples:
		write_call(name, tuple)

func _ready():
	rng.seed = 1337
	var out_path = ""
	for arg in OS.get_cmdline_args():
		if arg.begins_with(OUT_ARG):
			out_path = arg.substr(OUT_ARG.length())
	if out_path.empty():
		printerr("FMD_FAIL: missing " + OUT_ARG + "<path>")
		get_tree().quit(1)
		return
	if out.open(out_path, File.WRITE) != OK:
		printerr("FMD_FAIL: cannot write " + out_path)
		get_tree().quit(1)
		return
	for entry in METHODS:
		run_method(entry)
	out.close()
	print("FMD_DONE " + JSON.print({"calls": call_count, "methods": METHODS.size()}))
	get_tree().quit(0)
