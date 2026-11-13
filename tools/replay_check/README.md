# Headless replay check

This tool runs the game's simulation on Linux inside a podman container. It
needs no physical display, no Steam client and no Windows machine. It does
three checks:

| Check | Compares | Finds |
|---|---|---|
| Determinism | Two playbacks of one replay | Simulation state that depends on something other than the inputs |
| Replay fidelity | The live match against its own playback | Differences between the live code path and the replay code path |
| Regression | A playback against a stored golden trace | Behaviour changes after a code change |

## Usage

```sh
tools/replay_check/container.sh generate --out replays/ --pairs all --seeds 1-2
tools/replay_check/container.sh check --replays replays/
tools/replay_check/container.sh record --replays replays/ --golden golden/
tools/replay_check/container.sh check --replays replays/ --golden golden/
```

`container.sh` builds the image with Nix (see "The container" below;
podman reuses layers when nothing changed) and runs `run.py` in it.
`run.py` refuses to run outside the container.

- `--pairs` takes `all`, or a list such as `Cowboy:Ninja,Wizard:Robot`. The
  names come from `Global.name_paths`.
- `--work DIR` sets the work directory. The default is
  `~/.cache/hustle-replay-check`.
- `--jobs N` sets the number of parallel Godot processes.
- The exit code is 0 when every check passes, 1 when a check fails, and 2
  when a step cannot run.
- Measured on a 32-core host with `--jobs 14` (stock Godot, before the move
  to the container): `generate` for 50 matches took 7 to 9 minutes, and
  `check` for 50 replays took 22 minutes. Each tick takes a full snapshot,
  so one match runs at about 30 to 60 simulation ticks per second.

## The container

`flake.nix` (with `nix/godotsteam.nix`, `nix/audio-sleep-shim.nix` and
`nix/image.nix`) builds the image with Nix. `build.sh` calls the shared
`tools/nix_image_build.sh`, which builds it inside a pinned
`docker.io/nixos/nix` container and loads it into podman as
`localhost/hustle-replay-check`; `container.sh` calls `build.sh` before
every run. Nix never runs on the host: the Nix store is the named podman
volume `hustle-nix-store`, so a rebuild after a small change is
incremental. `flake.lock` pins nixpkgs; every package version in the
image traces back to that one commit. The flake source is a copy of this
directory's git-tracked files, so `git add` a new file before a build can
see it. `tools/fixedmath_diff` and `tools/lean_check` build their images
through the same shared script.

The image contains:

- **GodotSteam g351-s155-gs3184**, fetched by `nix/godotsteam.nix` from
  Codeberg with a pinned SHA-256. This is Godot 3.5.1 with the Steamworks
  SDK 1.55 module. Its `libsteam_api.so` has the same SHA-256 as the
  `libsteam_api.so` in this repository, so it is the engine family the
  game ships on. The tool uses the editor build (`linux-351-editor.64`),
  because only an editor build can run a project directory and import
  assets. The editor binary and `libsteam_api.so` are prebuilt glibc
  ELF files; `autoPatchelfHook` in `nix/godotsteam.nix` sets their
  interpreter and RPATH for Nix's dynamic loader.
- **Xvfb, with GLX.** The GodotSteam build is an X11 build, and without
  `--gpu` it draws straight through GLX to Mesa's software renderer
  (llvmpipe). nixpkgs' own `xvfb` package has no Mesa and no GLX at all;
  `nix/image.nix` takes the `Xvfb` binary from the full build,
  `xorg.xorgserver`, instead (and does the same for `xvfb-run`, which
  `tools/botplay` uses). The X server's software GLX provider looks for
  `swrast_dri.so` at a fixed path, `/run/opengl-driver/lib/dri` (nixpkgs'
  convention for a directory a non-NixOS image must populate itself), so
  the image links that path to its own Mesa.
- **Python 3 and rsync** for `run.py`.
- **xauth and ffmpeg** for `tools/botplay`, which uses the same image.
- **VirtualGL** (the nixpkgs package) for GPU rendering, and
  **libaudiosleep.so** (built by `nix/audio-sleep-shim.nix` from
  `audio_sleep_shim.c`) for audio faster than real time. See the two
  sections below.
- **The game's own native library, `lib/tbfg.so`.** This is also a
  prebuilt glibc ELF, but run.py never patches it: it loads at run time,
  through GDNative, from the project copy `run.py` makes, not from the
  image. Its NEEDED libraries (`libgcc_s.so.1`, `libc.so.6`) resolve
  through `LD_LIBRARY_PATH`, which the image sets to include glibc's and
  libgcc's Nix store paths.

