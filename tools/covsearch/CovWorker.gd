extends Node

# Headless coverage-directed-search worker for tools/covsearch. Reused
# patterns from tools/replay_check/ReplayCheck.gd: the two real
# ActionButtons panels decide legality, Legality.gd is the oracle to check
# against (design step 4), and `game.gd`'s turn-start block runs over two
# frames (the same wait as ReplayCheck.gd _on_live_actionable).
#
# Operator decision: no ghost branching. LIVE is the only simulation path
# this tool uses, both to explore and to credit coverage.
#
# Operator decision (this session): never run two live `Game` nodes in one
# process. An earlier version built a second, real `Game` inside the same
# process as the job's own reference `game` to save process-startup cost;
# a `replay_prefix`-rooted child then froze (README.md's "Blocking defect",
# now fixed by removing the second Game, not by chasing the freeze). Every
# build of a `Game` now happens through `_build_root_and_choices`, which
# first frees any previous `Game` and its panels completely
# (`_free_game`/`_free_panels`) and resets `ReplayManager` before building
# the next one, so at most one live `Game` ever exists at a time in this
# process -- matching `tools/replay_check/ReplayCheck.gd`'s own one-Game-
# per-job shape. A `branch_batch` job runs several branches this way, one
# after another, never concurrently (the brief's named optimisation: pay
# the process-startup cost once per batch instead of once per branch).
#
# One job = one root (a decision point), named by `job.base`:
#   {"mode": "match_start", "seed": N}
#     A fresh tournament-settings match (design step 2), Ninja vs Ninja.
#   {"mode": "replay_prefix", "replay": path, "up_to_tick": N}
#     Replay a human mirror's own recorded inputs, on the live path, up to
#     and including tick N's inputs, to reach one of its decision points
#     (design step 3). Keeps that replay's own settings (its match_data).
# `job.choices` (optional): `[[p1in, p2in], ...]`, the path's own
# already-kept choices, replayed after the root through on_action_selected.
#
# `job.action` picks what this job does at that (root, choices) node:
#   "list_candidates" (default): reads the real ActionButtons legal-button
#     lists for both players and the Legality.gd computed lists (recording
#     every difference), then writes the discretized candidate list for
#     each player (design step 5). Does not branch.
#   "branch_batch": for each pair in `job.pairs`, rebuilds the (root,
#     choices) node on a brand-new live Game (`_build_root_and_choices`),
#     applies that one (p1, p2) input pair through on_action_selected,
#     waits for the next decision point (or match end) the same way
#     `tools/replay_check`'s "live" mode does, and records the probe-count
#     delta and a projection of the resulting state. One `branch` record
#     per pair.
# `job.write_replay`: builds (root, choices) once and saves the replay.
# `base.mode` can also be "verify_replay" (determinism check, design step
# 10) or "replay_scan" (design step 3's root finder); both are self-
# contained and build exactly one `Game`, same as every other mode here.
#
# Output: one JSON object per line to job.out.
const Legality = preload("res://tools/replay_check/Legality.gd")
const GAME_SCENE = preload("res://Game.tscn")
const ACTION_BUTTONS_SCENE = preload("res://ui/ActionSelector/ActionButtons.tscn")
const SETTINGS_SCENE = preload("res://ui/GameSettingsPanelContainer.tscn")

const JOB_ARG = "--rc-job="
# Tournament settings (lab book decision 3) that differ from the default
# lobby and reach the simulation.
const TOURNAMENT_OVERRIDE = {
	"stage_width": 600,
	"game_length": 3000,
	"ceiling_height": 500,
	"starting_meter": "1.5",
}
# 8 compass directions plus zero, at the full extent of the aim/DI percent
# range (-100..100), matching the data UI's own range (lab book section 2.2).
const DIRECTIONS = [
	[0, 0], [100, 0], [100, 100], [0, 100], [-100, 100],
	[-100, 0], [-100, -100], [0, -100], [100, -100],
]
var job = {}
var out = File.new()
var game = null
var panels = []
var recorded = {}
var match_data = {}
var finished = false

