extends Node

# Headless FGObject probe. See tools/fgobject_diff/README.md.
#
# Drives an FGObjectRecorder (tools/fgobject_diff/FGObjectRecorder.gd)
# through every one of FGObject's 41 methods, from several starting
# configurations, with edge-value arguments: zero, negative, the
# type's integer-range limits, and the values the game's own GDScript
# passes at its live call sites. The recorder writes one JSON line per
# call to the path named by the environment variable
# FGOBJECT_DIFF_OUT; this scene sets that variable itself, then builds
# a fresh FGObjectRecorder per scenario (so one object's history never
# leaks into the next scenario's starting state).

const Recorder = preload("res://tools/fgobject_diff/FGObjectRecorder.gd")

var out_path := ""

# A representative set of fixed-point strings: zero, +/-1, a small and
# a large magnitude, and the type's own integer-range limits (see
# tools/fixedmath_diff/README.md, "The fixed-point type").
const EDGE_FIXED = ["0", "-0", "1", "-1", "0.5", "-0.5", "0.1", "2.5",
	"0.2", "15", "10", "100", "-100", "0.00001", "-0.00001",
	"140737488355327", "-140737488355328"]

const EDGE_INT = [0, 1, -1, 5, -5, 100, -100, 2147483647, -2147483648]

func out(path_env: String) -> String:
	return path_env

func new_object(gravity: String, ground_fric: String, air_fric: String,
		max_ground: String, max_air: String, max_fall: String):
	var o = Recorder.new()
	o.set_gravity(gravity)
	o.set_ground_friction(ground_fric)
	o.set_air_friction(air_fric)
	o.set_max_ground_speed(max_ground)
	o.set_max_air_speed(max_air)
	o.set_max_fall_speed(max_fall)
	return o

func default_object():
	return new_object("0.8", "2.5", "0.2", "15", "10", "15")

# --- probes, one function per method or closely related group --------------

func probe_setters():
	for v in EDGE_FIXED:
		var o = default_object()
		o.set_gravity(v)
		o.set_ground_friction(v)
		o.set_air_friction(v)
		o.set_max_ground_speed(v)
		o.set_max_air_speed(v)
		o.set_max_fall_speed(v)
		o.set_gravity_modifier(v)
	for x in EDGE_INT:
		for y in EDGE_INT:
			var o = default_object()
			o.set_x(x)
			o.set_y(y)
			o.set_position(x, y)
	for xs in EDGE_FIXED:
		for ys in EDGE_FIXED:
			var o = default_object()
			o.set_position_str(xs, ys)
			o.set_vel(xs, ys)
	for on in [true, false]:
		var o = default_object()
		o.set_grounded(on)
		o.is_grounded()
		o.set_snap_to_ground(on)
		o.get_snap_to_ground()
	for d in [1, -1, 0, 2, -2, 2147483647]:
		var o = default_object()
		o.set_facing(d)
		o.get_facing()
	for v in [0, 1, -1, 7, 2147483647]:
		var o = default_object()
		o.id = v

func probe_update_grounded_and_get_x_vel_int():
	for y in [0, -1, 1, -5, 5, -100, 100, 2147483647, -2147483648]:
		var o = default_object()
		o.set_position(0, y)
		o.update_grounded()
	for v in EDGE_FIXED:
		var o = default_object()
		o.set_vel(v, "0")
		o.get_x_vel_int()
	# ties and near-tie values for round-half-away-from-zero.
	for v in ["0.5", "-0.5", "1.5", "-1.5", "2.5", "-2.5"]:
		var o = default_object()
		o.set_vel(v, "0")
		o.get_x_vel_int()

func probe_reset_momentum():
	for xs in EDGE_FIXED:
		var o = default_object()
		o.set_vel(xs, xs)
		o.reset_momentum()

func probe_apply_fric():
	# Every combination of grounded/airborne and a spread of friction
	# and speed-cap configurations, including a zero speed cap (the
	# real library's division by zero, caught as Nil).
	var frics = ["0", "0.2", "2.5", "15", "100"]
	var caps = ["0", "1", "10", "15", "100"]
	for grounded in [true, false]:
		for fric in frics:
			for cap in caps:
				var o = new_object("0.8", fric, fric, cap, cap, "15")
				o.set_grounded(grounded)
				o.set_vel("5", "-3")
				o.apply_fric()
	for v in EDGE_FIXED:
		var o = default_object()
		o.set_grounded(true)
		o.set_vel(v, v)
		o.apply_fric()

func probe_apply_x_y_fric():
	for fric in EDGE_FIXED:
		var o = default_object()
		o.set_vel("5", "3")
		o.apply_x_fric(fric)
	for fric in EDGE_FIXED:
		var o = default_object()
		o.set_vel("5", "3")
		o.apply_y_fric(fric)

