extends Node

# Headless UI driver for one ui_test job. run.py writes a JSON job file and
# starts Godot with `--ui-job=<path>`. See tools/ui_test/README.md.
#
# This script is an autoload (registered by run.py into the work copy's
# project.godot, after ModOverride). The game still boots through its own
# normal path: project.godot's run/main_scene loads Main.tscn, exactly as
# a player's own launch does. This script never instances Main.tscn itself
# and never calls a UI handler function directly. It drives the game with
# synthetic InputEventMouseButton and InputEventKey events, routed through
# Input.parse_input_event (the same entry point real OS input uses), so
# real Button "pressed"/"toggled" signals, real Control focus, and real
# hotkey actions run.
#
# Being an autoload (not part of Main's scene tree) matters once the match
# ends: quitting to the main menu calls Global.reload(), which is
# SceneTree.reload_current_scene() under the hood. That frees and reloads
# only the current scene (Main), the same way the autoloads Global,
# ModLoader and friends already survive it during ordinary play. A driver
# living inside Main.tscn would be freed by the same reload.
#
# Coordinate note: a synthetic mouse event's position must be given in the
# OS window's pixel space, not the Control canvas space Control.get_rect()
# reports. project.godot sets window/stretch/mode="viewport" with a design
# resolution of 640x360 and a test window of 1280x720; OS.window_size stays
# at the real window size while Viewport.size stays at the design
# resolution. A probe scene confirmed empirically (see the handoff report)
# that multiplying a Control's canvas-space center by
# OS.window_size / get_viewport().size lands the click on that Control;
# without the scale, clicks land on whichever ancestor happens to cover
# the unscaled point (usually the wrong one, or none).

const JOB_ARG = "--ui-job="

# Character/mod loading runs on a background Thread; real wall time, not
# simulation ticks, so this budget is generous.
const MAIN_MENU_BUDGET = 3600
# Ordinary UI transitions (menu navigation, panel activation).
const UI_BUDGET = 900
# One turn: panel activation plus the tick(s) it takes to resolve.
const TURN_BUDGET = 1800

var job := {}
var milestones := []
var result_file := File.new()
var rng := RandomNumberGenerator.new()
var start_msec := 0
var finished := false
var turns := 0
var game = null
var main_node: Node = null
var p1_panel = null
var p2_panel = null
var _save_before: Array = []

func _ready():
	start_msec = OS.get_ticks_msec()
	job = _read_job()
	if job.empty():
		return
	rng.seed = int(job.get("seed", 1))
	if result_file.open(job.milestones, File.WRITE) != OK:
		_fail("cannot write " + str(job.milestones))
		return
	_run()

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

func _fail(message: String):
	if finished:
		return
	finished = true
	printerr("UI_TEST_FAIL: " + message)
	if result_file.is_open():
		result_file.close()
	get_tree().quit(1)

func _milestone(name: String, extra := {}):
	var entry = {
		"milestone": name,
		"t_msec": OS.get_ticks_msec() - start_msec,
		"t_unix": OS.get_unix_time(),
	}
	for k in extra:
		entry[k] = extra[k]
	milestones.append(entry)
	result_file.store_line(JSON.print(entry))
	result_file.flush()
	print("UI_TEST_MILESTONE " + JSON.print(entry))

# --- input helpers -------------------------------------------------------

func _screen_pos(canvas_pos: Vector2) -> Vector2:
	return canvas_pos * (Vector2(OS.window_size) / Vector2(get_viewport().size))

func _click(control: Control):
	var rect = control.get_global_rect()
	var pos = _screen_pos(rect.position + rect.size / 2.0)
	var down = InputEventMouseButton.new()
	down.button_index = BUTTON_LEFT
	down.pressed = true
	down.position = pos
	down.global_position = pos
	Input.parse_input_event(down)
	yield(get_tree(), "idle_frame")
	var up = InputEventMouseButton.new()
	up.button_index = BUTTON_LEFT
	up.pressed = false
	up.position = pos
	up.global_position = pos
	Input.parse_input_event(up)
	yield(get_tree(), "idle_frame")

