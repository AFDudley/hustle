# Headless UI test

This tool drives the real game UI, `Main.tscn`, with synthetic input
events. It runs inside the `tools/replay_check` container, on the same
GodotSteam 3.5.1 engine.

## How this differs from `tools/replay_check`

`tools/replay_check/ReplayCheck.gd` never loads `Main.tscn`. It instances
`Game.tscn` and two real `ActionButtons` panels directly, then "clicks"
them by calling `button.on_toggled(true)` and `panel._on_submit_pressed()`
in GDScript. That covers the simulation and the action menu's legality
logic, but it never runs `main.gd`, `UILayer.gd`, the main menu, or
character select, and a measured run covered only 17.3% of `ui/`.

This tool boots the real game the way a player's own launch does:
`project.godot`'s `run/main_scene` loads `Main.tscn`. It drives every
step -- main menu, singleplayer, character select, the action menu, the
pause menu, Save Replay, and the exit-to-menu path -- with synthetic
`InputEventMouseButton` and `InputEventKey` events sent through
`Input.parse_input_event`, the same entry point real OS input uses. Godot
routes each event through its own viewport hit-testing, so a click runs
the target `Button`'s real `pressed`/`toggled` signal, grants real
`Control` focus, and a key press runs a real hotkey action
(`Input.is_action_just_pressed`). No UI handler is called directly.

## Usage

```sh
tools/ui_test/container.sh generate --out matches/ --pairs Cowboy:Ninja --seeds 1
tools/ui_test/container.sh --coverage cov/ generate --out matches/ --pairs Cowboy:Ninja --seeds 1
```

- `--pairs` takes `all`, or a list such as `Cowboy:Ninja,Wizard:Robot`. The
  names come from `Global.name_paths`.
- `--seeds` takes a list or a range, such as `1,5,9` or `1-20`. The seed
  drives which legal action gets clicked each turn; see "The turn loop".
- `--work DIR` sets the work directory. The default is
  `~/.cache/hustle-ui-test`.
- `--jobs N` sets the number of parallel Godot processes.
- `--coverage DIR` measures GDScript line coverage the same way
  `tools/replay_check`'s `--coverage` does; see `tools/coverage/README.md`.
- The exit code is 0 when every match reaches `UI_TEST_DONE`, 1 when a
  match fails, and 2 when a step cannot run.

For each `<p1>_<p2>_<seed>`, `generate` writes
`<out>/<p1>_<p2>_<seed>.milestones.json`: the ordered milestone log (see
below), each with its elapsed time in milliseconds since the job started.

### The container

`tools/ui_test/container.sh` reuses the `localhost/hustle-replay-check`
image that `tools/replay_check/build.sh` builds; it does not build its own
image. The image's entrypoint is `tools/replay_check/run.py`, so
`container.sh` overrides it with `--entrypoint python3` and runs
`tools/ui_test/run.py` instead, with the same mounts (the repository
read-only, the work directory, and each `--out`/`--coverage` directory
read-write) as `tools/replay_check/container.sh`.

`run.py` reuses `tools/replay_check/run.py`'s runtime helpers --
`Work`, `prepare`, `start_display`, `godot_prefix`, `script_errors`,
`characters`, `parse_pairs`, `parse_seeds` -- through the same
`importlib`-based `tools/` loader `tools/replay_check/run.py` itself
uses for `tools/coverage`. It does not import `tools/replay_check/run.py`'s
`argparse` setup or its `check`/`record` commands: this tool only ever
plays one live match through the UI, so it has no playback or regression
comparison to run.

## PASS and milestone-reached