`container.sh` mounts the repository read-only, and the work directory and
each `--out`, `--replays`, `--golden` and `--coverage` directory read-write.
Each mount uses the same absolute path inside the container as on the host.

Each command first syncs the work directory:

1. It copies the repository into `<work>/project` with rsync. The
   repository stays unchanged.
2. It imports the assets with `--export-pack` and discards the pack. After
   the first run, the import is incremental.

No patch is needed: GodotSteam has the built-in `Steam` singleton, and the
X11 platform matches the `X11.64` entry for `tbfg.so` in `lib/lib.tres`.
`steamInit` fails without a Steam client, and `SteamY.gd` handles the
failure, so the game stays offline.


## GPU rendering

`container.sh --gpu <CDI device>` passes one GPU into the container through
the NVIDIA container toolkit (`nvidia-ctk cdi list` shows the names). Then
`run.py` starts every game run with `vglrun -d egl`. Godot 3.5 draws
through GLX and needs an X display. VirtualGL intercepts the GLX calls,
renders on the GPU with its EGL back end, and copies each frame into the
Xvfb window. The NVIDIA user-space driver comes from the host through CDI,
so it matches the host kernel module.

Checked with a probe scene and the RTX 5070
(`nvidia.com/gpu=GPU-402ebdd5-4254-0958-b4da-afe76c4968af` on this host):
`VisualServer.get_video_adapter_name()` reported `NVIDIA GeForce RTX
5070/PCIe/SSE2`. Without `--gpu`, Mesa's llvmpipe renders on the CPU.

The simulation does not depend on the renderer, so `check` results are the
same with or without `--gpu`.

`--gpu` also sets `VGL_READBACK=none` (no tool looks at the Xvfb window,
and Godot still reads its own frames from the GPU) and `__GL_YIELD=USLEEP`
(the NVIDIA driver sleeps instead of spinning while it waits for the GPU).

Measured with 20 s replays through `Main.tscn` with lockstep audio, all at
`--fixed-fps 60`, on this 32-core host:

| Runs at once | Renderer | Engine start | Replay, per run | Throughput |
|---|---|---|---|---|
| 1 | 5070 | 1.2 s | 4.4 s | 2.3x |
| 8 | 5070 | 18.1 to 19.1 s | 6.6 to 7.4 s | 3.8x |
| 8 | llvmpipe | 2.4 s | 21.3 to 21.8 s | 5.8x |
| 16 | 5070, without `__GL_YIELD=USLEEP` | | 0.31x to 0.68x speed | 2.3x |
| 16 | 5070 | | 1.53x to 1.90x speed | 3.7x |

Throughput is the summed game time over the wall time of the batch. The
GPU renders each frame about three times faster than llvmpipe, and its
mean use stayed at 14 to 26%. With several runs at once, the NVIDIA start
of each Godot process becomes the limit: it took 18 s, mostly kernel time,
against 1.2 s alone. Its shader cache is not the cause (no change with
`__GL_SHADER_DISK_CACHE=0` or a warm cache). So the GPU pays off for long
runs, and short runs in parallel are faster on llvmpipe. On this host,
vLLM also keeps about 11.3 of the 5070's 12.2 GB in use.

## Audio faster than real time

Every game run uses Godot's Dummy audio driver. Its thread
(`servers/audio/audio_driver_dummy.cpp`, `AudioDriverDummy::thread_func`)
mixes one buffer and then sleeps the full buffer time. So it mixes slower
than the nominal rate: 43,500 to 43,800 samples per second when the host is
idle, against 44,100. It also never mixes faster than real time.

A tool that needs audio in step with the video paces the mixer itself:

1. Hold `AudioServer.lock()` while the game ticks and draws frame N.
2. Unlock until an `AudioEffectCapture` holds `N * mix_rate / 60` frames in
   total. Read it with `get_buffer(get_frames_available())`, not
   `clear_buffer()`, which drops frames that arrive between the two calls.
3. Lock again.

For such a run, `audio_shim_env(project)` in `run.py` returns the
environment that loads `libaudiosleep.so` (`LD_PRELOAD`). The shim cuts the
driver's sleep, and only that sleep, from 11,609 us to 1 ms, so the mixer
can run faster than real time. It must stay above zero: with no sleep the
mixer takes its lock again at once, and in a test the audio ran 34 s ahead.

Measured with a probe scene, 20 s of game time at `--fixed-fps 60`:

| Setup | Host load | Speed | Samples (882,000 expected) | Largest lead |
|---|---|---|---|---|
| No shim | idle | 0.97x | 882,688 | 2,743 |
| Shim | idle | 7.36x | 882,688 | 1,023 |
| Shim | load average 73 to 85 on 32 cores | 0.93x to 1.36x | 882,688 to 884,736 | 3,543 to 50,757 |