func _press_key(physical_scancode: int):
	var down = InputEventKey.new()
	down.physical_scancode = physical_scancode
	down.pressed = true
	Input.parse_input_event(down)
	yield(get_tree(), "idle_frame")
	var up = InputEventKey.new()
	up.physical_scancode = physical_scancode
	up.pressed = false
	Input.parse_input_event(up)
	yield(get_tree(), "idle_frame")

# --- wait helpers ----------------------------------------------------------

func _wait_until(pred: FuncRef, budget: int, what: String):
	# A GDScript coroutine that returns without ever reaching a `yield`
	# gives its caller the return value directly, not a
	# GDScriptFunctionState -- so `yield(_wait_until(...), "completed")`
	# would crash with "First argument of yield() not of type object"
	# whenever the predicate is already true on the first check (observed
	# in the end-to-end run: character_select was already true by the
	# time this ran, since _click() had already yielded past it). This
	# unconditional yield guarantees every call returns a proper
	# coroutine state.
	yield(get_tree(), "idle_frame")
	var n = 1
	while not pred.call_func():
		if finished:
			return
		if n >= budget:
			_fail(what + " not reached within " + str(budget) + " frames")
			return
		yield(get_tree(), "idle_frame")
		n += 1

func _idle_frames(n: int):
	for i in range(n):
		yield(get_tree(), "idle_frame")

func _find(name: String) -> Node:
	var scene = get_tree().current_scene
	if scene == null or not is_instance_valid(scene):
		return null
	return scene.find_node(name, true, false)

# --- predicates (polled by _wait_until) -------------------------------------

func _pred_main_menu() -> bool:
	if not Global.mods_loaded:
		return false
	var mm = _find("MainMenu")
	return mm != null and mm.visible

func _pred_character_select() -> bool:
	var cs = _find("CharacterSelect")
	if cs == null or not cs.visible:
		return false
	var container = _find("CharacterButtonContainer")
	return container != null and container.get_child_count() > 0

func _pred_go_enabled() -> bool:
	var go = _find("GoButton")
	return go != null and not go.disabled

func _pred_game_exists() -> bool:
	return main_node != null and is_instance_valid(main_node.game)

func _pred_panels_active() -> bool:
	return is_instance_valid(p1_panel) and is_instance_valid(p2_panel) \
		and p1_panel.visible and p1_panel.active \
		and p2_panel.visible and p2_panel.active

func _pred_turn_resolved() -> bool:
	return finished or game.game_finished or not game.is_waiting_on_player()

# Same "always yield first" guard as _wait_until. A panel's own selection
# keeps re-validating against the ghost/prediction pass right up to
# lock-in (ActionButtons._process's auto-fallback to Continue when the
# picked button stops being visible); a click can land in the middle of
# that and resolve to nothing, the same way a real player's click can
# miss a button that moved under the cursor. Observed: a single click (or
# three retries a few dozen frames apart) sometimes was not enough, but a
# later click always was. So this re-clicks any panel that is still
# active and not locked in every RECLICK_PERIOD frames, the way a real
# player would keep clicking a button that does not seem to respond,
# instead of giving up after one attempt; it only fails once the whole
# turn budget is spent.
func _wait_turn_resolved(budget: int, turn: int):
	yield(get_tree(), "idle_frame")
	var reclick_period = 30
	var n = 1
	while not _pred_turn_resolved():
		if finished:
			return
		if n >= budget:
			_fail("turn %d resolution not reached within %d frames; p1 active=%s locked_in=%s current_action=%s; p2 active=%s locked_in=%s current_action=%s; game p1_turn=%s p2_turn=%s game_paused=%s waiting=%s" % [
				turn, budget,
				p1_panel.active, p1_panel.locked_in, p1_panel.current_action,
				p2_panel.active, p2_panel.locked_in, p2_panel.current_action,
				game.p1_turn, game.p2_turn, game.game_paused, game.is_waiting_on_player(),
			])
			return
		if n % reclick_period == 0:
			for panel in [p1_panel, p2_panel]:
				if panel.active and not panel.locked_in and not panel.get_node("%SelectButton").disabled:
					yield(_click(panel.get_node("%SelectButton")), "completed")
		yield(get_tree(), "idle_frame")
		n += 1

