extends Node

# Headless probe for tools/tablegen. Loads every scene named in the
# manifest (the five characters and every projectile scene their states
# reference), walks each one's `StateMachine`, and writes one JSON line
# per state, hitbox, and root record. tools/tablegen/compare.py checks
# these lines against `table_dump`'s own dump of the generated Rust
# tables. See tools/tablegen/README.md.
#
# This script reads field names from the manifest, not from a
# hand-written per-class list: `tools/tablegen/gen.py` is the one place
# that decides which fields a table keeps (see its `schema.py`). This
# script only knows how to read a property generically and how to spot
# a hitbox-like child, so the two sides cannot name different fields by
# accident.
#
# A property this node's script does not declare reads back as `null`
# (Godot's own `Object.get()` contract); such fields are left out of the
# record. `compare.py` treats a field missing from the probe's record as
# "not applicable to this state" and does not compare it -- the
# generated side still carries a value there (that mixin's own default),
# because every table of one scope shares one Rust struct shape.

const MANIFEST_ARG = "--tablegen-manifest="
const OUT_ARG = "--tablegen-out="

var out := File.new()
var record_count := 0
var manifest := {}


func write_record(record: Dictionary) -> void:
	out.store_line(JSON.print(record))
	record_count += 1


func get_prop(node: Object, name: String):
	if node.get(name) == null:
		return null
	return node.get(name)


func resolve_throw_positions(sprite: Object, tp: Dictionary) -> Array:
	# Mirrors gen.py's `resolve_throw_positions`: walk every animation
	# frame in the character's own `SpriteFrames`, and record a
	# position for every frame whose texture is a `throw_positions`
	# key. `tp` keys by the texture `Resource` itself (Godot dictionary
	# keys compare resources by reference), not a tick number.
	var result = []
	if tp == null or tp.empty():
		return result
	for anim_name in sprite.frames.get_animation_names():
		var frame_count = sprite.frames.get_frame_count(anim_name)
		for i in range(frame_count):
			var tex = sprite.frames.get_frame(anim_name, i)
			if tex != null and tp.has(tex):
				var pos = tp[tex]
				result.append({"animation": anim_name, "frame": i, "x": int(pos.x), "y": int(pos.y)})
	return result


func add_fields(record: Dictionary, node: Object, names: Array, sprite: Object) -> void:
	for name in names:
		var value = get_prop(node, name)
		if value == null:
			continue
		if name == manifest.projectile_scene_field:
			record[name] = value.resource_path
		elif name == "throw_positions":
			record[name] = resolve_throw_positions(sprite, value)
		else:
			record[name] = value


func is_hitbox_like(node: Object) -> bool:
	# Duck-typed, not `is Hitbox`: a loaded-but-untracked instance still
	# resolves class checks fine, but this mirrors gen.py's own test
	# (does the resolved script chain reach CollisionBox and Hitbox)
	# without needing the probe to preload those scripts by path.
	return node.get("x") != null and node.get("width") != null and node.get("damage") != null


func dump_scope(scope: Dictionary) -> void:
	var scene = load(scope.tscn)
	if scene == null:
		printerr("TABLEGEN_FAIL: cannot load " + str(scope.tscn))
		get_tree().quit(1)
		return
	var inst = scene.instance()
	var sprite = inst.get_node("Flip/Sprite")

	var root_record = {"scope": scope.key, "record": "root"}
	add_fields(root_record, inst, manifest.root_field_names, sprite)
	write_record(root_record)

	var state_machine = inst.get_node("StateMachine")
	for state_node in state_machine.get_children():
		var state_name = state_node.name
		var state_record = {"scope": scope.key, "record": "state", "state": state_name}
		add_fields(state_record, state_node, manifest.state_field_names, sprite)
		write_record(state_record)

		var hitboxes = []
		for child in state_node.get_children():
			if is_hitbox_like(child):
				hitboxes.append(child)
		hitboxes.sort_custom(self, "_by_name")
		for i in range(hitboxes.size()):
			var hitbox_record = {
				"scope": scope.key,
				"record": "hitbox",
				"state": state_name,
				"hitbox_index": i,
			}
			add_fields(hitbox_record, hitboxes[i], manifest.hitbox_field_names, sprite)
			write_record(hitbox_record)

	inst.free()


func _by_name(a: Object, b: Object) -> bool:
	return a.name < b.name


func _ready() -> void:
	var manifest_path := ""
	var out_path := ""
	for arg in OS.get_cmdline_args():
		if arg.begins_with(MANIFEST_ARG):
			manifest_path = arg.substr(MANIFEST_ARG.length())
		elif arg.begins_with(OUT_ARG):
			out_path = arg.substr(OUT_ARG.length())
	if manifest_path.empty() or out_path.empty():
		printerr("TABLEGEN_FAIL: need " + MANIFEST_ARG + "<path> and " + OUT_ARG + "<path>")
		get_tree().quit(1)
		return

	var f := File.new()
	if f.open(manifest_path, File.READ) != OK:
		printerr("TABLEGEN_FAIL: cannot read " + manifest_path)
		get_tree().quit(1)
		return
	var text := f.get_as_text()
	f.close()
	var parsed := JSON.parse(text)
	if parsed.error != OK:
		printerr("TABLEGEN_FAIL: cannot parse manifest: " + parsed.error_string)
		get_tree().quit(1)
		return
	manifest = parsed.result

	if out.open(out_path, File.WRITE) != OK:
		printerr("TABLEGEN_FAIL: cannot write " + out_path)
		get_tree().quit(1)
		return

	for scope in manifest.scopes:
		dump_scope(scope)

	out.close()
	print("TABLEGEN_DONE " + JSON.print({"records": record_count}))
	get_tree().quit(0)
