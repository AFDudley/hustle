extends Reference

# Converts the simulation state of a Game node into a JSON-safe Dictionary.
#
# The snapshot covers:
#   - the script variables of the Game node,
#   - for each entry of game.objs_map, sorted by name: its script variables,
#     the name and script variables of its current state, and the native
#     FGObject data from chara.get_data() (lib/tbfg.so).
#
# The snapshot takes every script variable and makes no guess about which
# variables belong to the simulation. ReplayCheck.gd removes variables only
# through an explicit ignore list in the job file, so each exclusion is
# visible and reviewable. See tools/replay_check/README.md.

const MAX_DEPTH = 6

static func game_state(game, ignore: Dictionary) -> Dictionary:
	var objs = {}
	var names = game.objs_map.keys()
	names.sort()
	for obj_name in names:
		var obj = game.objs_map[obj_name]
		if is_instance_valid(obj):
			objs[str(obj_name)] = object_state(obj, ignore)
	return {
		"game": script_vars(game, ignore),
		"objs": objs,
	}

static func object_state(obj, ignore: Dictionary) -> Dictionary:
	var out = {"vars": script_vars(obj, ignore)}
	var sm = obj.get("state_machine")
	if sm != null and is_instance_valid(sm) and sm.state != null:
		out["state"] = str(sm.state.name)
		out["state_vars"] = script_vars(sm.state, ignore)
	var chara = obj.get("chara")
	if chara != null:
		out["native"] = encode(chara.get_data(), 0)
	return out

# Script variables only: engine properties (position, modulate, ...) are
# either derived from the script variables or are presentation.
static func script_vars(obj, ignore: Dictionary, depth: int = 0) -> Dictionary:
	var out = {}
	for prop in obj.get_property_list():
		if not (prop.usage & PROPERTY_USAGE_SCRIPT_VARIABLE):
			continue
		if ignore.has(prop.name):
			continue
		out[prop.name] = encode(obj.get(prop.name), depth)
	return out

# Floats and vectors go through var2str so the text keeps every digit.
# Dictionary keys become strings. JSON.print(..., sort_keys=true) orders them.
# A Node becomes its name: sim objects have deterministic names (P1, P2,
# projectile names from objs_spawned_count), and the node itself is
# captured under its own objs_map entry. An auto-generated name becomes
# "@auto". A RandomNumberGenerator (BetterRng
# drives logic_rng) becomes its seed and state. Another object with a script
# becomes its script variables. Any other object becomes its class name.
static func encode(value, depth: int):
	match typeof(value):
		TYPE_NIL, TYPE_BOOL, TYPE_INT, TYPE_STRING:
			return value
		TYPE_DICTIONARY:
			if depth >= MAX_DEPTH:
				return "<depth>"
			var out = {}
			for k in value:
				# str() of an object key includes its instance id, which
				# changes from run to run. Encode the key like a value instead.
				# Insertion order is deterministic, so a suffix keeps two keys
				# with the same encoding apart.
				var key = str(k) if typeof(k) != TYPE_OBJECT else JSON.print(encode(k, MAX_DEPTH))
				while out.has(key):
					key += "#"
				out[key] = encode(value[k], depth + 1)
			return out
		TYPE_ARRAY, TYPE_INT_ARRAY, TYPE_REAL_ARRAY, TYPE_STRING_ARRAY, TYPE_VECTOR2_ARRAY, TYPE_COLOR_ARRAY, TYPE_RAW_ARRAY:
			if depth >= MAX_DEPTH:
				return "<depth>"
			var out = []
			for item in value:
				out.append(encode(item, depth + 1))
			return out
		TYPE_OBJECT:
			if not is_instance_valid(value):
				return "<freed>"
			if value is Node:
				# Godot names an unnamed node "@@<n>" or "@<class>@<n>". The
				# number counts nodes made so far, menu nodes included, so it
				# is not simulation state.
				if str(value.name).begins_with("@"):
					return "<node:@auto>"
				return "<node:" + str(value.name) + ">"
			if value is RandomNumberGenerator:
				return {"<rng>": {"seed": value.seed, "state": value.get("state")}}
			if value.get_script() != null and depth < MAX_DEPTH:
				return {"<" + value.get_class() + ">": script_vars(value, {}, depth + 1)}
			return "<" + value.get_class() + ">"
		_:
			return var2str(value)