func _ready():
	OS.vsync_enabled = false
	job = _read_job()
	if job.empty():
		return
	if out.open(job.out, File.WRITE) != OK:
		_fail("cannot write " + str(job.out))
		return
	_run()

func _read_job() -> Dictionary:
	for arg in OS.get_cmdline_args():
		if arg.begins_with(JOB_ARG):
			var f = File.new()
			if f.open(arg.substr(JOB_ARG.length()), File.READ) != OK:
				_fail("cannot read job file")
				return {}
			var parsed = JSON.parse(f.get_as_text())
			f.close()
			return parsed.result
	_fail("missing " + JOB_ARG + "<path>")
	return {}

func _fail(message: String):
	finished = true
	printerr("COVSEARCH_FAIL: " + message)
	get_tree().quit(1)

func _write(line: Dictionary):
	out.store_line(JSON.print(line))

func _new_game():
	game = GAME_SCENE.instance()
	add_child(game)

# --- root construction -------------------------------------------------

func _run():
	var base = job.get("base", job)
	if base.mode == "verify_replay":
		yield(_verify_replay(base.replay), "completed")
		out.close()
		get_tree().quit(0)
		return
	if base.mode == "replay_scan":
		yield(_scan_replay(base.replay), "completed")
		out.close()
		get_tree().quit(0)
		return
	if job.has("write_replay"):
		yield(_build_root_and_choices(base, job.get("choices", [])), "completed")
		if finished:
			return
		_write_replay(job.write_replay)
		out.close()
		get_tree().quit(0)
		return
	var action = job.get("action", "list_candidates")
	if action == "branch_batch":
		yield(_branch_batch(base, job.get("choices", []), job.get("pairs", [])), "completed")
	elif action == "list_candidates":
		yield(_build_root_and_choices(base, job.get("choices", [])), "completed")
		if !finished:
			yield(_list_candidates(), "completed")
	else:
		_fail("unknown job action " + str(action))
	out.close()
	get_tree().quit(0)

# Frees the current `Game` (if any) completely before a new one is built,
# so at most one live Game ever exists in this process at a time. Mirrors
# what a fresh `tools/replay_check` job gets for free by being a fresh
# process: no state from the old match survives into the new one.
func _free_game():
	if game != null and is_instance_valid(game):
		remove_child(game)
		game.free()
	game = null

func _free_panels():
	panels = []
	var holder = get_node_or_null("Panels")
	if holder != null:
		remove_child(holder)
		holder.free()

# Builds one fresh, real (non-ghost) Game: the root (design step 3) plus
# the path's own already-kept choices (through on_action_selected, the
# live path), live, from tick 0. The single mechanism every job in this
# file uses to reach a (root, choices) node -- `list_candidates`,
# `branch_batch` (once per pair, see header), and `write_replay` all call
# this. Returns the built `game`, or null on failure (`finished` is set).
func _build_root_and_choices(base: Dictionary, choices: Array):
	_free_game()
	_free_panels()
	ReplayManager.init()
	ReplayManager.playback = false
	match base.mode:
		"match_start":
			yield(_start_match(int(base.seed)), "completed")
		"replay_prefix":
			yield(_start_replay_prefix(base.replay, int(base.up_to_tick)), "completed")
		_:
			_fail("unknown base mode " + str(base.mode))
			return null
	if finished:
		return null
	if game.game_finished:
		_fail("root replay ended before reaching a decision point")
		return null
	for choice in choices:
		yield(_apply_pair_live(game, choice[0], choice[1]), "completed")
		if finished:
			return null
		if game.game_finished:
			_fail("root replay ended before all of this path's recorded choices were applied; non-determinism, not an expected path end")
			return null
	return game