func _pred_pause_visible() -> bool:
	var pp = _find("PausePanel")
	return pp != null and pp.visible

func _pred_replay_saved() -> bool:
	return _newest_replay(_save_before) != null

# --- flow --------------------------------------------------------------

func _run():
	yield(_wait_until(funcref(self, "_pred_main_menu"), MAIN_MENU_BUDGET, "main_menu"), "completed")
	if finished:
		return
	_milestone("main_menu")
	main_node = get_tree().current_scene

	var sp_button = _find("SingleplayerButton")
	if sp_button == null:
		_fail("SingleplayerButton not found")
		return
	yield(_click(sp_button), "completed")

	yield(_wait_until(funcref(self, "_pred_character_select"), UI_BUDGET, "character_select"), "completed")
	if finished:
		return
	_milestone("character_select")

	yield(_pick_character(1, job.p1), "completed")
	if finished:
		return
	_milestone("p1_selected", {"character": job.p1})

	yield(_pick_character(2, job.p2), "completed")
	if finished:
		return
	_milestone("p2_selected", {"character": job.p2})

	var go_button = _find("GoButton")
	if go_button == null:
		_fail("GoButton not found")
		return
	yield(_wait_until(funcref(self, "_pred_go_enabled"), UI_BUDGET, "go_button_enabled"), "completed")
	if finished:
		return
	yield(_click(go_button), "completed")

	yield(_wait_until(funcref(self, "_pred_game_exists"), UI_BUDGET, "match_started"), "completed")
	if finished:
		return
	game = main_node.game
	p1_panel = _find("P1ActionButtons")
	p2_panel = _find("P2ActionButtons")
	if p1_panel == null or p2_panel == null:
		_fail("action button panels not found")
		return
	_milestone("match_started", {"p1": job.p1, "p2": job.p2})

	while not finished and not game.game_finished:
		yield(_wait_until(funcref(self, "_pred_panels_active"), TURN_BUDGET, "turn %d actionable" % (turns + 1)), "completed")
		if finished:
			return
		turns += 1
		_milestone("turn_%d_actionable" % turns, {"tick": game.current_tick})
		yield(_play_turn(), "completed")
		if finished:
			return
		yield(_wait_turn_resolved(TURN_BUDGET, turns), "completed")
		if finished:
			return
	if finished:
		return
	_milestone("match_ended", {
		"turns": turns,
		"tick": game.current_tick,
		"p1_hp": game.p1.hp,
		"p2_hp": game.p2.hp,
	})

	yield(_press_key(KEY_ESCAPE), "completed")
	yield(_wait_until(funcref(self, "_pred_pause_visible"), UI_BUDGET, "pause_menu_open"), "completed")
	if finished:
		return
	_milestone("pause_menu_open")

	_save_before = _replay_names()
	var save_button = _find("SaveReplayButton")
	if save_button == null:
		_fail("SaveReplayButton not found")
		return
	yield(_click(save_button), "completed")
	yield(_wait_until(funcref(self, "_pred_replay_saved"), UI_BUDGET, "replay_saved"), "completed")
	if finished:
		return
	var saved_name = _newest_replay(_save_before)
	_milestone("replay_saved", {"file": ProjectSettings.globalize_path("user://replay/" + saved_name)})

	var quit_button = _find("QuitToMainMenuButton")
	if quit_button == null:
		_fail("QuitToMainMenuButton not found")
		return
	yield(_click(quit_button), "completed")
	yield(_wait_until(funcref(self, "_pred_main_menu"), UI_BUDGET, "returned_to_menu"), "completed")
	if finished:
		return
	_milestone("returned_to_menu")

	_finish()

