# The core game: five official characters, default rules

Lab book entry. Session 2026-11-12 to 2026-11-13, on kelce.
Repos affected: `hustle` (this repo: `tools/`, `.scratch/`), `dancing_machine`
(the Discord replay corpus, read only).

This entry records the scope of a port of Your Only Move Is HUSTLE to Rust, with
proofs in Lean. The decision: model the core game only. The core game is the
five official characters, version `1.10.0-steam`, and the default match rules.
Sections 2 and 3 give the evidence. Section 4 gives the decisions. Section 5
lists what is out of scope and why.

## 1. The question

The goal is a model of the game that a computer can search and that Lean can
prove facts about. Three questions decide the shape of that model:

1. Is the game small enough to search, if the model is done properly?
2. Which language holds the model?
3. Which characters and rules does the model hold? The Steam Workshop has
   thousands of community characters, and replays contain them.

## 2. Evidence about the game

### 2.1 The game is deterministic from its inputs

- The replay check harness (`tools/replay_check/`) hashed the full game state at
  every tick. 49 of 49 replays gave the same hashes across engine builds, CPU and
  GPU rendering, and coverage instrumentation.
- A replay stores inputs only. `characters/BaseChar.gd` writes
  `frames[id][tick] = {"action", "data", "extra"}`. The rest of the file is the
  match data: seed, characters, stage and settings. The replay keys an input by
  the tick that applies it, which is the decision tick + 1.
- Game-logic arithmetic is fixed point. The GDScript calls a Rust GDNative
  library, `lib/tbfg.so`, as `FixedMath` (36 methods) and `FGObject` (40 methods:
  `apply_force`, `apply_fric`, `apply_grav` and others). Fixed-point values pass
  as strings: `fixed.mul` has 1,528 call sites. The library's strings show the
  crates `fixed 1.19.0`, `fixed-sqrt 0.2.5` and `gdnative-core 0.11.3`. The
  logic RNG is `BetterRng`, seeded from `hash(match_data.seed)`.
- The game logic is about 22,800 lines of GDScript in `characters/`, `obj/`,
  `mechanics/` and `framework/`, plus `game.gd`.

### 2.2 Exact inputs are finite, and outcomes fall into few classes

An aim control (`XYPlot`) sends integer percents from −100 to 100 on each axis.
That gives 31,417 points in the disc. The game logic does not check whether the
UI could send a value. The sweep driver is `.scratch/sweep/Sweep.gd`.

| Sweep | Aims the UI can send | Distinct exact states | Coarse outcomes |
|---|---|---|---|
| Wizard Jump, `Wizard_Robot_1` tick 13, to the next decision | 10,563 | 10,563 | 141 |
| Wizard MagicMissile, `Wizard_Ninja_2` tick 874, 90 ticks with the replay's later inputs (stopped at 20,031 aims) | 20,031 | 20,031 | 41 (7 HP outcomes) |

A coarse outcome is (HP, state names, decision tick, positions rounded to 8 px,
object count). Exact states never merge: the velocity keeps 1/65536-pixel
precision, and the state stores a copy of the input. So a search can group aims
by outcome class, and exact-state transpositions do not exist.

A scan of 296 aimed projectile inputs compared each replay aim with its
opposite. All 83 GunThrow aims had no effect after 90 ticks.

### 2.3 Combos remove most of the defender's choices

A census (`.scratch/census/Census.gd`) recorded every decision point in 6 human
replays at `1.10.0-steam`: 1,017 turns. 2,034 of the 2,036 recorded inputs were
in the computed legal-action list. Three Haiku agents audited the six files.
Their counts matched the census script exactly (`.scratch/census/audit/`).

| | Turns | Legal actions per player (median) |
|---|---|---|
| Neutral | 710 | 18 to 23 |
| Combo, attacker | 307 | 22 to 29 |
| Combo, defender | 307 | Hold only: 146 turns. Hold or Burst: 148. Hold, Getup or TechRoll (knockdown): 13 |

- The defender's choice in a combo is 1 or 2 actions plus a DI aim. This agrees
  with `CharState.gd:238–243`: a `Hurt` state interrupts only into `BusyHurt`,
  and only when it is burstable.
- The attacker's legal list is not shorter in a combo. Legality does not check
  whether a move connects before the hitstun ends.