# Corpus writer (design step 9). `game` is a real (non-ghost) match that has
# already applied the path's choices through on_action_selected, so
# ReplayManager.frames (BaseChar.gd on_state_started, "if !is_ghost") holds
# exactly those inputs, keyed by tick. save_replay writes the same binary
# .replay format a real match or tools/replay_check generate produces.
func _write_replay(name: String):
	var file_name = ReplayManager.save_replay(match_data, name)
	var path = OS.get_user_data_dir() + "/replay/" + file_name
	_write({"kind": "replay_written", "path": path, "tick": game.current_tick})

# Determinism check (design step 10). Loads a corpus replay and plays its
# recorded inputs on the live path, exactly as tools/replay_check/
# ReplayCheck.gd mode "live" does, applying each input through
# Fighter.on_action_selected. Fails if the game ever disagrees with the
# replay about who acts, same rule ReplayCheck.gd _on_live_actionable
# enforces. Reports the final-tick projection and the full cumulative
# probe id set this live process has hit since tick 0 (`_hit_ids`), which
# run.py's caller compares between two independent runs of the same
# replay (corpus.check_fidelity): identical projection and identical
# probe hits is the determinism check the brief asks for.
func _verify_replay(replay_path: String):
	yield(get_tree(), "idle_frame")
	match_data = ReplayManager.load_replay(replay_path)
	recorded = ReplayManager.frames.duplicate(true)
	var last_tick = -1
	for id in [1, 2]:
		for t in recorded[id].keys():
			last_tick = int(max(last_tick, int(t)))
	ReplayManager.init()
	ReplayManager.playback = false
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the replay characters")
		return
	while game.current_tick < last_tick and !finished and !game.game_finished:
		yield(_wait_decision(game), "completed")
		if finished or game.game_finished:
			break
		var key = game.current_tick + 1
		for id in [1, 2]:
			var fighter = game.get_player(id)
			var has_input = recorded[id].has(key)
			if fighter.state_interruptable != has_input:
				_fail("live path diverged at tick %d: player %d interruptable=%s, replay input=%s" % [game.current_tick, id, str(fighter.state_interruptable), str(has_input)])
				return
			if has_input:
				var input = recorded[id][key]
				fighter.on_action_selected(input["action"], input["data"], input["extra"])
		yield(_wait_resolved(game), "completed")
	_write({"kind": "verify_result", "final_tick": game.current_tick,
		"game_over": game.game_finished, "projection": _projection(game),
		"hit_ids": _hit_ids()})

# replay_scan (design step 3): play one human mirror's own recorded inputs
# on the live path from tick 0 to its end. At every decision point, run
# the legality oracle (design step 4, cheap: two UI refreshes) and write
# its tick as a root_tick record, so the coordinator can later re-anchor
# a replay_prefix root at that exact tick. It does NOT run the full
# discretized branch expansion here: run.py scan_roots only reads
# root_tick records (every branch/candidate record would be discarded),
# and the coordinator re-expands each root from scratch anyway, so doing
# the full candidate cross product at every decision of a whole human
# match was pure wasted compute -- confirmed by timing it: one replay's
# scan passed 9,000+ JSONL records without reaching its last tick.
func _scan_replay(replay_path: String):
	yield(get_tree(), "idle_frame")
	match_data = ReplayManager.load_replay(replay_path)
	recorded = ReplayManager.frames.duplicate(true)
	var last_tick = -1
	for id in [1, 2]:
		for t in recorded[id].keys():
			last_tick = int(max(last_tick, int(t)))
	ReplayManager.init()
	ReplayManager.playback = false
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the replay characters")
		return
	_make_panels()
	while game.current_tick < last_tick and !finished and !game.game_finished:
		yield(_wait_decision(game), "completed")
		if finished or game.game_finished:
			return
		_write({"kind": "root_tick", "replay": replay_path, "tick": game.current_tick})
		yield(_legality_report(), "completed")
		if finished:
			return
		var key = game.current_tick + 1
		for id in [1, 2]:
			if recorded[id].has(key):
				var input = recorded[id][key]
				game.get_player(id).on_action_selected(input["action"], input["data"], input["extra"])
		yield(_wait_resolved(game), "completed")