# Real-click character select: find the CharacterButton with this name's
# text and click it, then wait for CharacterSelect's own selection state
# (not a shortcut call) to record the pick.
func _pick_character(player_id: int, char_name: String):
	# Same "always yield first" guard as _wait_until: _fail() below can
	# return before any other yield runs.
	yield(get_tree(), "idle_frame")
	var cs = _find("CharacterSelect")
	var container = _find("CharacterButtonContainer")
	if cs == null or container == null:
		_fail("character select UI not found")
		return
	var button = null
	for child in container.get_children():
		if "text" in child and child.text == char_name:
			button = child
			break
	if button == null:
		_fail("character button not found: " + char_name)
		return
	yield(_click(button), "completed")
	var n = 0
	while cs.selected_characters.get(player_id) == null:
		if n >= UI_BUDGET:
			_fail("character " + char_name + " not selected within " + str(UI_BUDGET) + " frames")
			return
		yield(get_tree(), "idle_frame")
		n += 1

# One turn, both players: read the real legal-button state of each panel
# (ActionButtons.update_buttons already hid the illegal ones), click one
# legal button per panel, then click each panel's own lock-in button.
# Only a fighter that is state_interruptable is owed a submission this
# turn -- ReplayCheck.gd's bot gates the same way. A panel whose fighter
# is busy (hurt, mid-move) keeps re-resetting its own selection back to
# Continue every frame regardless of what gets clicked (observed: a
# submission attempt on such a panel never resolved, even after repeated
# retries; see the handoff report).
func _play_turn():
	for panel in [p1_panel, p2_panel]:
		if panel.fighter.state_interruptable:
			yield(_click_legal_action(panel), "completed")
			if finished:
				return
	for panel in [p1_panel, p2_panel]:
		if panel.fighter.state_interruptable:
			yield(_lock_in(panel), "completed")
			if finished:
				return
	yield(_idle_frames(2), "completed")

# Clicking an action button starts a multi-frame chain in ActionButtons.gd
# (_send_ui_action: buffer, emit action_clicked, toggle can_lock_in,
# update_select_button) before %SelectButton's disabled state reflects
# the new pick. Wait for it for real, the way a player's own eyes would,
# instead of guessing a fixed frame count.
func _lock_in(panel):
	yield(get_tree(), "idle_frame")
	var select_button = panel.get_node("%SelectButton")
	var n = 1
	while select_button.disabled:
		if n >= UI_BUDGET:
			_fail("player %d: SelectButton stayed disabled" % panel.player_id)
			return
		yield(get_tree(), "idle_frame")
		n += 1
	# The panel keeps re-validating legality right up to lock-in, so this
	# first click can land mid-change and resolve to nothing (see
	# _wait_turn_resolved, which re-clicks a panel that is still active
	# and not locked in).
	yield(_click(select_button), "completed")

func _click_legal_action(panel):
	# Same "always yield first" guard as _wait_until.
	yield(get_tree(), "idle_frame")
	var options = []
	for button in panel.buttons:
		if is_instance_valid(button) and button.visible and not button.get_disabled():
			options.append(button)
	if options.empty():
		_fail("no visible/enabled action buttons for player " + str(panel.player_id))
		return
	var button = options[rng.randi_range(0, options.size() - 1)]
	var clickable = button.get_node("%Button") if button.has_node("%Button") else button
	yield(_click(clickable), "completed")

func _replay_names() -> Array:
	var names = []
	var dir = Directory.new()
	if dir.open("user://replay") != OK:
		return names
	dir.list_dir_begin(true, true)
	var f = dir.get_next()
	while f != "":
		names.append(f)
		f = dir.get_next()
	dir.list_dir_end()
	return names

func _newest_replay(before: Array):
	for name in _replay_names():
		if not before.has(name):
			return name
	return null

func _finish():
	finished = true
	result_file.close()
	print("UI_TEST_DONE " + JSON.print({
		"turns": turns,
		"milestones": milestones.size(),
		"last_milestone": (milestones[-1].milestone if not milestones.empty() else ""),
	}))
	get_tree().quit(0)
