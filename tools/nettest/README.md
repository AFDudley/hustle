# Network test

This tool runs one real match of Your Only Move Is HUSTLE between two
Godot processes, over `Network.gd`'s direct multiplayer (ENet over UDP),
through a relay that adds latency, jitter and packet loss. It answers one
question: does online play stay deterministic and in sync under realistic
internet conditions.

It reuses `tools/replay_check`'s trace format and comparison code
unchanged (`TraceHooks.gd`, `StateSnapshot.gd`, `ignore.txt`,
`compare_traces`, `snapshot_diff`). See that tool's README for the trace
format and the container image.

## Usage

```sh
tools/nettest/run.py --replay PATH --profile {lan,typical,bad} [--seed N]
```

- `--replay PATH` is the source replay. Its recorded inputs drive both
  sides of the live match, one input per turn, in the order the turns
  happen (not by looking the live tick up directly; see "Turn order"
  below).
- `--profile` picks the relay's link parameters (see "Profiles" below).
- `--seed N` seeds the relay's delay, jitter and loss draws (default 1).
  It is not the match's own RNG seed: the live match reuses the source
  replay's seed, so its logic RNG matches an offline playback of the same
  replay exactly (see "Checks" below, check b).
- `--work DIR` sets the prepared project directory (default
  `~/.cache/hustle-nettest`), reusing `tools/replay_check/container.sh`'s
  `prepare` step. `--out DIR` sets where runs write their output (default
  `.scratch/nettest/runs`).
- `--timeout N` is each Godot process's deadline, in seconds (default 180).
  A deadline is not a tuning knob: if a run needs longer, state why before
  raising it.
- The exit code is 0 when both checks (a) and (b) pass, 1 when a check
  fails, and 2 when a step cannot run (for example, the match itself did
  not finish).

Each run writes `.scratch/nettest/runs/<run-id>/report.json` and prints it
to stdout. `<run-id>/host/`, `<run-id>/client/` and `<run-id>/playback/`
hold each side's trace, dump, turn log, Godot log and saved replay.

## Profiles

| Profile | One-way delay | Jitter | Loss | Reorder |
|---|---|---|---|---|
| `lan` | 0 ms | 0 ms | 0% | no |
| `typical` | 40 ms | 10 ms | 0.5% | no |
| `bad` | 120 ms | 40 ms | 2% | yes |

These are `udp-netsim`'s own parameters (`--delay-ms`, `--jitter-ms`,
`--loss`, `--reorder`); see `tools/replay_check/netsim/src/main.rs`'s
doc comment for exactly what each one does to a datagram.

## How a run works

`tools/nettest/run.py` runs on the host and orchestrates podman; it never
runs Godot or `udp-netsim` itself (see `tools/replay_check/README.md`,
"The container", for the image). For one run, it:

1. Builds the image and prepares the project copy
   (`tools/replay_check/build.sh`, `container.sh ... prepare`).
2. Creates one podman network and starts the host peer's container
   (`tools/nettest/run_peer.py`, which starts Xvfb and
   `tools/nettest/NetPlay.gd` through `NetPlay.tscn`).
3. Starts the relay (`udp-netsim`), with `--upstream` set to the host
   peer's address, and waits for its "listening on" log line.
4. Waits for the host peer's "NETTEST_READY" log line (its ENet server
   bound), then starts the client peer, pointed at the relay's address.
5. Waits for both peers to exit, reads each one's `NETTEST_DONE` result
   line, and collects the relay's forwarded/dropped counters.
6. Plays the host's saved replay back offline, through
   `tools/replay_check/ReplayCheck.gd` unchanged (one more short-lived
   container), for check (b).
7. Removes every container and network it created, writes
   `report.json`.

## The driver (`NetPlay.gd`)

One Godot process is one peer (host or client), chosen by the job file
`run.py` writes it. Match setup is traced from the real menus
(`ui/Lobby.gd`, `ui/CSS/CharacterSelect.gd`, `main.gd`) and calls the same
`Network.gd` functions in the same order: `host_game_direct` /
`join_game_direct`, `assign_players` + `begin_game` (host only, once both
sides are registered), `select_character`, the host's
`rpc_("send_match_data", ...)` once both sides picked, then
`game.start_game(false, match_data)`. `match_data` and the recorded
inputs both come from the source replay (`ReplayManager.load_replay`), so
the live match plays the same characters, settings and seed the replay
was made with.

Per turn, the driver feeds the recorded input for whichever side
`Network.gd` assigned it (sides are **not** fixed to host=P1,
client=P2: `Network.assign_players` shuffles them) with the same two
calls `ui/ActionSelector/ActionButtons.gd on_action_submitted` makes for
a real player's choice:

```gdscript
Network.player_objects[my_player_id].on_action_selected(action, data, extra)
Network.submit_action(action, data, extra)
```

This is the live code path, not the replay-playback path. A loaded
replay's inputs are fed by `characters/BaseChar.gd tick_before()`, which
sets `queued_action` directly and never calls `on_action_selected` (see
that function's own comment on the Movement-aura difference between the
two paths). A network match always runs the live path, so the driver
must call the same function a real panel calls, not reimplement the
playback path.

The driver reacts to `Network.player_turns_synced`, the same signal
`ui/UILayer.gd` opens the action menu on
(`Network.connect("player_turns_synced", self, "on_player_actionable")`).
It does not submit at once. It waits until `game.p1_turn` and
`game.p2_turn` are both set, then submits.

Reason: `game.gd`'s turn-start block (about lines 1452–1493) runs over
two frames. The p1 branch runs on one frame, and the p2 branch runs on
the next. The p2 branch sets `p1.state_interruptable = true`
(`game.gd:1474`). `player_turns_synced` can fire between the two frames.
An action submitted there is undone, and the match deadlocks: the
submitting fighter is interruptable again, and the other peer waits
forever for its `opponent_tick`. The first version of this driver
submitted at once. All 6 of its runs stalled early, at ticks 0 to 349. The
tick-0 stalls showed this exact state. After the fix, 7 of 7 runs finished
(2026-11-13, two replays, lan, typical and bad). A human
never clicks within one frame, so real players do not see this. An
instant bot, such as an AI mod, can. It is a defect of the game's turn
protocol, and this driver works around it the way a human does, by
acting after the turn-start block finishes.

### Turn order and divergence

`ReplayManager.frames[id][tick]` is keyed by `decision_tick + 1` (see
`characters/BaseChar.gd tick_before()`). The driver reads each player's
recorded ticks in ascending order, one entry per turn, and compares the
**next** one to the live match's own `current_tick + 1` when that turn
opens. If they match, it submits the recorded action. If the replay has
no more turns for this player, or the tick does not match, it logs a
divergence (`turn`, `player`, `live_tick`, and the recorded/expected tick
if there was one) and submits `"Continue"` instead, for the rest of the
match. This keeps the match running instead of stalling, so the report
shows how far it diverged rather than only that it did.

## Checks

Each run computes:

- **(a) Host vs. client, same live match.** `tools/replay_check`'s
  `compare_traces` on the two peers' per-tick hash traces. A difference
  means the two processes disagreed about the simulation state while
  playing the same match — the determinism/sync question this tool
  exists to answer. On a difference, `report.json`'s `check_a` carries
  the first divergent tick, which objects differed, and (via
  `snapshot_diff` on each side's full per-tick dump) the exact script
  variables that differed.
- **(b) The live match vs. an offline playback of its own saved
  replay.** The host's saved replay, played back through
  `tools/replay_check/ReplayCheck.gd` unchanged, compared against the
  host's own live trace. A difference here is the known live-path vs.
  replay-path divergence (see "The driver" above) made concrete for this
  specific match, not a bug in this tool.
- **(c) Turns, finish state, latency.** `report.json`'s `check_c` carries
  each side's turn-submission wait time (`min`, `mean`, `p50`, `p95`,
  `max`, from each `turns.jsonl`), and each side's own `NETTEST_DONE`
  result carries `turns_submitted`, `game_finished`, `final_tick`,
  `divergences`, and `forfeiter` (`Network.forfeiter`, 0 if nobody
  forfeited). `report.json`'s `relay` carries the relay's own forwarded
  and dropped datagram counts per direction.

Nothing here is tuned to make a check pass: a profile's delay, jitter and
loss are `udp-netsim`'s own parameters, and the match's inputs are the
source replay's, unmodified.

## Limits

- Three containers run per live match (relay, host, client), plus one
  short-lived container for the offline playback check. Run profiles one
  at a time; do not start more than one network test's containers at
  once alongside other 3-game-container work on this host.
- The relay's delay, jitter and loss draws are seeded
  (`--seed`), but a run's actual packet timing still depends on process
  and container scheduling, so a run is not bit-for-bit reproducible
  between invocations, only its link parameters are.
- `Network.assign_players` shuffles which physical process becomes
  side 1 and which becomes side 2. The driver reacts to whichever side
  `Network.gd` assigns it instead of assuming host=P1; `report.json`
  states each side's `player_id` and role.
