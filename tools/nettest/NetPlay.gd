extends Node

# Headless network-test peer. One Godot process is one side (host or
# client) of a direct-connect match, played over a real Network.gd session
# through a latency/loss relay (tools/replay_check/netsim). See
# tools/nettest/README.md and tools/nettest/run.py.
#
# Setup mirrors the real menus, traced from ui/Lobby.gd, ui/CSS/CharacterSelect.gd
# and main.gd, so the same Network.gd calls run in the same order:
#   host_game_direct / join_game_direct  (Lobby.gd _on_host_pressed / _on_join_pressed)
#   assign_players, begin_game           (Lobby.gd _on_start_pressed, host only)
#   select_character                     (CharacterSelect.gd init/_on_button_pressed)
#   rpc_("send_match_data", ...)         (CharacterSelect.gd _on_network_character_selected)
#   game.start_game(false, match_data)   (main.gd setup_game_deferred)
#
# Per turn, this driver feeds the action tools/replay_check's source replay
# recorded for the assigned side straight to the fighter and to the
# network layer:
#   Network.player_objects[id].on_action_selected(action, data, extra)
#   Network.submit_action(action, data, extra)
# ui/ActionSelector/ActionButtons.gd on_action_submitted makes the same two
# calls when a real player locks in a choice. This is the live code path,
# not the replay-playback path: characters/BaseChar.gd tick_before() feeds
# a loaded replay without calling on_action_selected at all (see its own
# comment on the Movement-aura difference between the two paths). A
# network match always runs the live path, so this driver must too.
#
# Turn order: ReplayManager.frames[id][tick] (tick = decision tick + 1,
# characters/BaseChar.gd tick_before()) is read in ascending tick order per
# player, one entry per turn, not by looking up the live tick directly.
# If the live tick does not match the recorded tick, or the replay has no
# more turns for this player, the driver logs a divergence and submits
# "Continue" from then on, so the match keeps running and the report shows
# how far it diverged.
#
# Output, reusing tools/replay_check's trace format unchanged
# (TraceHooks.gd, StateSnapshot.gd; run.py's read_trace/compare_traces/
# snapshot_diff read it as-is):
#   job.trace   one hash line per tick
#   job.dump    one full snapshot line per tick (every tick: a live match
#               runs once, so there is no later re-run to dump from)
#   job.turns   one line per turn: recorded tick, live tick, action, wall time
#   job.replay_out  the match this peer recorded, saved through ReplayManager

const StateSnapshot = preload("res://tools/replay_check/StateSnapshot.gd")
const TraceHooks = preload("res://tools/replay_check/TraceHooks.gd")
const GAME_SCENE = preload("res://Game.tscn")

const JOB_ARG = "--nt-job="

var job = {}
var ignore = {}
var game = null
var trace = File.new()
var dump = File.new()
var turns = File.new()

# Template match data and inputs read from the source replay before the
# live match starts. ReplayManager.load_replay sets ReplayManager.frames as
# a side effect, so recorded_frames is a copy taken before ReplayManager.init()
# clears it for the live match's own recording.
var template_match_data = {}
var recorded_frames = {}
var sorted_ticks = {1: [], 2: []}
var turn_index = {1: 0, 2: 0}
var diverged = {1: false, 2: false}
var divergences = []

var my_player_id = 0
var selected = {1: null, 2: null}
var game_starting = false
var finished = false
var stop_pending = false
var ticks_traced = 0
var turn_start_ms = 0


func _ready():
	# A job runs with --fixed-fps and must not wait for the display.
	# See tools/replay_check/ReplayCheck.gd for the same setting and reason.
	OS.vsync_enabled = false
	job = _read_job()
	if job.empty():
		return
	for var_name in job.get("ignore", []):
		ignore[var_name] = true
	if !_open(trace, job.trace) or !_open(dump, job.dump) or !_open(turns, job.turns):
		return
	_load_replay_template()
	_connect_network_signals()
	match job.role:
		"host":
			Network.host_game_direct(job.player_name, job.port)
			print("NETTEST_READY role=host port=" + str(job.port))
		"client":
			Network.join_game_direct(job.relay_host, job.relay_port, job.player_name)
			print("NETTEST_READY role=client")
		_:
			_fail("unknown role " + str(job.role))


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
	if finished:
		return
	finished = true
	printerr("NETTEST_FAIL: " + message)
	print("NETTEST_DONE " + JSON.print({"error": message}))
	get_tree().quit(1)


