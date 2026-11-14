extends Node

# Headless driver for one replay-check job. run.py writes a JSON job file and
# starts Godot with `--rc-job=<path>`. See tools/replay_check/README.md.
#
# Modes:
#   generate  Play one live singleplayer match. Two real ActionButtons panels
#             (the in-game action menu) decide which actions are legal. A
#             seeded RNG clicks a random legal button, and sometimes toggles
#             the reverse, feint and character-extra buttons. Writes the
#             replay through ReplayManager.save_replay, and a trace.
#   playback  Load a replay and run it through ReplayManager.playback, the
#             path that the in-game replay viewer uses. Writes a trace.
#   live      Load a replay, then play its recorded inputs through the live
#             path a real match runs: ReplayManager.playback stays false,
#             game.gd's turn-start block runs (including on_interrupt), and
#             each input enters through Fighter.on_action_selected, the call
#             the action panel makes. Singleplayer, so no network protocol;
#             tools/nettest covers that. At the end, the inputs the game
#             recorded must equal the replay's inputs. Writes a trace.
#
# A trace has one JSON line per simulation tick:
#   {"t": tick, "h": hash of the tick, "g": hash of the Game vars,
#    "o": {object name: hash of the object}}
# Each hash is the SHA-256 of a StateSnapshot.gd dictionary. For each tick in
# job.dump_ticks, the full snapshot also goes to job.dump.

const StateSnapshot = preload("res://tools/replay_check/StateSnapshot.gd")
const TraceHooks = preload("res://tools/replay_check/TraceHooks.gd")
const Legality = preload("res://tools/replay_check/Legality.gd")
const GAME_SCENE = preload("res://Game.tscn")
const ACTION_BUTTONS_SCENE = preload("res://ui/ActionSelector/ActionButtons.tscn")
const SETTINGS_SCENE = preload("res://ui/GameSettingsPanelContainer.tscn")

const JOB_ARG = "--rc-job="

var job = {}
var ignore = {}
var dump_ticks = {}
var game = null
var panels = []
var rng = RandomNumberGenerator.new()
var trace = File.new()
var dump = File.new()
var census = File.new()
var turn_active = false
var turns = 0
var ticks_traced = 0
var finished = false
var stop_pending = false
var recorded = {}
var last_input_tick = -1

func _ready():
	# A job runs with --fixed-fps and must not wait for the display. On a GPU
	# (container.sh --gpu), vsync capped a measured replay at exactly 60.0 fps;
	# with vsync off it ran at 252 fps. Godot 3.5 has no command-line flag
	# for this. project.godot does not set display/window/vsync/use_vsync, so
	# the default (true) applies.
	OS.vsync_enabled = false
	job = _read_job()
	if job.empty():
		return
	for var_name in job.get("ignore", []):
		ignore[var_name] = true
	for t in job.get("dump_ticks", []):
		dump_ticks[int(t)] = true
	if !_open(trace, job.trace):
		return
	if !dump_ticks.empty() and !_open(dump, job.dump):
		return
	match job.mode:
		"generate":
			_generate()
		"playback":
			_playback()
		"live":
			_live()
		_:
			_fail("unknown mode " + str(job.mode))

func _read_job() -> Dictionary:
	for arg in OS.get_cmdline_args():
		if arg.begins_with(JOB_ARG):
			var f = File.new()
			var path = arg.substr(JOB_ARG.length())
			if f.open(path, File.READ) != OK:
				_fail("cannot read job file " + path)
				return {}
			var parsed = JSON.parse(f.get_as_text())
			f.close()
			if parsed.error != OK or typeof(parsed.result) != TYPE_DICTIONARY:
				_fail("job file is not a JSON object: " + path)
				return {}
			return parsed.result
	_fail("missing " + JOB_ARG + "<path>")
	return {}

func _open(file: File, path: String) -> bool:
	if file.open(path, File.WRITE) != OK:
		_fail("cannot write " + path)
		return false
	return true

func _fail(message: String):
	finished = true
	printerr("REPLAY_CHECK_FAIL: " + message)
	get_tree().quit(1)

func _new_game():
	game = GAME_SCENE.instance()
	var hooks = game.get_node("Hooks")
	hooks.set_script(TraceHooks)
	hooks.listener = funcref(self, "_on_post_tick")
	add_child(game)

func _generate():
	rng.seed = int(job.seed)
	var settings = SETTINGS_SCENE.instance()
	add_child(settings)
	var match_data = settings.get_data()
	remove_child(settings)
	settings.free()
	match_data["singleplayer"] = true
	match_data["selected_characters"] = {1: {"name": job.p1}, 2: {"name": job.p2}}
	match_data["seed"] = int(job.seed)
	ReplayManager.init()
	ReplayManager.playback = false
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the characters " + job.p1 + ", " + job.p2)
		return
	_make_panels()
	game.connect("player_actionable", self, "_on_player_actionable")