func _start_match(match_seed: int):
	yield(get_tree(), "idle_frame")
	var settings = SETTINGS_SCENE.instance()
	add_child(settings)
	match_data = settings.get_data()
	remove_child(settings)
	settings.free()
	for key in TOURNAMENT_OVERRIDE:
		match_data[key] = TOURNAMENT_OVERRIDE[key]
	match_data["singleplayer"] = true
	match_data["selected_characters"] = {1: {"name": "Ninja"}, 2: {"name": "Ninja"}}
	match_data["seed"] = match_seed
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected Ninja/Ninja")
		return
	_make_panels()
	yield(_wait_decision(game), "completed")

func _start_replay_prefix(replay_path: String, up_to_tick: int):
	yield(get_tree(), "idle_frame")
	match_data = ReplayManager.load_replay(replay_path)
	recorded = ReplayManager.frames.duplicate(true)
	ReplayManager.init()
	ReplayManager.playback = false
	_new_game()
	if _rejected(game.start_game(true, match_data)):
		_fail("start_game rejected the replay characters")
		return
	_make_panels()
	while game.current_tick < up_to_tick and !finished:
		yield(_wait_decision(game), "completed")
		var key = game.current_tick + 1
		for id in [1, 2]:
			if recorded[id].has(key):
				var input = recorded[id][key]
				game.get_player(id).on_action_selected(input["action"], input["data"], input["extra"])
		yield(_wait_resolved(game), "completed")

func _rejected(result) -> bool:
	return typeof(result) == TYPE_BOOL and result == false

func _make_panels():
	var holder = Node.new()
	holder.name = "Panels"
	panels = []
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

# Waits for game.gd's turn-start block (two frames, as in ReplayCheck.gd).
# Parameterized by `g` so the same, single, trusted mechanism drives every
# live Game this file ever builds.
func _wait_decision(g):
	yield(get_tree(), "idle_frame")
	while !g.game_paused and !g.game_finished and !finished:
		yield(get_tree(), "idle_frame")
	if g.game_finished or finished:
		return
	while !(g.p1_turn or !g.p1.state_interruptable) or !(g.p2_turn or !g.p2.state_interruptable):
		yield(get_tree(), "physics_frame")
		if g.game_finished:
			return

func _wait_resolved(g):
	yield(get_tree(), "idle_frame")
	while !finished and !g.game_finished and g.is_waiting_on_player():
		yield(get_tree(), "physics_frame")

# --- legality oracle (design step 4) ------------------------------------

func _legality_report():
	yield(get_tree(), "idle_frame")
	for id in [1, 2]:
		var fighter = game.get_player(id)
		if !fighter.state_interruptable:
			continue
		var panel = panels[id - 1]
		panel.activate()
		yield(_idle_frames(3), "completed")
		var ui_visible = []
		for button in panel.buttons:
			if is_instance_valid(button) and button.visible and !button.get_disabled():
				ui_visible.append(button.action_name)
		var extra = panel.get_extra()
		var computed = Legality.legal_actions(fighter, extra)
		var ui_set = {}
		for a in ui_visible:
			ui_set[a] = true
		var computed_set = {}
		for a in computed:
			computed_set[a.rstrip("*")] = true
		var only_ui = []
		var only_computed = []
		for a in ui_visible:
			if !computed_set.has(a):
				only_ui.append(a)
		for a in computed:
			if !ui_set.has(a.rstrip("*")):
				only_computed.append(a)
		if only_ui.size() > 0 or only_computed.size() > 0:
			_write({"kind": "legality_diff", "tick": game.current_tick, "player": id,
				"ui_only": only_ui, "legality_gd_only": only_computed})

func _idle_frames(count: int):
	for i in range(count):
		yield(get_tree(), "idle_frame")

# --- discretization (design step 5) -------------------------------------