- No attacker repeated an action sequence across about 61 combos.
- The share of combo turns per replay ranged from 5% to 79%.

### 2.4 Rust in a provable subset costs no speed

A tick kernel (gravity, friction, position update, pairwise hitbox test) ran in
three styles in `.scratch/leanbench/bench2.rs`. All three gave the same
checksums.

| Objects | Safe Rust | Provable style (checked arithmetic, `Result`, index loops) | `unsafe`, unchecked |
|---|---|---|---|
| 4 | 13.1 ns/tick | 12.4 | 12.7 |
| 32 | 1,046–1,426 | 248–251 | 1,022–1,496 |

The game logic in GDScript takes about 2 ms per tick. The kernel does less work
than a real tick, so the ratio is not a measured speed-up. The Lean version of
the kernel did not build, and it has no measurement.

## 3. Evidence about community characters

### 3.1 The game puts no constraint on a character

- The Custom Character Loader is built into this version
  (`ui/CSS/CharacterSelect.gd:21`). A mod calls `addCustomChar(name, path)`. The
  loader checks only that the files exist and that the scene instances
  (`loadListChar`, lines 625–675). Nothing checks that the root is a `Fighter`.
- A character's internal id is `F-<hash of the mod folder>__<name>` (line 566).
  A replay does not contain the character's code.
- `ModLoader.installScriptExtension` (`modloader/ModLoader.gd:229`) lets a mod
  take over any script path except `Network.gd`. So a mod can replace the rules.
- The Workshop has 23,872 items. 90 of the top 100 by subscribers are
  characters. 131 of the 1,824 Discord replays use a community character, with
  162 distinct character ids.

### 3.2 Audit of nine popular characters

Method: a deterministic scan (`.scratch/mods/scan.py`), calibrated on the
official characters, then a reading of every flagged line. The official
characters also write to the opponent in normal ways: throws set the opponent's
position and state, and Ninja sets `opponent.hitlag_ticks`.

| Character | Scene | Verdict | Main reason |
|---|---|---|---|
| Aoi Todo | inherits `BaseChar.tscn` | fits | Only `Start` replaced. No network, RNG or float physics. |
| Satoru Gojo | inherits | fits | Universal-state overrides change only the sprite frame. |
| Cryomancer | inherits | fits | Only `Start` replaced. Carries an unused decompiled copy of the game's source (`decompsteam/`). |
| Goon | inherits | fits | One float in the death check. The AI only chooses inputs. |
| Toji | inherits | fits after one change | `Dash.gd` sets velocity from float math passed through Godot's float-to-string format. |
| NIFLHEIM | inherits | fits after one change | The Steam username decides whether the intro spawns game objects. Burn subtracts HP directly. |
| Miko | own copy of the base scene | does not fit | InklessBrush template: `faker()`, a list of more than 60 usernames, 46 direct writes to the opponent, float forces. |
| Vixen | own copy | does not fit | The same template. 83 direct writes to the opponent. |
| Moonwalker | own copy | does not fit | 58 of 86 states use mod scripts, including all Parry, Hurt and Knockdown states. `faker()`. |

`faker()` runs in online or spectated matches when a player's Steam name
contains "inkless" or "leftie" and the Steam ID is not the creator's. The
creator's ID is `76561199474761694`. The function gives the opponent meter,
feints, perfect parry, HP and burst every tick. It punishes impersonation of the
creator. The replay does not record identity, so an offline replay of such a
match desyncs.

### 3.3 Sandbox settings

Turbo Mode ("Freely cancel moves into other moves"), Extremely Turbo Mode and
Infinite Resources are official match settings (`ui/GameSettingsPanelContainer`).
The game blocks the Touch of Death achievement when any of them is on
(`BaseChar.gd:1397`). The InklessBrush characters add more powers in these
modes (`lamturbo()`), with float forces from new input fields.

## 4. Decisions

1. **Characters:** the model holds the five official characters only. They are
   the entries of `Global.name_paths` (`Global.gd:126–133`): Ninja
   (`stickman`), Cowboy (`swordandgun`), Wizard (`wizard`), Robot (`robo`) and
   Mutant (`mutant`). The commented-out `Alien` entry is not part of the game.
2. **Version:** `1.10.0-steam`, the version of this checkout
   (`Global.gd:5`).