func _playback():
	var match_data = ReplayManager.load_replay(job.replay)
	ReplayManager.playback = true
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the replay characters")

func _live():
	var match_data = ReplayManager.load_replay(job.replay)
	recorded = ReplayManager.frames.duplicate(true)
	for id in [1, 2]:
		for t in recorded[id].keys():
			last_input_tick = int(max(last_input_tick, int(t)))
	ReplayManager.init()
	ReplayManager.playback = false
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the replay characters")
		return
	if job.has("census") and !_open(census, job.census):
		return
	game.connect("player_actionable", self, "_on_live_actionable")

# The same waits as _on_player_actionable: the turn-start block in game.gd
# sets p1_turn and p2_turn on two separate frames, and its second frame sets
# p1.state_interruptable again, so an input applied in between is lost.
# The replay keys an input by the tick that applies it: decision tick + 1.
func _on_live_actionable():
	if turn_active or finished:
		return
	turn_active = true
	while !game.game_paused:
		yield(get_tree(), "idle_frame")
	while !(game.p1_turn or !game.p1.state_interruptable) or !(game.p2_turn or !game.p2.state_interruptable):
		yield(get_tree(), "physics_frame")
	var key = game.current_tick + 1
	if census.is_open():
		_write_census(key)
	for id in [1, 2]:
		var fighter = game.get_player(id)
		var has_input = recorded[id].has(key)
		if fighter.state_interruptable != has_input:
			_fail("live path diverged at tick %d: player %d interruptable=%s, replay input=%s" % [game.current_tick, id, str(fighter.state_interruptable), str(has_input)])
			return
		if has_input:
			var input = recorded[id][key]
			fighter.on_action_selected(input["action"], input["data"], input["extra"])
	while !finished and game.is_waiting_on_player():
		yield(get_tree(), "physics_frame")
	turns += 1
	turn_active = false

# One JSON line per decision, written after game.gd's turn-start block and
# before the inputs apply: each player's state, whether it acts busy, the
# legal action list for its recorded extras (Legality.gd), and its input.
func _write_census(key):
	var line = {"t": game.current_tick}
	for id in [1, 2]:
		var p = game.get_player(id)
		var input = recorded[id].get(key)
		var extra = input["extra"] if input != null else null
		line["p" + str(id)] = {
			"state": str(p.current_state().name),
			"combo": p.combo_count,
			"hp": p.hp,
			"meter": p.supers_available,
			"burst": p.bursts_available,
			"grounded": p.is_grounded(),
			"feinting": p.feinting,
			"acts": p.state_interruptable,
			"busy": p.busy_interrupt,
			"legal": Legality.legal_actions(p, extra) if p.state_interruptable else [],
			"input": input,
		}
	census.store_line(JSON.print(line, "", true))

# start_game returns false for an unknown character. In playback it yields,
# so the result can also be a GDScriptFunctionState.
func _rejected(result) -> bool:
	return typeof(result) == TYPE_BOOL and result == false

# Mirrors the P1ActionButtons/P2ActionButtons pair in Main.tscn and the
# pairing in BottomBar.gd. The holder enters the tree after both panels are
# its children, so each panel's _ready can resolve opponent_action_buttons_path.
func _make_panels():
	var holder = Node.new()
	holder.name = "Panels"
	for id in [1, 2]:
		var panel = ACTION_BUTTONS_SCENE.instance()
		panel.name = "P" + str(id)
		panel.player_id = id
		panel.opponent_action_buttons_path = NodePath("../P" + str(3 - id))
		holder.add_child(panel)
		panels.append(panel)
	add_child(holder)
	panels[0].opposite_buttons = panels[1]
	panels[1].opposite_buttons = panels[0]
	panels[0].init(game, 1)
	panels[1].init(game, 2)

# Mirrors UILayer.on_player_actionable: wait for the pause, open both panels,
# then lock in one choice per panel.
func _on_player_actionable():
	if turn_active or finished:
		return
	turn_active = true
	while !game.game_paused:
		yield(get_tree(), "idle_frame")
	# process_tick opens the turn for one player per physics frame.
	while !(game.p1_turn or !game.p1.state_interruptable) or !(game.p2_turn or !game.p2.state_interruptable):
		yield(get_tree(), "physics_frame")
	for panel in panels:
		panel.hide()
		panel.activate()
	yield(_idle_frames(3), "completed")
	for panel in panels:
		if panel.fighter.state_interruptable:
			_toggle_extras(panel.fighter_extra)
	yield(_idle_frames(3), "completed")
	for panel in panels:
		if panel.fighter.state_interruptable:
			_click_random_action(panel)
	yield(_idle_frames(3), "completed")
	for panel in panels:
		if panel.fighter.state_interruptable:
			_toggle_if_enabled(panel.get_node("%ReverseButton"), job.reverse_chance)
			_toggle_if_enabled(panel.get_node("%FeintButton"), job.feint_chance)
			panel._on_submit_pressed()
	while !finished and game.is_waiting_on_player():
		yield(get_tree(), "physics_frame")
	turns += 1
	for panel in panels:
		panel.hide()
	turn_active = false