The total count is exact in every run. The lead (audio ahead of the
video) grows with host load: when the main thread waits for the CPU, the
mixer keeps mixing. Under heavy load it reached 1.15 s inside one run.

Do not use the shim for a run without this pacing: the mixer then runs
about 10 times faster than real time.

## How a job runs

`run.py` writes a JSON job file and starts
`godot --no-window --audio-driver Dummy --path <copy> --fixed-fps 60 res://tools/replay_check/ReplayCheck.tscn --rc-job=<file>`.
`--fixed-fps` removes the real-time clock, so a job runs as fast as the CPU
allows. `XDG_DATA_HOME` points at a separate directory for each job, so
`user://` never touches the real game data. run.py empties that directory
before each run, so every job is a first launch. This matters:
`ModLoader.gd` turns mods off on the second launch in a user directory
(the version-transition gate). With mods off, `BaseObj` gets no `Hooks`
node, and the fighter state differs from a first launch.

`ReplayCheck.gd` has two modes:

- **generate** starts a live singleplayer match with the default settings
  from `GameSettingsPanelContainer.tscn`. It creates two real
  `ActionButtons` panels, the same pair as in `Main.tscn`. On each turn, it
  opens both panels as `UILayer.on_player_actionable` does. Then it clicks a
  random visible button and locks in with `_on_submit_pressed`. The menu
  shows only legal buttons, so the menu decides legality. A seeded RNG also
  toggles the reverse, feint and character-extra buttons. At the end, it
  saves the replay with `ReplayManager.save_replay`. run.py also writes
  `<name>.match.json` with the characters and the seed.
- **playback** loads a replay with `ReplayManager.load_replay` and sets
  `ReplayManager.playback`. This is the path that the in-game replay viewer
  uses.

For the fidelity check, `check` plays the live match again from
`<name>.match.json` with the current code. The new replay must equal the
stored replay byte for byte. Then `check` compares the live trace with the
playback trace. A replay without a `.match.json` file, for example a replay
from a real player, gets the determinism and regression checks only.

Both modes put `TraceHooks.gd` on the `Hooks` node of the `Game`.
`post_tick` runs at the end of each `game.tick()`. At that point,
`StateSnapshot.gd` records the script variables of the `Game` and of each
object in `objs_map`. It also records each object's current state and its
variables, and the native `FGObject` data from `tbfg.so`. The trace stores
SHA-256 hashes only.

A comparison does not stop at the first difference. The simulation can
converge again, and a later, independent difference must stay visible. The
report lists each *divergence episode*: a run of consecutive ticks that
differ, with the objects that differ. It also states whether the final
state is equal. For the first 20 episodes, run.py runs both sides again with
a full snapshot at the first tick of each episode. Then it prints the
variables that differ.

`ignore.txt` lists the variable names that the snapshot leaves out. Each entry
has a reason. Add a name only when the variable does not feed the
simulation.

## Limits

- `generate` runs no prediction ghost game. In live play, `main.gd` runs one
  each turn (`_start_ghost`). Playback needs no ghost: `_start_ghost`
  returns when `ReplayManager.playback` is set, so the game also runs replays
  without one.
- Data inputs (`XYPlot`, sliders and other action data controls) keep their
  default values. Only the action, reverse, feint and character-extra
  choices are random.
- Only singleplayer matches run. The network path (`Network.gd`) is not
  covered; see "Network tests" below.
- The check runs the GodotSteam editor build from a project directory. The
  shipped game runs an exported pack on a GodotSteam export template. The
  exact gs3.18 release of the shipped build is not known; `readme.md` says
  only "godotsteam 3.5.1".

## Network tests

`tools/nettest/` runs a real match over `Network.gd`'s direct
multiplayer, between two Godot processes, through a relay that adds
latency, jitter and packet loss. It reuses this tool's trace format
(`TraceHooks.gd`, `StateSnapshot.gd`, `ignore.txt`) and its comparison
code (`compare_traces`, `snapshot_diff`), so a network-match trace and a
singleplayer-match trace compare the same way.

The relay is `udp-netsim`, built from `tools/replay_check/netsim` and
included in this tool's image (`localhost/hustle-replay-check`, at
`/bin/udp-netsim`). It is a plain UDP forwarder: it adds delay, jitter
and loss to the direct-multiplayer traffic between the two game
processes, so a network test measures the game's actual behaviour under
bad network conditions, not an idealised local connection.

See `tools/nettest/README.md` for usage.