# Builds the discrete value classes for one data node's default value
# (design step 5, extended per README.md "Discretization completeness").
# A whole-dict {"x","y"} value (most actions' aim plot) becomes the 9
# compass-direction classes. A dict of named sub-fields -- for example
# Parry's "Melee Parry Timing"/"Block Height" (NewParry.gd _enter) or
# StickyBomb's "Jump"/"Aim" (StickyBombActionUIData.gd) -- varies one
# sub-field at a time from the shared default: each {"x","y"} sub-field
# gets the 9 classes, each boolean sub-field gets both values. This is
# the same "one axis at a time from a shared baseline" reduction the
# DI/toggle axes already use, not a full cross product. A plain boolean
# data value (none found on Ninja, kept for completeness) gets both
# values. Any other leaf (for example "Melee Parry Timing"'s own
# {"count": N}, which the game sets from the opponent's attack timing,
# not a free player choice) keeps its one default value -- a named, not
# a silent, reduction.
func _data_variants(default_data) -> Array:
	if default_data == null:
		return [null]
	if typeof(default_data) == TYPE_BOOL:
		return [true, false]
	if typeof(default_data) != TYPE_DICTIONARY:
		return [default_data]
	if default_data.has("x") and default_data.has("y"):
		var out = []
		for dir in DIRECTIONS:
			var d = default_data.duplicate(true)
			d["x"] = str(dir[0])
			d["y"] = str(dir[1])
			out.append(d)
		return out
	var variants = [default_data.duplicate(true)]
	for key in default_data.keys():
		var value = default_data[key]
		if typeof(value) == TYPE_DICTIONARY and value.has("x") and value.has("y"):
			for dir in DIRECTIONS:
				var d = default_data.duplicate(true)
				d[key] = value.duplicate(true)
				d[key]["x"] = str(dir[0])
				d[key]["y"] = str(dir[1])
				variants.append(d)
		elif typeof(value) == TYPE_BOOL:
			var d2 = default_data.duplicate(true)
			d2[key] = !value
			variants.append(d2)
	return variants

func _candidates(player_id: int) -> Array:
	var fighter = game.get_player(player_id)
	var out_candidates = []
	if !fighter.state_interruptable:
		return [{"action": "Continue", "data": null, "extra": null}]
	var panel = panels[player_id - 1]
	var base_extra = panel.get_extra()
	out_candidates.append({"action": "Continue", "data": null, "extra": base_extra.duplicate(true)})
	for button in panel.buttons:
		if !is_instance_valid(button) or !button.visible or button.name == "ContinueButton":
			continue
		var action_name = button.state.state_name if button.state else str(button.name)
		var has_aim = button.data_node != null
		var default_data = button.data_node.get_data() if has_aim else null
		var data_variants = _data_variants(default_data) if has_aim else [null]
		for i in range(data_variants.size()):
			var data = data_variants[i]
			var extra = base_extra.duplicate(true)
			out_candidates.append({"action": action_name, "data": data, "extra": extra})
			# One DI class per direction, one toggle flip each, independent
			# of the data-variant loop (design step 5: "each extra toggle
			# on/off, feint on/off"), tried once at the baseline data
			# variant to bound the candidate count: this is a deliberate
			# reduction from the full cross product, recorded in
			# tools/covsearch/README.md.
			if i == 0:
				for di in DIRECTIONS:
					var e2 = base_extra.duplicate(true)
					e2["DI"] = {"x": str(di[0]), "y": str(di[1])}
					out_candidates.append({"action": action_name, "data": data, "extra": e2})
				for toggle in ["reverse", "feint"]:
					if base_extra.has(toggle):
						var e3 = base_extra.duplicate(true)
						e3[toggle] = !e3[toggle]
						out_candidates.append({"action": action_name, "data": data, "extra": e3})
	return out_candidates

# --- candidate listing (design step 5) ----------------------------------