3. **Rules:** the default match settings. Turbo Mode, Extremely Turbo Mode and
   Infinite Resources are off. The model rejects a replay whose match data turns
   any of them on. Section 6 lists the other settings as an open item.
4. **Language:** Rust. The port uses `fixed 1.19.0` and `fixed-sqrt 0.2.5`, so
   that its fixed-point rounding matches `tbfg.so` by construction.
5. **Rust style:** write the simulation in the subset that Aeneas (through
   Charon) or hax can translate to Lean, from the first line:
   - plain `struct`s and `enum`s, with one enum variant per character state,
   - objects in a `Vec`, referenced by index, with no `Rc`, `RefCell` or trait
     objects,
   - a pure step function, `step(state, inputs) -> state`,
   - no `unsafe`,
   - no hash maps in the logic.
   Check the current limits of Aeneas and hax before the port starts.
6. **Acceptance check:** the port writes the same `StateSnapshot` JSON as
   `tools/replay_check`. For every replay in the test set, its per-tick hashes
   must equal the hashes of the real game.
7. **Test set:** Discord replays at `1.10.0-steam` with official characters only:
   93 replays, 12 of them finished. Add the generated replays from
   `tools/replay_check`.
8. **Search abstraction:** group continuous inputs (aims, DI, sliders) by
   outcome class, not by exact state (section 2.2). Treat a combo turn as a
   reduced matrix: attacker actions × (defender action × DI class) (section 2.3).
