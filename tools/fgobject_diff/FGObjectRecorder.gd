extends Reference

# A recording proxy for FGObject. See tools/fgobject_diff/README.md,
# "Recording real play".
#
# A patched work copy of obj/BaseObj.gd (made by driver.py, never the
# committed source) replaces `var chara = FGObject.new()` with
# `var chara = preload(".../FGObjectRecorder.gd").new()`. Every other
# line in the game stays the same: this proxy forwards each call to a
# real FGObject and returns its real result, so the game runs exactly
# as it does with the real class. The only side effect is one JSON
# line per call, appended to the path named by the environment
# variable FGOBJECT_DIFF_OUT:
#   {"method", "args", "before", "after", "result"}
# "before" and "after" are the real object's own get_data() output,
# read right before and right after the call. `copy_to` writes a
# different shape; see its own method below.
#
# GDScript's `_set`/`_get` intercept the one plain property write the
# game uses, `chara.id = id` (obj/BaseObj.gd:228), which is not one of
# the 41 methods.

var real = FGObject.new()
var out_path: String = ""

func _init():
	out_path = OS.get_environment("FGOBJECT_DIFF_OUT")

func _set(property, value):
	if property == "id":
		var before = real.get_data()
		real.id = value
		_write("set_id", [value], before, real.get_data(), null)
		return true
	return false

func _get(property):
	if property == "id":
		return real.id
	return null

func to_json_value(v):
	var t = typeof(v)
	match t:
		TYPE_NIL:
			return null
		TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return v
		TYPE_ARRAY:
			var a = []
			for item in v:
				a.append(to_json_value(item))
			return a
		TYPE_DICTIONARY:
			var d = {}
			for k in v.keys():
				d[str(k)] = to_json_value(v[k])
			return d
		_:
			return str(v)

func _write(method: String, args: Array, before, after, result):
	if out_path.empty():
		return
	var f = File.new()
	if f.open(out_path, File.READ_WRITE) != OK:
		return
	f.seek_end()
	var line = {
		"obj": get_instance_id(),
		"method": method,
		"args": to_json_value(args),
		"before": to_json_value(before),
		"after": to_json_value(after),
		"result": to_json_value(result),
	}
	f.store_line(JSON.print(line))
	f.close()

func _call0(method: String):
	var before = real.get_data()
	var result = real.call(method)
	_write(method, [], before, real.get_data(), result)
	return result

func _call1(method: String, a):
	var before = real.get_data()
	var result = real.call(method, a)
	_write(method, [a], before, real.get_data(), result)
	return result

func _call2(method: String, a, b):
	var before = real.get_data()
	var result = real.call(method, a, b)
	_write(method, [a, b], before, real.get_data(), result)
	return result

# --- the 41 FGObject methods, each forwarded through _callN above ----------

func get_data():
	return _call0("get_data")

func apply_force(x, y):
	return _call2("apply_force", x, y)

func apply_force_str(x, y):
	return _call2("apply_force_str", x, y)

func apply_force_relative(x, y):
	return _call2("apply_force_relative", x, y)

func apply_force_relative_str(x, y):
	return _call2("apply_force_relative_str", x, y)

func apply_forces():
	return _call0("apply_forces")

func apply_forces_no_limit():
	return _call0("apply_forces_no_limit")

func apply_grav():
	return _call0("apply_grav")

func apply_grav_custom(grav, fall_speed):
	return _call2("apply_grav_custom", grav, fall_speed)

func apply_fric():
	return _call0("apply_fric")

func apply_x_fric(fric):
	return _call1("apply_x_fric", fric)

func apply_y_fric(fric):
	return _call1("apply_y_fric", fric)

func apply_pushback(dir):
	return _call1("apply_pushback", dir)

func reset_pushback():
	return _call0("reset_pushback")

func add_pushback(v):
	return _call1("add_pushback", v)

func reset_momentum():
	return _call0("reset_momentum")

func move_directly(x, y):
	return _call2("move_directly", x, y)

func move_directly_str(x, y):
	return _call2("move_directly_str", x, y)

func move_directly_relative(x, y):
	return _call2("move_directly_relative", x, y)

func move_directly_relative_str(x, y):
	return _call2("move_directly_relative_str", x, y)

func set_position(x, y):
	return _call2("set_position", x, y)

func set_position_str(x, y):
	return _call2("set_position_str", x, y)

func set_x(x):
	return _call1("set_x", x)

func set_y(y):
	return _call1("set_y", y)

func set_vel(x, y):
	return _call2("set_vel", x, y)

func set_facing(dir):
	return _call1("set_facing", dir)

func get_facing():
	return _call0("get_facing")

func get_x_vel_int():
	return _call0("get_x_vel_int")

func update_grounded():
	return _call0("update_grounded")

func is_grounded():
	return _call0("is_grounded")

func set_grounded(on):
	return _call1("set_grounded", on)

func set_snap_to_ground(snap):
	return _call1("set_snap_to_ground", snap)

func get_snap_to_ground():
	return _call0("get_snap_to_ground")

func set_gravity(v):
	return _call1("set_gravity", v)

func set_gravity_modifier(v):
	return _call1("set_gravity_modifier", v)

func set_ground_friction(v):
	return _call1("set_ground_friction", v)

func set_air_friction(v):
	return _call1("set_air_friction", v)

func set_max_ground_speed(v):
	return _call1("set_max_ground_speed", v)

func set_max_air_speed(v):
	return _call1("set_max_air_speed", v)

func set_max_fall_speed(v):
	return _call1("set_max_fall_speed", v)

# copy_to mutates `target`, not `self`: self's own get_data() never
# changes, so the useful before/after pair is the target's. One
# record carries both objects' states, instead of forcing the
# comparison tool to join two call-log lines across two different
# recorders.
func copy_to(target):
	var source_before = real.get_data()
	var target_before = target.real.get_data()
	real.copy_to(target.real)
	var target_after = target.real.get_data()
	if out_path.empty():
		return
	var f = File.new()
	if f.open(out_path, File.READ_WRITE) != OK:
		return
	f.seek_end()
	var line = {
		"obj": get_instance_id(),
		"method": "copy_to",
		"args": [],
		"target_obj": target.get_instance_id(),
		"source_before": to_json_value(source_before),
		"target_before": to_json_value(target_before),
		"target_after": to_json_value(target_after),
	}
	f.store_line(JSON.print(line))
	f.close()