# Reads the source replay's match data and inputs. ReplayManager.frames is
# the live match's own recording buffer, so it must be copied out and reset
# before start_game runs, or the template's inputs and the live match's
# recorded inputs would be the same dictionary.
func _load_replay_template():
	template_match_data = ReplayManager.load_replay(job.replay)
	recorded_frames = ReplayManager.frames.duplicate(true)
	for id in [1, 2]:
		var keys = recorded_frames[id].keys()
		keys.sort()
		sorted_ticks[id] = keys
	ReplayManager.init()
	ReplayManager.playback = false


func _connect_network_signals():
	Network.connect("player_list_changed", self, "_on_player_list_changed")
	Network.connect("character_selected", self, "_on_character_selected")
	Network.connect("match_locked_in", self, "_on_match_locked_in")
	Network.connect("start_game", self, "_on_start_game")
	Network.connect("player_turns_synced", self, "_on_turns_synced")
	Network.connect("game_error", self, "_on_game_error")
	Network.connect("connection_failed", self, "_on_connection_failed")


# Mirrors ui/Lobby.gd _on_start_pressed: once both peers registered, the
# host assigns sides and starts the match. The client only reacts.
func _on_player_list_changed():
	if finished or game_starting:
		return
	if Network.is_host() and Network.players.size() >= 2:
		game_starting = true
		Network.assign_players()
		if !Network.ids_synced:
			yield(Network, "player_ids_synced")
		Network.begin_game()


# Mirrors ui/CSS/CharacterSelect.gd init(): pick the character the source
# replay recorded for whichever side Network.gd assigned this peer.
func _on_start_game():
	var char_name = template_match_data.selected_characters[Network.player_id]["name"]
	var style = null
	if template_match_data.has("selected_styles"):
		style = template_match_data.selected_styles[Network.player_id]
	Network.select_character(char_name, style)


# Mirrors CharacterSelect.gd _on_network_character_selected: once both
# sides picked, the host broadcasts the final match data.
func _on_character_selected(player_id, character, _style=null):
	selected[player_id] = character
	if selected[1] != null and selected[2] != null and Network.is_host():
		Network.rpc_("send_match_data", template_match_data)


# Mirrors CharacterSelect.gd _on_network_match_locked_in + go(), and
# main.gd setup_game_deferred: start the live Game with the agreed match
# data, exactly as a real match does past character select.
func _on_match_locked_in(match_data):
	my_player_id = Network.player_id
	ReplayManager.init()
	ReplayManager.playback = false
	game = GAME_SCENE.instance()
	var hooks = game.get_node("Hooks")
	hooks.set_script(TraceHooks)
	hooks.listener = funcref(self, "_on_post_tick")
	add_child(game)
	Network.game = game
	game.start_game(false, match_data)
	print("NETTEST_MATCH_STARTED player_id=" + str(my_player_id))


func _on_game_error(what):
	_fail("game_error: " + str(what))


func _on_connection_failed():
	_fail("connection_failed")


# ui/UILayer.gd opens the action menu on this same signal
# (Network.connect("player_turns_synced", self, "on_player_actionable")).
# It fires once both fighters are interruptable this round, i.e. once per
# shared turn, so this is the real "your turn" event, not a poll.
func _on_turns_synced():
	if finished or !is_instance_valid(game):
		return
	# Network.end_turn_simulation emits player_turns_synced from inside
	# game.gd's turn-start block (game.gd ~1452-1493). That block runs over
	# two frames: the p1 branch on one frame, the p2 branch on the next, and
	# the p2 branch sets p1.state_interruptable = true (game.gd:1474). An
	# action submitted between those two frames is undone, and the match
	# deadlocks with the submitting fighter interruptable again. A human
	# never clicks within one frame. An instant driver does. So wait until
	# the block has run both branches (p1_turn and p2_turn both set).
	while !(game.p1_turn and game.p2_turn):
		yield(get_tree(), "idle_frame")
		if finished or !is_instance_valid(game):
			return
	_submit_my_turn()
	turn_start_ms = OS.get_ticks_msec()