func _idle_frames(count: int):
	for i in range(count):
		yield(get_tree(), "idle_frame")

# Character-specific options (Cowboy's Shift and Milk, and others) are toggle
# buttons inside the panel's PlayerExtra node.
func _toggle_extras(node: Node):
	for child in node.get_children():
		if child is BaseButton and child.toggle_mode:
			if child.is_visible_in_tree():
				_toggle_if_enabled(child, job.extra_toggle_chance)
		else:
			_toggle_extras(child)

func _toggle_if_enabled(button: BaseButton, chance: float):
	if !button.disabled and rng.randf() < chance:
		button.pressed = !button.pressed

# ActionButtons.update_buttons shows exactly the legal buttons and hides
# the rest, so `visible` is the legality flag.
func _click_random_action(panel):
	var options = []
	for button in panel.buttons:
		if is_instance_valid(button) and button.visible and _button_enabled(button):
			options.append(button)
	if options.empty():
		return
	var button = options[rng.randi_range(0, options.size() - 1)]
	if button == panel.continue_button:
		button.on_pressed()
	else:
		button.on_toggled(true)

func _button_enabled(button) -> bool:
	if button is BaseButton:
		return !button.disabled
	return !button.get_node("%Button").disabled

func _physics_process(_delta):
	if finished or game == null or !game.game_started:
		return
	if stop_pending:
		_finish()
		return
	# A replay that stops before the end of the match hands control back to
	# the players. That point is the end of the playback.
	if job.mode == "playback" and !ReplayManager.playback and game.is_waiting_on_player():
		_finish()
	# A live run whose replay stops before the end of the match ends at the
	# first decision after the replay's last input. A finished match ends the
	# way playback does, through stop_pending in _on_post_tick, so both modes
	# trace the same last tick.
	if job.mode == "live" and !game.game_finished and !turn_active and game.is_waiting_on_player() and game.current_tick + 1 > last_input_tick:
		_finish()

func _on_post_tick():
	if finished or stop_pending:
		return
	var snap = StateSnapshot.game_state(game, ignore)
	var obj_hashes = {}
	for obj_name in snap.objs:
		obj_hashes[obj_name] = JSON.print(snap.objs[obj_name], "", true).sha256_text()
	var game_hash = JSON.print(snap.game, "", true).sha256_text()
	var tick_hash = (game_hash + JSON.print(obj_hashes, "", true)).sha256_text()
	trace.store_line(JSON.print({"t": game.current_tick, "h": tick_hash, "g": game_hash, "o": obj_hashes}, "", true))
	ticks_traced += 1
	if dump_ticks.has(game.current_tick):
		dump.store_line(JSON.print({"t": game.current_tick, "s": snap}, "", true))
	# end_game runs after post_tick, so the first traced tick with
	# game_finished set is the tick after the match ended.
	# A dump job exists only to show one tick, so it stops after the last one.
	# The stop waits for the next physics frame: the rest of game.tick() runs
	# after post_tick, and it sets ReplayManager.frames.finished, which the
	# saved replay needs. As the parent node, this node gets
	# _physics_process before the Game queues another tick.
	if game.game_finished or (!dump_ticks.empty() and game.current_tick >= dump_ticks.keys().max()):
		stop_pending = true

func _finish():
	finished = true
	var replay_path = ""
	if job.mode == "generate":
		var file_name = ReplayManager.save_replay(game.match_data, "replay_check_" + str(job.seed))
		replay_path = ProjectSettings.globalize_path("user://replay/" + file_name)
		var dir = Directory.new()
		if dir.copy(replay_path, job.replay_out) != OK:
			_fail("cannot copy " + replay_path + " to " + job.replay_out)
			return
	if job.mode == "live":
		# The live path records each applied input in ReplayManager.frames.
		# They must be the replay's inputs, or this run played another match.
		# A dump run stops at its last dump tick, so only the inputs up to the
		# tick this run reached are checked.
		for id in [1, 2]:
			for t in recorded[id].keys():
				if int(t) > game.current_tick:
					continue
				var got = ReplayManager.frames[id].get(t)
				if got == null or JSON.print(got, "", true) != JSON.print(recorded[id][t], "", true):
					_fail("live run recorded a different input for player %d at tick %d" % [id, int(t)])
					return
	trace.close()
	if dump.is_open():
		dump.close()
	if census.is_open():
		census.close()
	print("REPLAY_CHECK_DONE " + JSON.print({
		"mode": job.mode,
		"final_tick": game.current_tick,
		"ticks_traced": ticks_traced,
		"turns": turns,
		"game_finished": game.game_finished,
		"p1_hp": game.p1.hp,
		"p2_hp": game.p2.hp,
	}))
	get_tree().quit(0)