func probe_apply_grav():
	for grav_mod in EDGE_FIXED:
		var o = default_object()
		o.set_position(0, -200)
		o.set_grounded(false)
		o.set_gravity_modifier(grav_mod)
		o.apply_grav()
		o.apply_forces()
	# Near and over the fall-speed cap, both directions.
	for vy in ["0", "14.9", "15", "20", "-20", "-0.1"]:
		var o = default_object()
		o.set_position(0, -200)
		o.set_grounded(false)
		o.set_vel("0", vy)
		o.apply_grav()
		o.apply_forces()
	# Landing: start close enough to the ground that one tick's gravity
	# crosses y = 0.
	for start_y in ["-1", "-0.5", "-0.1", "0", "1"]:
		var o = default_object()
		o.set_position_str("0", start_y)
		o.set_grounded(false)
		o.apply_grav()
		o.apply_forces()

func probe_apply_grav_custom():
	for grav in EDGE_FIXED:
		for fall_speed in ["0", "1", "6", "15", "100"]:
			var o = default_object()
			o.set_position(0, -200)
			o.set_grounded(false)
			o.apply_grav_custom(grav, fall_speed)
			o.apply_forces()
	# Above, at, and below the custom fall_speed gate.
	for vy in ["5", "6", "7"]:
		var o = default_object()
		o.set_position(0, -200)
		o.set_vel("0", vy)
		o.apply_grav_custom("1.5", "6")
		o.apply_forces()

func probe_apply_forces_and_no_limit():
	for grounded in [true, false]:
		for force in EDGE_FIXED:
			var o = default_object()
			o.set_grounded(grounded)
			o.apply_force_str(force, "0")
			o.apply_forces()
	for grounded in [true, false]:
		for force in EDGE_FIXED:
			var o = default_object()
			o.set_grounded(grounded)
			o.apply_force_str(force, "0")
			o.apply_forces_no_limit()
	# int-argument variants.
	for x in EDGE_INT:
		for y in EDGE_INT:
			var o = default_object()
			o.apply_force(x, y)
			o.apply_forces()
	# accumulation: two apply_force calls before one apply_forces.
	var acc = default_object()
	acc.apply_force_str("3", "0")
	acc.apply_force_str("4", "0")
	acc.apply_forces()
	# relative variants, both facings.
	for facing in [1, -1]:
		for x in EDGE_INT:
			var o = default_object()
			o.set_facing(facing)
			o.apply_force_relative(x, 0)
			o.apply_forces()
		for xs in EDGE_FIXED:
			var o = default_object()
			o.set_facing(facing)
			o.apply_force_relative_str(xs, "0")
			o.apply_forces()

func probe_move_directly():
	for x in EDGE_INT:
		for y in EDGE_INT:
			var o = default_object()
			o.move_directly(x, y)
	for xs in EDGE_FIXED:
		for ys in EDGE_FIXED:
			var o = default_object()
			o.move_directly_str(xs, ys)
	for facing in [1, -1]:
		for x in EDGE_INT:
			var o = default_object()
			o.set_facing(facing)
			o.move_directly_relative(x, 0)
		for xs in EDGE_FIXED:
			var o = default_object()
			o.set_facing(facing)
			o.move_directly_relative_str(xs, "0")
	# ground clamp / snap_to_ground interaction, from both sides of 0.
	for snap in [true, false]:
		for start_y in [-200, -1, 0, 1, 200]:
			var o = default_object()
			o.set_position(0, start_y)
			o.set_snap_to_ground(snap)
			o.move_directly(0, 10)
			o.move_directly(0, -10)

func probe_pushback():
	for v in EDGE_FIXED:
		var o = default_object()
		o.add_pushback(v)
		o.apply_pushback(1)
	for dir in [1, -1, 0, 2]:
		var o = default_object()
		o.add_pushback("5")
		o.apply_pushback(dir)
	# repeated application without a new add_pushback, both facings.
	for facing in [1, -1]:
		var o = default_object()
		o.set_facing(facing)
		o.add_pushback("5")
		o.apply_pushback(1)
		o.apply_pushback(1)
		o.apply_pushback(1)
	var o2 = default_object()
	o2.add_pushback("3")
	o2.add_pushback("4")
	o2.apply_pushback(1)
	var o3 = default_object()
	o3.add_pushback("5")
	o3.apply_pushback(1)
	o3.reset_pushback()
	o3.apply_pushback(1)

func probe_copy_to():
	for xs in EDGE_FIXED:
		var source = default_object()
		source.set_position_str(xs, xs)
		source.set_vel(xs, xs)
		source.set_facing(-1)
		source.set_grounded(false)
		var target = default_object()
		source.copy_to(target)
	# the source carries pending state (an unconsumed force, a
	# nonzero pushback) at the moment of the copy.
	var source2 = default_object()
	source2.apply_force_str("3", "4")
	source2.add_pushback("2")
	var target2 = default_object()
	source2.copy_to(target2)
	target2.apply_forces()
	target2.apply_pushback(1)

func _ready():
	out_path = OS.get_environment("FGOBJECT_DIFF_OUT")
	if out_path.empty():
		printerr("FGD_FAIL: missing FGOBJECT_DIFF_OUT")
		get_tree().quit(1)
		return
	probe_setters()
	probe_update_grounded_and_get_x_vel_int()
	probe_reset_momentum()
	probe_apply_fric()
	probe_apply_x_y_fric()
	probe_apply_grav()
	probe_apply_grav_custom()
	probe_apply_forces_and_no_limit()
	probe_move_directly()
	probe_pushback()
	probe_copy_to()
	print("FGD_DONE")
	get_tree().quit(0)