func _submit_my_turn():
	var pid = my_player_id
	var idx = turn_index[pid]
	var live_tick = game.current_tick
	# characters/BaseChar.gd tick_before() records a live action at
	# decision_tick + 1, the same key ReplayManager.frames uses.
	var expected_tick = live_tick + 1
	var action = "Continue"
	var data = null
	var extra = null
	var note = ""
	if diverged[pid]:
		note = "post-divergence fallback"
	elif idx >= sorted_ticks[pid].size():
		diverged[pid] = true
		note = "no recorded turn %d for player %d at live tick %d" % [idx, pid, live_tick]
		divergences.append({"turn": idx, "player": pid, "live_tick": live_tick, "reason": note})
	else:
		var recorded_tick = sorted_ticks[pid][idx]
		if recorded_tick != expected_tick:
			diverged[pid] = true
			note = "tick mismatch: recorded %d, expected %d (live tick %d)" % [recorded_tick, expected_tick, live_tick]
			divergences.append({
				"turn": idx, "player": pid, "live_tick": live_tick,
				"recorded_tick": recorded_tick, "expected_tick": expected_tick, "reason": note,
			})
		else:
			var frame = recorded_frames[pid][recorded_tick]
			action = frame.action
			data = frame.data
			extra = frame.extra
	turns.store_line(JSON.print({
		"turn": idx,
		"player": pid,
		"live_tick": live_tick,
		"expected_tick": expected_tick,
		"action": action,
		"note": note,
		"turn_wait_ms": (OS.get_ticks_msec() - turn_start_ms) if turn_start_ms > 0 else 0,
		"wall_ms": OS.get_ticks_msec(),
	}, "", true))
	# Flushed immediately, not only at _finish(): a stalled match (see
	# README.md, "Limits") is killed by run.py's timeout with no chance to
	# close files cleanly, and the turn log is the first evidence of how
	# far the match got.
	turns.flush()
	turn_index[pid] += 1
	# The same two calls ui/ActionSelector/ActionButtons.gd on_action_submitted
	# makes: apply the action to the local fighter, then notify the network
	# layer so it reaches the remote peer and records the turn-end tick.
	var fighter = Network.player_objects[pid]
	fighter.on_action_selected(action, data, extra)
	Network.submit_action(action, data, extra)


var last_watchdog_ms = 0

func _physics_process(_delta):
	if finished or game == null or !game.game_started:
		return
	if stop_pending:
		_finish()
		return
	# Diagnostic only: if the match is not advancing, this shows which
	# flag is stuck, without needing a new run to find out.
	var now = OS.get_ticks_msec()
	if now - last_watchdog_ms >= 2000:
		last_watchdog_ms = now
		print("NETTEST_WATCHDOG tick=" + str(game.current_tick)
			+ " waiting=" + str(game.is_waiting_on_player())
			+ " p1_interruptable=" + str(game.p1.state_interruptable)
			+ " p2_interruptable=" + str(game.p2.state_interruptable)
			+ " p1_turn=" + str(game.p1_turn) + " p2_turn=" + str(game.p2_turn)
			+ " network_simulate_ready=" + str(game.network_simulate_ready)
			+ " game_paused=" + str(game.game_paused))


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
	dump.store_line(JSON.print({"t": game.current_tick, "s": snap}, "", true))
	ticks_traced += 1
	if ticks_traced % 10 == 0:
		# Flushed periodically, not only at _finish(): see the same note
		# on turns.flush() above. Every tick would add needless I/O; every
		# ten loses at most nine ticks of evidence to a hard kill.
		trace.flush()
		dump.flush()
	if ticks_traced % 100 == 0:
		print("NETTEST_TICK " + str(game.current_tick) + " wall_ms=" + str(OS.get_ticks_msec()))
	if game.game_finished:
		stop_pending = true


func _finish():
	finished = true
	var file_name = ReplayManager.save_replay(game.match_data, "nettest_" + str(job.role))
	var replay_path = ProjectSettings.globalize_path("user://replay/" + file_name)
	var dir = Directory.new()
	if dir.copy(replay_path, job.replay_out) != OK:
		_fail("cannot copy " + replay_path + " to " + job.replay_out)
		return
	trace.close()
	dump.close()
	turns.close()
	print("NETTEST_DONE " + JSON.print({
		"role": job.role,
		"player_id": my_player_id,
		"final_tick": game.current_tick,
		"ticks_traced": ticks_traced,
		"turns_submitted": turn_index[my_player_id],
		"divergences": divergences,
		"game_finished": game.game_finished,
		"p1_hp": game.p1.hp,
		"p2_hp": game.p2.hp,
		"forfeiter": Network.forfeiter,
	}))
	get_tree().quit(0)
