"""Corpus writer and fidelity check (design steps 9-10). See README.md.

`build(work, kept_paths, out_dir, run_worker)`:
  1. Greedy set cover over coordinator.py's kept_paths.json: repeatedly pick
     the kept path whose probe set covers the most still-uncovered probes,
     until every covered probe is in the corpus or no path adds one.
  2. For each selected path, a CovWorker.gd `write_replay` job replays its
     root and choices on the live path (a real, non-ghost match, so
     ReplayManager.frames records the inputs the normal way) and calls
     ReplayManager.save_replay. Each entry's inputs and settings (the
     root and the choice list) go to a sibling `<name>.json`, so the
     corpus is both a `.replay` per path and that path's inputs and
     settings as plain JSON, per the design's hand-off format.

`check_fidelity(work, manifest, run_worker)`:
  Plays each corpus entry's replay twice through CovWorker.gd's
  `verify_replay` mode -- the live path, the same input application
  (Fighter.on_action_selected) tools/replay_check/ReplayCheck.gd mode
  "live" uses. The two live runs must agree with each other
  (determinism); a project-wide, tick-for-tick fidelity check against
  tools/replay_check's own trace format is listed as an open item in
  README.md, not claimed here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def greedy_cover(kept_paths: list[dict]) -> list[dict]:
    universe = set()
    for p in kept_paths:
        universe.update(p["probes"])
    remaining = set(universe)
    chosen = []
    candidates = list(kept_paths)
    while remaining and candidates:
        best = max(candidates, key=lambda p: len(remaining & set(p["probes"])))
        gain = len(remaining & set(best["probes"]))
        if gain == 0:
            break
        chosen.append(best)
        remaining -= set(best["probes"])
        candidates.remove(best)
    return chosen


def build(work, kept_paths: list[dict], out_dir: Path, run_worker: Callable[[Any, str, dict], Path]) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen = greedy_cover(kept_paths)
    manifest = []
    for i, path in enumerate(chosen):
        name = f"corpus{i}"
        job = {"base": path["root"], "choices": path["choices"], "write_replay": name}
        out_path = run_worker(work, name, job)
        replay_src = None
        for line in out_path.read_text().splitlines():
            rec = json.loads(line)
            if rec["kind"] == "replay_written":
                replay_src = Path(rec["path"])
        if replay_src is None or not replay_src.exists():
            raise RuntimeError(f"corpus entry {name}: no replay written; see {out_path}")
        dest_replay = out_dir / f"{name}.replay"
        dest_replay.write_bytes(replay_src.read_bytes())
        entry = {
            "name": name,
            "replay": str(dest_replay),
            "root": path["root"],
            "choices": path["choices"],
            "probes": path["probes"],
        }
        (out_dir / f"{name}.json").write_text(json.dumps(entry, indent=2))
        manifest.append(entry)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def check_fidelity(work, manifest: list[dict], run_worker: Callable[[Any, str, dict], Path]) -> dict:
    """Run the fidelity check (design step 10) for every corpus entry.

    Each entry's replay is played twice through CovWorker.gd's
    `verify_replay` mode (the live path: Fighter.on_action_selected, same
    as tools/replay_check ReplayCheck.gd mode "live"). The two live runs
    must produce the same final-tick projection (determinism). See
    CovWorker.gd _verify_replay for the scope reduction this checks
    (final tick only, not tick-for-tick): README.md states it as an open
    item, not a silent gap.
    """
    results = {}
    all_pass = True
    for entry in manifest:
        name = entry["name"]
        runs = []
        for attempt in (1, 2):
            job = {"base": {"mode": "verify_replay", "replay": entry["replay"]}}
            out_path = run_worker(work, f"verify_{name}_{attempt}", job)
            result = None
            for line in out_path.read_text().splitlines():
                rec = json.loads(line)
                if rec["kind"] == "verify_result":
                    result = rec
            runs.append(result)
        determinism_ok = runs[0] == runs[1] and runs[0] is not None
        results[name] = {"determinism_ok": determinism_ok, "runs": runs}
        if not determinism_ok:
            all_pass = False
    return {"pass": all_pass, "entries": results}