A job passes (`UI_TEST_DONE`) when every milestone below is reached in
order, with no `SCRIPT ERROR` in the Godot log. "Reached" means the
driver's own post-condition for that step became true within its frame
budget (`UIPlay.gd`'s `_wait_until`): the real `Control` the step depends
on became visible, or the real game state the step depends on changed,
not merely that a click was sent. A milestone that is not reached within
its budget fails the job immediately (`UI_TEST_FAIL: <milestone> not
reached within N frames`); the driver never skips a missed milestone and
continues.

| Milestone | Reached when |
|---|---|
| `main_menu` | `MainMenu` is visible and `Global.mods_loaded` |
| `character_select` | `CharacterSelect` is visible with its button list populated |
| `p1_selected` | `CharacterSelect.selected_characters[1]` is set, after a real click on P1's character button |
| `p2_selected` | same, for player 2 |
| `match_started` | `Main.game` exists, after a real click on `GoButton` |
| `turn_N_actionable` | both `P1ActionButtons` and `P2ActionButtons` are visible and `active` |
| `match_ended` | `game.game_finished` |
| `pause_menu_open` | `PausePanel` is visible, after a real Escape key press |
| `replay_saved` | a new file appears under `user://replay`, after a real click on `SaveReplayButton` |
| `returned_to_menu` | `MainMenu` is visible again, after a real click on `QuitToMainMenuButton` |

## The turn loop

`UIPlay.gd` does not call `ActionButtons.on_toggled`/`on_pressed`
directly. Each turn, once both action panels are visible and `active`
(`ActionButtons.update_buttons` has already hidden the illegal buttons,
so visible+enabled is the real legality state), it reads `panel.buttons`
for the visible, enabled ones, picks one with a seeded RNG, and clicks its
real `%Button` child (or the button itself for `ContinueButton`) at its
`get_global_rect()` center. It then clicks each panel's real
`%SelectButton` to lock in.

This is a smaller move space than `tools/replay_check/ReplayCheck.gd`'s
bot: it does not toggle Reverse, Feint or character-extra buttons, and
does not drive the data controls (`XYPlot`, sliders). The simulation
coverage this tool adds comes from the UI it drives, not from a richer
move policy; `tools/replay_check` already covers move variety at a much
higher throughput (see its own README).

## Coordinate scaling for synthetic clicks

A synthetic mouse event's `position` must be given in the OS window's
pixel space, not the `Control` canvas space `Control.get_global_rect()`
reports. `project.godot` sets `window/stretch/mode="viewport"` with a
design resolution of 640x360 and a test window of 1280x720. Under
`--no-window` with `DISPLAY` set to Xvfb (sized 1280x720, matching
`window/size/test_width`/`test_height`), `OS.window_size` reports
`(1280, 720)` while `Viewport.size` stays at the design resolution
`(640, 360)` -- confirmed by reading both back in a probe scene. A probe
scene with one `Button` at a known position confirmed empirically that:

- Sending a click at the `Control`'s raw canvas-space position lands on
  whichever ancestor happens to cover that unscaled point (observed: it
  consistently missed a non-full-rect `Button` and either hit a full-rect
  parent `Control` or hit nothing, even though `Control.has_point` is
  mathematically satisfied for the *unscaled* point against the button's
  *canvas* rect -- the engine is comparing the scaled-up raw position
  against the same rect).
- Multiplying the canvas-space position by
  `OS.window_size / Viewport.size` (measured `(2, 2)` in this project)
  before sending it lands the click on the target `Control` every time,
  confirmed by a grid scan of synthetic clicks across the whole screen
  that mapped the actually-clickable region to exactly `2x` the button's
  own rect, offset to match.

`UIPlay.gd`'s `_screen_pos` applies this scale to every click. No such
scaling is needed for `InputEventKey` (used for the pause hotkey), since
key events carry no position.

## Verified: `Input.parse_input_event` vs `viewport.input` vs `unhandled_input`

`Input.parse_input_event(event)` (the global `Input` singleton) was
verified to fire a real `Button.pressed` signal, with the scaling above
applied. `Viewport.input(event)` was also tried in an earlier probe and
produced the same result (both route through the same
`Viewport::_gui_input_event` hit-test, confirmed by reading
`scene/main/viewport.cpp` from the engine source for Godot 3.5.1); the
shipped driver uses `Input.parse_input_event`, since it matches how a
real OS mouse event enters the engine. `_unhandled_input` was not used
for clicks: it is the *fallback* delivery path for an event no `Control`
claimed, so it is unsuitable for driving a `Button` click, and the pause
hotkey in `UILayer.gd` is in fact polled with
`Input.is_action_just_pressed("pause")` in `_process`, not handled in
`_unhandled_input`, so the key press only needs to reach the `Input`
singleton's action state, which `Input.parse_input_event` also does.

## Why an autoload, not the scene Godot boots

`UIPlay.gd`/`UIPlay.tscn` is registered as a project autoload in the work
copy's `project.godot` (`tools/ui_test/run.py`'s `register_autoload`,
inserted the same way `tools/coverage/instrument.py` adds `__COV__`), not
passed as the scene on Godot's command line. Godot boots
`project.godot`'s own `run/main_scene`, `Main.tscn`, unmodified -- the
real game boot path.

This matters once the match ends: the exit-to-menu button calls
`Global.reload()`, which is `SceneTree.reload_current_scene()`. That frees
and reloads only the *current scene* (`Main`), the same way the game's
own autoloads (`Global`, `Network`, `ModLoader`, ...) already survive it
during ordinary play. A driver living inside `Main.tscn`'s own tree, or
passed as the scene Godot loads directly (as
`tools/replay_check/ReplayCheck.tscn` is), would be freed by the same
reload and could not log the `returned_to_menu` milestone.

## Verification run

One match, `--pairs Cowboy:Ninja --seeds 1`, no `--coverage`: every
milestone above was reached, in order, with no `SCRIPT ERROR`. See the
task's handoff report for the timestamped milestone log.

With `--coverage`, this tool's own coverage (`ui/`, `main.gd`, overall)
over that one match should be compared against
`tools/replay_check`'s own single-run baseline, not its 50-match
aggregate from its README -- the aggregate runs many seeds and pairs, so
it is not an apples-to-apples comparison for one UI-driven match. See the
handoff report for the measured numbers.

## Limits

- One singleplayer match per job; no multiplayer path (`Network.gd`) and
  no replay-viewer path.
- The turn loop clicks one legal action per panel per turn; it does not
  toggle Reverse, Feint, character-extra buttons, or drive data controls.
- `instrument.py` excludes `tools/` from coverage instrumentation, so
  `UIPlay.gd` itself is never instrumented. That is correct: this tool
  measures how much of the *game's* UI code a real click reaches, not how
  much of its own driver code runs.
