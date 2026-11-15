extends Node

# Probe counters for tools/covsearch's coverage-directed search.
# tools/covsearch/instrument.py registers this script as the first autoload,
# named __COV__, in a disposable project copy. It replaces SIZE with the
# number of probes (lines + two per branch). Each probe is a counter in h[].
#
# b(id_true, id_false, value) counts a branch condition's outcome and
# returns the value unchanged, so instrumentation can wrap a condition
# expression in place: `if __COV__.b(3,4,(x>0)):`.
#
# vc(id, value) counts the value class of one FGObject call argument and
# returns the value unchanged. Four classes, numbered 0..3:
#   0 zero         value == 0 (as a float)
#   1 negative     value < 0
#   2 positive     value > 0 (and not a type-limit value)
#   3 type limit   abs(value) >= LIMIT_MAGNITUDE, a magnitude far past any
#                  value this game's tuning data produces (walk speed,
#                  forces and positions stay under 10000 in this game's own
#                  data; 1_000_000 is two orders of magnitude past that).
# Each class gets one probe id, laid out consecutively (id+0 .. id+3) by
# instrument.py, so one hit array slot per class. A String argument (the
# game's fixed-point values pass as strings) is parsed with float() first.
#
# At exit, the counts go to the file in the COVERAGE_OUT environment
# variable, as JSON: [[probe id, count], ...] for each probe with a count
# above 0. Without COVERAGE_OUT, nothing is written.

const SIZE = 0
const LIMIT_MAGNITUDE = 1000000.0

var h = []

func _init():
	h.resize(SIZE)
	for i in range(SIZE):
		h[i] = 0

func b(id_true: int, id_false: int, value):
	if value:
		h[id_true] += 1
	else:
		h[id_false] += 1
	return value

func vc(id: int, value):
	var f = float(value) if typeof(value) != TYPE_STRING else float(value)
	if f == 0.0:
		h[id + 0] += 1
	elif abs(f) >= LIMIT_MAGNITUDE:
		h[id + 3] += 1
	elif f < 0.0:
		h[id + 1] += 1
	else:
		h[id + 2] += 1
	return value

func _exit_tree():
	var path = OS.get_environment("COVERAGE_OUT")
	if path == "":
		return
	var hits = []
	for i in range(SIZE):
		if h[i] > 0:
			hits.append([i, h[i]])
	var f = File.new()
	if f.open(path, File.WRITE) != OK:
		printerr("COVERAGE_FAIL: cannot write " + path)
		return
	f.store_string(JSON.print(hits))
	f.close()