func _list_candidates():
	yield(_legality_report(), "completed")
	if finished:
		return
	var cap = int(job.get("max_per_player", 1000000))
	var p1_candidates = _candidates(1)
	var p2_candidates = _candidates(2)
	if p1_candidates.size() > cap:
		p1_candidates = p1_candidates.slice(0, cap - 1)
	if p2_candidates.size() > cap:
		p2_candidates = p2_candidates.slice(0, cap - 1)
	_write({"kind": "candidate_count", "tick": game.current_tick,
		"p1": p1_candidates.size(), "p2": p2_candidates.size()})
	_write({"kind": "candidates", "tick": game.current_tick,
		"p1": p1_candidates, "p2": p2_candidates})

# --- branching (design step 6, live-only, one Game at a time) ----------
#
# Operator decision: no ghost branching, and no second live Game
# coexisting with another one in this process (see header comment and
# README.md "Blocking defect", now closed by this design). For each
# candidate pair, `_build_root_and_choices` frees whatever Game this
# process built last and builds a fresh one: the job's own root plus its
# already-kept choices, live, from tick 0 -- the same construction
# `_list_candidates`'s own root uses. `_apply_pair_live` then applies the
# candidate pair and waits for the next decision point
# (`_wait_decision`/`_wait_resolved`, the single, shared wait mechanism
# every live drive in this file uses). The probe-count delta and a
# projection of the resulting state are recorded. A `branch_batch` job
# runs its pairs this way one after another, never two Games at once --
# the brief's named optimisation for amortising one process's startup
# cost over several branches, measured in README.md "Cost measurement".

func _apply_pair_live(g, p1in: Dictionary, p2in: Dictionary):
	yield(get_tree(), "idle_frame")
	g.get_player(1).on_action_selected(p1in.action, p1in.data, p1in.extra)
	g.get_player(2).on_action_selected(p2in.action, p2in.data, p2in.extra)
	yield(_wait_resolved(g), "completed")
	if finished or g.game_finished:
		return
	yield(_wait_decision(g), "completed")

func _branch_batch(base: Dictionary, choices: Array, pairs: Array):
	for pair in pairs:
		var g = yield(_build_root_and_choices(base, choices), "completed")
		if finished:
			return
		var root_tick = g.current_tick
		var before = _probe_snapshot()
		yield(_apply_pair_live(g, pair[0], pair[1]), "completed")
		if finished:
			return
		var after = _probe_snapshot()
		_write({
			"kind": "branch", "tick": root_tick,
			"p1_input": pair[0], "p2_input": pair[1],
			"probes": _probe_delta(before, after),
			"projection": _projection(g),
		})
	_free_game()
	_free_panels()

func _probe_snapshot() -> Array:
	return __COV__.h.duplicate()

func _probe_delta(before: Array, after: Array) -> Array:
	var hit = []
	for i in range(before.size()):
		if after[i] > before[i]:
			hit.append(i)
	return hit

# The full cumulative set of probe ids this process has hit since it
# started (tick 0 of the job's root). Used only by _verify_replay's
# determinism check (corpus.check_fidelity compares this set between two
# independent live runs of the same corpus replay).
func _hit_ids() -> Array:
	var ids = []
	var h = __COV__.h
	for i in range(h.size()):
		if h[i] > 0:
			ids.append(i)
	return ids

func _projection(g) -> Dictionary:
	var objs = {}
	for name in g.objs_map.keys():
		var o = g.objs_map[name]
		if !is_instance_valid(o):
			continue
		var entry = {
			"pos": o.get_pos(), "vel": o.get_vel(),
			"state": str(o.current_state().state_name) if o.has_method("current_state") else "",
		}
		if o is Fighter:
			entry["hp"] = o.hp
			entry["meter"] = o.supers_available
			entry["burst"] = o.bursts_available
			entry["hitlag"] = o.hitlag_ticks
			entry["hitstun"] = o.get("hitstun_ticks") if o.get("hitstun_ticks") != null else 0
		objs[str(name)] = entry
	return {
		"tick": g.current_tick, "game_over": g.game_finished,
		"p1_turn": g.p1_turn, "p2_turn": g.p2_turn, "objs": objs,
	}