9. **Functional core, imperative shell.** Formalize the core first. The core is
   one pure function, `step(state, inputs) -> state`: `game.gd`'s tick, the
   objects, states, hitboxes, fixed-point math and the logic RNG. The shell is
   everything that decides *which inputs reach the core*. Address the shell
   second, but do not leave it out. Parts of the shell define the game:
   - **Input legality lives in the UI, not in the core.** The community
     already found this, in public. "Fresh Milk" was a Cowboy exploit,
     discovered in spring 2023 (patch 1.4 era). The source is the roycebracket
     video "'Fresh Milk' – The Coolest Bug In Fighting Game History"
     (2025-04-03, https://www.youtube.com/watch?v=-TQn8ZsN8Dc). Its transcript
     says: "Yomi Hustle doesn't actually check that you're allowed to do a
     certain move before letting you lock it in. It simply assumes that if
     the move was illegal, it wouldn't have shown up on your UI." The Foresight
     "Shift" toggle switched the UI to the grounded or aerial move set,
     depending on the mirage. A player toggled Shift and locked in a move
     before the UI deselected it. Cowboy then did grounded moves in the air and
     aerial moves on the ground. The developer's fixes did not hold.
     Tournaments banned the exploit. The developer then added **Drift**, a
     legal version that costs meter and destroys the Foresight. `MilkButton`
     in `CowboyExtra.gd` is the Drift toggle.
   - **This checkout still does not check legality in the core.**
     `BaseChar.on_action_selected` (`characters/BaseChar.gd:2935`) calls only
     `state.is_usable()` (line 2954): resources, air options, combo-only and
     neutral-only flags. It does not check the cancel categories
     (`interrupt_into`, `hit_cancel_into`) or the grounded or aerial air type
     (`is_usable_with_grounded_check`). Those checks exist only in
     `ActionButtons.update_buttons()`. Replay playback applies recorded inputs
     with no check at all. Drift has the same split. `SwordGuy.drift()`
     (`SwordGuy.gd:245`) checks only meter and the afterimage. The UI adds
     `MilkButton.visible = obj.is_grounded() != fighter.is_grounded()`
     (`CowboyExtra.gd:84`).
   - **Extremely Turbo Mode is the same UI invariant switched off.** It shows
     and enables every action button (`ActionButtons.gd:631–636`). The core
     needs no change for it, because the core never enforced the rule.
   - The same split holds for the feint and reverse buttons, each character's
     extra-options panel, and the aim controls (`XYPlot` `min_length`, the Jump
     arc).
   - Therefore the core alone is a different and larger game: the game with
     Fresh Milk everywhere. The rules of the real game are the core plus the
     UI invariant.
   - **The turn protocol is in the shell too.** `game.gd` decides who may act,
     sets `busy_interrupt`, and calls `on_interrupt()`. Live play and replay
     play take different paths here (open item 2).
   So the model has three layers: the core step function, a pure legality
   function `legal_inputs(state, player)` ported out of the UI code, and the
   turn protocol. Generated replays and searches must draw inputs only from
   `legal_inputs`, or they test states that no player can reach.
   The difficulty is the code's design, not the game. The rules are
   formalizable, but the engine couples to the UI loosely. The UI computes
   legality again and again, and lock-in reads whatever it holds at that
   moment. The UI test harness found the same window: `ActionButtons.gd`
   checks the selection again, and falls back to Continue in `_process()`, up
   to lock-in, so a click can land mid-change. That window is how Fresh Milk
   works.

   **Version 1 of the Rust core keeps the same gap on purpose.** It ports the
   engine as it is, including every UI issue:
   - `step` applies any input, exactly as replay playback does now. It does
     not check legality.
   - The `is_usable()` check that changes an action to `Forfeit` stays in the
     shell. `on_action_selected` runs it at selection time in live play, not
     during playback.
   - `legal_inputs(state, player)` exists in version 1 as a separate pure
     function, ported from `update_buttons()` and the extra-options panels.
     Nothing enforces it. The replay generator and searches use it to choose
     inputs.

   Reason: the acceptance check (decision 6) needs tick-for-tick equality with
   the real game, and replays can contain inputs that the UI should not allow.
   A core that refused them would fail the check for a reason that is not a
   port defect. A bug-compatible core also lets the model state Fresh Milk and
   the Extremely Turbo Mode behaviour exactly, as properties of the real game.

   Version 2 enforces legality: `step` refuses an input outside
   `legal_inputs` with an explicit error. Version 1 and version 2 must agree
   on every legal input sequence. A Lean theorem can state this, and a second
   theorem can state that every state version 2 reaches comes from legal
   inputs only.

## 5. Out of scope

| Item | Why |
|---|---|
| Community characters | The game puts no constraint on them (3.1). Each one needs its own port. Some depend on Steam identity (3.2). |
| Miko, Vixen, Moonwalker | Each is a second set of rules: 4,000 to 7,000 lines of GDScript, replaced Parry and Hurt states, and identity-dependent code. |
| Aoi Todo, Gojo, Cryomancer, Goon, Toji, NIFLHEIM | They could fit later (3.2). The model would need three more building blocks: set the opponent's hitlag, the intro lock, and a direct HP change. |
| Turbo Mode, Extremely Turbo Mode, Infinite Resources | Sandbox settings (3.3). Turbo Mode is one extra cancel rule and can come later. |
| Community stages, and mods that extend the game's scripts | They change the rules or the arena. |
| Presentation: camera, particles, sound, menus | They do not change the game state. The replay check ignores them already (`tools/replay_check/ignore.txt`). The input-legality rules in the UI are not presentation. Decision 9 keeps them in the model. |

## 6. Open items

1. **Match settings.** The settings panel has about 46 settings:
   `stage_width`, `di_enabled`, `prorated_di`, `one_hit_ko`, `game_length`,
   `burst_enabled`, `clashing_enabled`, `global_damage_modifier`,
   `global_hitstun_modifier`, `global_hitstop_modifier`,
   `global_gravity_modifier`, `has_ceiling`, `sadness_enabled`,
   `starting_meter`, `min_di_scaling`, `max_di_scaling`, `di_combo_limit`,
   `brace_enabled`, `p1_starting_health`, `p2_starting_health` and others. List
   their default values. Then decide which ones the model takes as parameters
   and which ones it fixes.
2. **Live play and replay play take different paths.** `interrupt_exceptions`
   and `state_interruptable` differ between them (the NewParry `on_interrupt`
   path). The census used the live-path busy rule from `game.gd` (about lines
   1452–1475). Decide which path the model follows, and record why.
3. **The string interface.** `FixedMath` takes and returns strings. The port
   must match the parse and format rules of `tbfg.so`. Test this layer first:
   call `tbfg.so` through a GDScript harness, and compare with the port.
4. **Turn the legality rule into code.** The census copies
   `ActionButtons.update_buttons()`. It misses 2 of 2,036 recorded inputs:
   Robot `Blast` from `Flamethrower`, and Mutant `DownwardCleave` from
   `ForesightNeutral`. Find the missing rule before the model uses it.
5. **The effective branching factor.** For each combo turn, simulate each legal
   attacker move once. Count the moves that keep the combo going.
6. **Known game defects** that the model must reproduce or document:
   `characters/stickman/projectiles/Caltrops.gd:18` (null `host.id`) and
   `Robot.GroundSlam` (dead cancel category `Magnetize`).
   `ui/HUD/HudLayer.gd:248` (`super_speed_scale` divides by zero) is UI only.
7. **Replay coverage.** Generate replays that cover the core's rules
   efficiently: every state entered, every legal transition, every hitbox
   outcome, every projectile, every mechanic, every branch of the logic code,
   for each character pair. Use a coverage-guided search over
   `legal_inputs`, shared prefixes from copied states, one input per outcome
   class, and a greedy set cover at the end. First measure the baseline
   coverage of the logic code from the 50 generated and 93 human replays.
8. **Network tests.** Online play is part of the shell and runs the live
   path. `tools/nettest/` plays one match between two game containers over
   the game's direct ENet multiplayer, through `udp-netsim`
   (`tools/replay_check/netsim/`), a Rust UDP relay with seeded delay,
   jitter, loss and optional reordering. Profiles: lan, typical
   (40 ms ± 10 ms, 0.5% loss), bad (120 ms ± 40 ms, 2% loss, reordering).
   The relay's own check through an echo server gave a round trip of 81 to
   109 ms for 40 ms ± 10 ms each way, and 32 of 300 lost for 5% loss each
   way (9.75% expected).

   Results, 2026-11-13. `Wizard_Robot_1` (generated) under lan, typical and
   bad, and the human replay `1524493457157128242_2026-7-8-22-10-31` (Ninja
   vs Wizard, finished) under typical and bad:
   - Every match finished. Host and client per-tick hashes were equal at
     every tick (1,684 and 1,692 ticks). The relay dropped up to 30 datagrams
     per direction in the bad profile. ENet retransmits, so loss and delay
     changed only the turn wait (bad profile: median about 1 s per turn).
   - `Wizard_Robot_1` online equals its offline playback and the golden
     trace at every tick, for every object.
   - The human replay online differs from its offline playback in **P2 at
     87 ticks**, in 6 windows (314–329, 346–361, 378–393, 625–626,
     1064–1081, 1115–1133). The only differing field is P2's ParryHigh
     `interrupt_exceptions`: `["AerialMovement"]` online, empty offline.
     `NewParry.on_interrupt()` (`characters/states/NewParry.gd:172`) adds it,
     and only the live path calls `on_interrupt()` (`game.gd:1456, 1473`).
     Positions and HP stay equal, and the final state is equal. This is open
     item 2, observed. The field changes which cancels the UI allows out of
     the parry, so the live and replay paths differ in legality, not here in
     the simulation.
   - **A turn-protocol defect.** `game.gd`'s turn-start block sets the two
     turn flags on two consecutive frames, and the second frame sets
     `p1.state_interruptable = true` again (`game.gd:1474`). An action
     submitted between the two frames is lost, and the match deadlocks. The
     first driver submitted at once and stalled in all 6 runs. Humans never
     click within one frame. An instant bot can.

## 7. Artifacts

| Path | What |
|---|---|
| `tools/replay_check/` | Determinism harness, Nix image, per-tick state hashes |
| `tools/coverage/` | GDScript line coverage |
| `tools/ui_test/` | Real-input UI test harness |
| `tools/replay_check/netsim/` | `udp-netsim`, the latency and loss relay (Rust, in the Nix image) |
| `tools/nettest/` | Two-process network test (in progress) |
| `.scratch/sweep/` | Aim sweep driver, results and `analyze.py` |
| `.scratch/census/` | Decision-point census, audit files, Haiku reports, `reference.txt` |
| `.scratch/mods/` | Workshop rankings (`details.json`), `scan.py`, `scan.json`, unpacked mods |
| `.scratch/leanbench/` | Rust and Lean tick kernels |
| `.scratch/docs/` | Text of the strategy guides |
| `SESSION.md` | Notes from the earlier bitwise3 session |

`.scratch/` is not in version control. The `tools/` directory and this lab
book are not committed yet.
