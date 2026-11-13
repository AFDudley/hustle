#!/usr/bin/env python3
"""Network test for Your Only Move Is HUSTLE: two real game processes play
one match over Network.gd's direct multiplayer, through a latency/loss
relay (tools/replay_check/netsim, udp-netsim).

This script runs on the host and orchestrates podman; it never runs Godot,
udp-netsim, or GDScript itself (see tools/nettest/README.md, "How a run
works"). It starts three containers on one fresh podman network, all from
the tools/replay_check image (localhost/hustle-replay-check):

  relay   udp-netsim, with the chosen profile's delay, jitter and loss
  host    Godot running tools/nettest/NetPlay.gd as the host side
  client  Godot running tools/nettest/NetPlay.gd as the client side

Each side plays one source replay's recorded inputs through the real
Network.gd protocol (see NetPlay.gd for the exact calls). It then runs a
fourth, short-lived container to play the host's saved replay back offline
through tools/replay_check/ReplayCheck.gd, for the fidelity check.

Usage:
  tools/nettest/run.py --replay PATH --profile {lan,typical,bad} [--seed N]

Checks reported per run (see README.md):
  a. host vs. client per-tick hashes, for the whole live match
  b. the live match vs. an offline playback of its own saved replay
  c. turns, finish state, Network.gd desync messages, per-turn latency,
     relay counters
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib.util
import json
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

TOOL_DIR = Path(__file__).resolve().parent
REPO = TOOL_DIR.parent.parent
RC_DIR = REPO / "tools" / "replay_check"
IMAGE = "localhost/hustle-replay-check"

HOST_PORT = 7000
RELAY_PORT = 7001
RELAY_READY_TIMEOUT_S = 20
PEER_READY_TIMEOUT_S = 30


class CheckError(Exception):
    """A step failed. The message says which step and why."""


@dataclasses.dataclass(frozen=True)
class Profile:
    delay_ms: float
    jitter_ms: float
    loss: float
    reorder: bool = False


PROFILES: dict[str, Profile] = {
    "lan": Profile(delay_ms=0, jitter_ms=0, loss=0.0),
    "typical": Profile(delay_ms=40, jitter_ms=10, loss=0.005),
    "bad": Profile(delay_ms=120, jitter_ms=40, loss=0.02, reorder=True),
}


def load_replay_check_run() -> Any:
    """Import tools/replay_check/run.py by path, for its pure comparison
    helpers only (read_trace, compare_traces, snapshot_diff, read_dumps,
    ignore_list). Those need no container: they only parse files already on
    disk. Container-only helpers (start_display, Work.godot, import_assets)
    are not called from here; run_peer.py calls them inside the container.
    """
    path = RC_DIR / "run.py"
    spec = importlib.util.spec_from_file_location("replay_check_run", path)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses looks up cls.__module__ in sys.modules while decorating;
    # a module not yet registered there makes @dataclass crash.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def sanitize(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", name)


def podman(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["podman", *args],
        check=check,
        capture_output=capture,
        text=True,
    )


def container_logs(name: str) -> str:
    result = podman("logs", name, check=False, capture=True)
    return (result.stdout or "") + (result.stderr or "")


def container_ip(name: str, network: str) -> str:
    """The container's address on one network.

    podman assigns this at container creation, so it is available the
    instant podman run returns. aardvark-dns name resolution lags behind
    that by a short, variable amount (observed on this host), and
    udp-netsim resolves --upstream once at startup with no retry, so the
    relay uses this address instead of the host container's name.
    """
    # Dot-field access (.Networks.NAME) only works for a Go-identifier
    # key; network names here contain dashes, so index is required.
    result = podman(
        "inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}', name, capture=True
    )
    ip = result.stdout.strip()
    if not ip:
        raise CheckError(f"{name} has no address on {network}")
    return ip


def wait_for_log(name: str, pattern: str, timeout_s: float) -> str:
    """Poll a container's combined logs for a regex. Fails fast if the
    container exits first, or if the deadline passes; never a blind sleep.
    """
    rx = re.compile(pattern, re.M)
    deadline = time.monotonic() + timeout_s
    while True:
        text = container_logs(name)
        match = rx.search(text)
        if match:
            return match.group(0)
        state = podman("inspect", "-f", "{{.State.Running}}", name, check=False, capture=True)
        if state.returncode == 0 and state.stdout.strip() == "false":
            raise CheckError(f"{name} exited before logging {pattern!r}; log:\n{text}")
        if time.monotonic() > deadline:
            raise CheckError(f"timed out after {timeout_s}s waiting for {pattern!r} in {name}; log:\n{text}")
        time.sleep(0.2)


def wait_for_file_log(container_name: str, log_path: Path, pattern: str, timeout_s: float) -> str:
    """Poll a peer's Godot log file for a regex.

    run_peer.py redirects the Godot subprocess's own stdout to log_path
    (tools/replay_check/run.py's run_job does the same), so a line Godot
    prints mid-run never reaches `podman logs` (that only captures
    run_peer.py's own stdout, which prints just one summary line at the
    very end). log_path is a bind mount, so it is this same file on the
    host; poll it directly instead.
    """
    rx = re.compile(pattern, re.M)
    deadline = time.monotonic() + timeout_s
    while True:
        text = log_path.read_text(errors="replace") if log_path.exists() else ""
        match = rx.search(text)
        if match:
            return match.group(0)
        state = podman("inspect", "-f", "{{.State.Running}}", container_name, check=False, capture=True)
        if state.returncode == 0 and state.stdout.strip() == "false":
            raise CheckError(f"{container_name} exited before logging {pattern!r}; log:\n{text}")
        if time.monotonic() > deadline:
            raise CheckError(f"timed out after {timeout_s}s waiting for {pattern!r} in {log_path}; log:\n{text}")
        time.sleep(0.2)


def wait_for_exit(name: str, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while True:
        state = podman("inspect", "-f", "{{.State.Running}}", name, check=False, capture=True)
        if state.returncode != 0:
            raise CheckError(f"{name} is gone before reporting an exit code")
        if state.stdout.strip() == "false":
            code = podman("inspect", "-f", "{{.State.ExitCode}}", name, capture=True)
            return int(code.stdout.strip())
        if time.monotonic() > deadline:
            raise CheckError(f"timed out after {timeout_s}s waiting for {name} to exit")
        time.sleep(0.3)


def ensure_image() -> None:
    subprocess.run([str(RC_DIR / "build.sh")], check=True)


def ensure_project(work: Path) -> None:
    subprocess.run(
        [str(RC_DIR / "container.sh"), "--work", str(work), "prepare"], check=True
    )


def run_peer_container(
    *,
    name: str,
    network: str,
    work: Path,
    run_dir: Path,
    replays_dir: Path,
    scene: str,
    job_flag: str,
    job_file: Path,
    done_prefix: str,
    log: Path,
    xdg: Path,
    timeout_s: int,
) -> None:
    podman(
        "run", "-d", "--init",
        "--name", name,
        "--network", network,
        "-v", f"{REPO}:{REPO}:ro",
        "-v", f"{work}:{work}:ro",
        "-v", f"{run_dir}:{run_dir}",
        "-v", f"{replays_dir}:{replays_dir}:ro",
        "-e", "LP_NUM_THREADS=1",
        "--entrypoint", "python3",
        IMAGE,
        str(TOOL_DIR / "run_peer.py"),
        "--work", str(work),
        "--scene", scene,
        f"--job-flag={job_flag}",
        "--job-file", str(job_file),
        "--done-prefix", done_prefix,
        "--log", str(log),
        "--xdg", str(xdg),
        "--timeout", str(timeout_s),
    )


def run_relay_container(name: str, network: str, host_name: str, profile: Profile, seed: int) -> None:
    args = [
        "run", "-d",
        "--name", name,
        "--network", network,
        "--entrypoint", "/bin/udp-netsim",
        IMAGE,
        "--listen", f"0.0.0.0:{RELAY_PORT}",
        "--upstream", f"{host_name}:{HOST_PORT}",
        "--delay-ms", str(profile.delay_ms),
        "--jitter-ms", str(profile.jitter_ms),
        "--loss", str(profile.loss),
        "--seed", str(seed),
        "--stats-secs", "2",
    ]
    if profile.reorder:
        args.append("--reorder")
    podman(*args)


def parse_done(log_path: Path) -> dict[str, Any]:
    """NetPlay.gd's NETTEST_DONE line. A stalled match's Godot process is
    SIGKILLed by run_peer.py's subprocess timeout and never prints one, so
    this returns {"error": ...} instead of raising: run_one still compares
    whatever each side's trace reached (see read_trace_safe) rather than
    aborting with no evidence of how far the match got.
    """
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    match = re.search(r"^NETTEST_DONE (.*)$", text, re.M)
    if not match:
        return {"error": f"no NETTEST_DONE line in {log_path}"}
    return json.loads(match.group(1))


def parse_replay_check_done(log_path: Path) -> dict[str, Any]:
    text = log_path.read_text(errors="replace") if log_path.exists() else ""
    match = re.search(r"^REPLAY_CHECK_DONE (.*)$", text, re.M)
    if not match:
        raise CheckError(f"{log_path}: no REPLAY_CHECK_DONE line; log:\n{text}")
    return json.loads(match.group(1))


def read_trace_safe(rc: Any, path: Path) -> list[dict[str, Any]]:
    """rc.read_trace, tolerant of a stalled match's trace file: missing
    entirely (no tick was ever flushed), or with a truncated last line (a
    SIGKILL mid-write; see NetPlay.gd's periodic trace.flush()).
    """
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            break
    return rows


def read_dumps_safe(path: Path) -> dict[int, Any]:
    """rc.read_dumps, tolerant of a truncated last line (see read_trace_safe)."""
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(errors="replace").splitlines():
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            break
        out[record["t"]] = record["s"]
    return out


def relay_stats(name: str) -> dict[str, Any]:
    text = container_logs(name)
    listening = re.search(r"^udp-netsim: listening on .*$", text, re.M)
    counters = re.findall(
        r"^udp-netsim: up forwarded=(\d+) dropped=(\d+) \| down forwarded=(\d+) dropped=(\d+)$",
        text,
        re.M,
    )
    last = counters[-1] if counters else None
    return {
        "listening_line": listening.group(0) if listening else None,
        "up_forwarded": int(last[0]) if last else 0,
        "up_dropped": int(last[1]) if last else 0,
        "down_forwarded": int(last[2]) if last else 0,
        "down_dropped": int(last[3]) if last else 0,
    }


def turn_latency_stats(turns_path: Path) -> dict[str, Any]:
    waits = []
    for line in turns_path.read_text().splitlines():
        if not line:
            continue
        row = json.loads(line)
        if row.get("turn_wait_ms", 0) > 0:
            waits.append(row["turn_wait_ms"])
    if not waits:
        return {"count": 0}
    waits.sort()

    def pct(p: float) -> float:
        idx = min(len(waits) - 1, int(len(waits) * p))
        return waits[idx]

    return {
        "count": len(waits),
        "min_ms": waits[0],
        "mean_ms": round(statistics.mean(waits), 1),
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
        "max_ms": waits[-1],
    }


def first_field_diff(rc: Any, dump_a: Path, dump_b: Path, tick: int) -> list[str]:
    snaps_a = read_dumps_safe(dump_a)
    snaps_b = read_dumps_safe(dump_b)
    if tick not in snaps_a or tick not in snaps_b:
        return [f"tick {tick} missing from one side's dump (a has {tick in snaps_a}, b has {tick in snaps_b})"]
    return rc.snapshot_diff(snaps_a[tick], snaps_b[tick])


def run_one(replay: Path, profile_name: str, seed: int, work: Path, out_root: Path, timeout_s: int) -> dict[str, Any]:
    rc = load_replay_check_run()
    profile = PROFILES[profile_name]
    # Truncate the replay stem, never the random suffix. A 40-character cut
    # of the whole id once dropped the suffix of a long Discord replay name,
    # so two runs of one replay and profile shared a directory, and the
    # second run overwrote the first one's traces.
    run_id = sanitize(f"{replay.stem[:20]}-{profile_name}-{seed}-{uuid.uuid4().hex[:6]}")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    net_name = f"nt-{run_id}"[:63]
    names = {role: f"nt-{run_id}-{role}"[:63] for role in ("relay", "host", "client", "playback", "playbackdump")}
    replays_dir = replay.resolve().parent

    report: dict[str, Any] = {
        "run_id": run_id,
        "replay": str(replay),
        "profile": profile_name,
        "profile_params": dataclasses.asdict(profile),
        "seed": seed,
    }

    podman("network", "create", net_name)
    try:
        ignore = rc.ignore_list()
        # Session metadata in game.gd: these fields describe how this
        # process runs the match, not the match itself, so they differ by
        # definition between the two peers and an offline playback.
        # - my_id: each peer's own side (Network.player_id), 1 on one peer
        #   and 2 on the other; used by the forfeit button (Network.gd
        #   player_forfeit, ui/UILayer.gd).
        # - p1_username, p2_username: lobby names, null offline.
        # - singleplayer, started_multiplayer: the mode flags.
        # - network_sync_tick: the turn protocol's last synced tick
        #   (game.gd ~1489); -100 offline.
        # Runs of 2026-11-13 differed from their offline playback in exactly
        # these game fields at every tick. tools/replay_check's ignore.txt
        # has no such entries, because it never compares two modes.
        peer_ignore = ignore + [
            "my_id", "p1_username", "p2_username", "singleplayer", "started_multiplayer", "network_sync_tick",
        ]
        host_dir = run_dir / "host"
        client_dir = run_dir / "client"
        for d in (host_dir, client_dir):
            d.mkdir(parents=True, exist_ok=True)

        host_job = host_dir / "job.json"
        host_job.write_text(json.dumps({
            "role": "host",
            "player_name": "Host",
            "port": HOST_PORT,
            "replay": str(replay),
            "trace": str(host_dir / "trace.jsonl"),
            "dump": str(host_dir / "dump.jsonl"),
            "turns": str(host_dir / "turns.jsonl"),
            "replay_out": str(host_dir / "match.replay"),
            "ignore": peer_ignore,
        }))

        # The host container must exist before the relay starts, since
        # udp-netsim resolves --upstream once at startup with no retry.
        # Its IP address is used, not its name: podman assigns the
        # address at container creation, but aardvark-dns name
        # resolution lags behind that by a short, variable amount
        # (observed on this host) and udp-netsim has no retry for it.
        run_peer_container(
            name=names["host"], network=net_name, work=work, run_dir=run_dir, replays_dir=replays_dir,
            scene="res://tools/nettest/NetPlay.tscn", job_flag="--nt-job=", job_file=host_job,
            done_prefix="NETTEST_DONE", log=host_dir / "godot.log", xdg=host_dir / "xdg", timeout_s=timeout_s,
        )
        host_ip = container_ip(names["host"], net_name)
        run_relay_container(names["relay"], net_name, host_ip, profile, seed)
        wait_for_log(names["relay"], r"udp-netsim: listening on", RELAY_READY_TIMEOUT_S)
        wait_for_file_log(names["host"], host_dir / "godot.log", r"NETTEST_READY role=host", PEER_READY_TIMEOUT_S)

        relay_ip = container_ip(names["relay"], net_name)
        client_job = client_dir / "job.json"
        client_job.write_text(json.dumps({
            "role": "client",
            "player_name": "Client",
            "relay_host": relay_ip,
            "relay_port": RELAY_PORT,
            "replay": str(replay),
            "trace": str(client_dir / "trace.jsonl"),
            "dump": str(client_dir / "dump.jsonl"),
            "turns": str(client_dir / "turns.jsonl"),
            "replay_out": str(client_dir / "match.replay"),
            "ignore": peer_ignore,
        }))

        run_peer_container(
            name=names["client"], network=net_name, work=work, run_dir=run_dir, replays_dir=replays_dir,
            scene="res://tools/nettest/NetPlay.tscn", job_flag="--nt-job=", job_file=client_job,
            done_prefix="NETTEST_DONE", log=client_dir / "godot.log", xdg=client_dir / "xdg", timeout_s=timeout_s,
        )
        wait_for_file_log(names["client"], client_dir / "godot.log", r"NETTEST_READY role=client", PEER_READY_TIMEOUT_S)

        wait_for_exit(names["host"], timeout_s + 30)
        wait_for_exit(names["client"], timeout_s + 30)

        host_result = parse_done(host_dir / "godot.log")
        client_result = parse_done(client_dir / "godot.log")
        report["host"] = host_result
        report["client"] = client_result
        report["relay"] = relay_stats(names["relay"])
    finally:
        for role in ("relay", "host", "client"):
            podman("rm", "-f", names[role], check=False)
        podman("network", "rm", net_name, check=False)

    stalled = "error" in report.get("host", {}) or "error" in report.get("client", {})
    report["stalled"] = stalled
    if stalled:
        # run_peer.py's subprocess timeout SIGKILLs Godot, so _finish()
        # (NetPlay.gd) never runs and no match.replay exists. trace.jsonl
        # and dump.jsonl still have everything up to the last flush
        # (every 10 ticks; see NetPlay.gd _on_post_tick), so check (a)
        # still runs on whatever each side reached, and the report shows
        # how far the match got instead of only that it did not finish.
        for side, d in (("host", host_dir), ("client", client_dir)):
            log = d / "godot.log"
            if log.exists():
                report[f"{side}_log_tail"] = log.read_text(errors="replace").splitlines()[-20:]

    # Check a: host vs. client per-tick hashes, for the whole live match
    # (or for as much of it as each side reached, if it stalled).
    host_trace = read_trace_safe(rc, host_dir / "trace.jsonl")
    client_trace = read_trace_safe(rc, client_dir / "trace.jsonl")
    comparison = rc.compare_traces(host_trace, client_trace)
    check_a: dict[str, Any] = {
        "ok": comparison.ok and not stalled,
        "problem": comparison.problem,
        "episodes": len(comparison.episodes),
        "differing_ticks": comparison.differing_ticks,
        "final_equal": comparison.final_equal,
        "host_ticks": len(host_trace),
        "client_ticks": len(client_trace),
    }
    if comparison.episodes:
        first = comparison.episodes[0]
        check_a["first_tick"] = first.tick
        check_a["first_objects"] = first.parts
        check_a["field_diff"] = first_field_diff(rc, host_dir / "dump.jsonl", client_dir / "dump.jsonl", first.tick)
    report["check_a"] = check_a

    if not (host_dir / "match.replay").exists():
        report["check_b"] = {"ok": False, "reason": "no match.replay: the match did not finish, so ReplayManager.save_replay never ran"}
        report["check_c"] = {
            "host_turn_latency": turn_latency_stats(host_dir / "turns.jsonl") if (host_dir / "turns.jsonl").exists() else {"count": 0},
            "client_turn_latency": turn_latency_stats(client_dir / "turns.jsonl") if (client_dir / "turns.jsonl").exists() else {"count": 0},
        }
        _write_report(run_dir, report)
        return report


    # Check b: the live match (host's trace) vs. an offline playback of the
    # host's own saved replay, through tools/replay_check/ReplayCheck.gd
    # unchanged.
    playback_dir = run_dir / "playback"
    playback_dir.mkdir(parents=True, exist_ok=True)
    playback_trace = playback_dir / "trace.jsonl"
    playback_job_path = playback_dir / "job.json"
    playback_job_path.write_text(json.dumps({
        "mode": "playback",
        "replay": str(host_dir / "match.replay"),
        "trace": str(playback_trace),
        "ignore": peer_ignore,
    }))
    podman(
        "network", "create", f"{net_name}-pb",
    )
    try:
        run_peer_container(
            name=names["playback"], network=f"{net_name}-pb", work=work, run_dir=run_dir, replays_dir=replays_dir,
            scene="res://tools/replay_check/ReplayCheck.tscn", job_flag="--rc-job=", job_file=playback_job_path,
            done_prefix="REPLAY_CHECK_DONE", log=playback_dir / "godot.log", xdg=playback_dir / "xdg", timeout_s=timeout_s,
        )
        wait_for_exit(names["playback"], timeout_s + 30)
        playback_done = parse_replay_check_done(playback_dir / "godot.log")
    finally:
        podman("rm", "-f", names["playback"], check=False)
        podman("network", "rm", f"{net_name}-pb", check=False)
    report["playback"] = playback_done

    playback_trace_rows = rc.read_trace(playback_trace)
    fidelity = rc.compare_traces(host_trace, playback_trace_rows)
    check_b: dict[str, Any] = {
        "ok": fidelity.ok,
        "problem": fidelity.problem,
        "episodes": len(fidelity.episodes),
        "differing_ticks": fidelity.differing_ticks,
        "final_equal": fidelity.final_equal,
    }
    if fidelity.episodes:
        first = fidelity.episodes[0]
        check_b["first_tick"] = first.tick
        check_b["first_objects"] = first.parts
        # The playback trace has only hashes; re-run playback once more
        # with a full dump at the first divergent tick to get field names.
        dump_path = playback_dir / "dump.jsonl"
        dump_job_path = playback_dir / "dump_job.json"
        dump_job_path.write_text(json.dumps({
            "mode": "playback",
            "replay": str(host_dir / "match.replay"),
            "trace": str(playback_dir / "dump_trace.jsonl"),
            "ignore": peer_ignore,
            "dump_ticks": [first.tick],
            "dump": str(dump_path),
        }))
        podman("network", "create", f"{net_name}-pbd")
        try:
            run_peer_container(
                name=names["playbackdump"], network=f"{net_name}-pbd", work=work, run_dir=run_dir, replays_dir=replays_dir,
                scene="res://tools/replay_check/ReplayCheck.tscn", job_flag="--rc-job=", job_file=dump_job_path,
                done_prefix="REPLAY_CHECK_DONE", log=playback_dir / "dump_godot.log", xdg=playback_dir / "dump_xdg", timeout_s=timeout_s,
            )
            wait_for_exit(names["playbackdump"], timeout_s + 30)
        finally:
            podman("rm", "-f", names["playbackdump"], check=False)
            podman("network", "rm", f"{net_name}-pbd", check=False)
        check_b["field_diff"] = first_field_diff(rc, host_dir / "dump.jsonl", dump_path, first.tick)
    report["check_b"] = check_b

    # Check c: turns, finish state, latency.
    report["check_c"] = {
        "host_turn_latency": turn_latency_stats(host_dir / "turns.jsonl"),
        "client_turn_latency": turn_latency_stats(client_dir / "turns.jsonl"),
    }

    _write_report(run_dir, report)
    return report


def _write_report(run_dir: Path, report: dict[str, Any]) -> None:
    (run_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    parser.add_argument("--seed", type=int, default=1, help="udp-netsim random seed")
    parser.add_argument("--work", type=Path, default=Path.home() / ".cache" / "hustle-nettest")
    parser.add_argument("--out", type=Path, default=REPO / ".scratch" / "nettest" / "runs")
    parser.add_argument("--timeout", type=int, default=180, help="seconds; each Godot process's deadline")
    args = parser.parse_args()

    if shutil.which("podman") is None:
        print("error: podman not found on PATH", file=sys.stderr)
        return 2

    try:
        ensure_image()
        ensure_project(args.work)
        report = run_one(args.replay.resolve(), args.profile, args.seed, args.work.resolve(), args.out, args.timeout)
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    ok = report.get("check_a", {}).get("ok") and report.get("check_b", {}).get("ok")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
