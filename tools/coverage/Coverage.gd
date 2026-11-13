extends Node

# Probe counters for GDScript line coverage. See tools/coverage/README.md.
#
# tools/coverage/instrument.py registers this script as the first autoload,
# named __COV__, in a disposable project copy. It also replaces SIZE with
# the number of probes. Each probe is `__COV__.h[<id>] += 1`.
#
# At exit, the counts go to the file in the COVERAGE_OUT environment
# variable, as JSON: [[probe id, count], ...] for each probe with a count
# above 0. Without COVERAGE_OUT, nothing is written.

const SIZE = 0

var h = []

func _init():
	h.resize(SIZE)
	for i in range(SIZE):
		h[i] = 0

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
