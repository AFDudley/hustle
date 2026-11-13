"""Merge GDScript coverage hits and write reports. See tools/coverage/README.md.

Inputs: the probe map from instrument.py and the hit files that Coverage.gd
writes (one per Godot run). Outputs:
  - a text summary per top-level directory and per file,
  - an lcov tracefile (lcov.info), which genhtml and most CI tools read.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path


def merge_hits(hit_files: list[Path]) -> dict[int, int]:
    counts: dict[int, int] = defaultdict(int)
    for path in hit_files:
        for pid, count in json.loads(path.read_text()):
            counts[pid] += count
    return counts


def per_file(cov_map: dict, counts: dict[int, int]) -> dict[str, dict[int, int]]:
    """{res_path: {line: count}}. Two probes on one line cannot happen."""
    out: dict[str, dict[int, int]] = {}
    for res_path, probes in cov_map["files"].items():
        out[res_path] = {line: counts.get(pid, 0) for pid, line in probes}
    return out


def write_lcov(files: dict[str, dict[int, int]], dest: Path) -> None:
    """SF paths are relative to the repository, so genhtml can find the sources."""
    with dest.open("w") as fh:
        for res_path in sorted(files):
            lines = files[res_path]
            fh.write(f"SF:{res_path.removeprefix('res://')}\n")
            for line in sorted(lines):
                fh.write(f"DA:{line},{lines[line]}\n")
            fh.write(f"LH:{sum(1 for c in lines.values() if c)}\nLF:{len(lines)}\n")
            fh.write("end_of_record\n")


def summary(files: dict[str, dict[int, int]], runs: int) -> str:
    by_dir: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    total = [0, 0]
    rows = []
    for res_path, lines in files.items():
        hit = sum(1 for c in lines.values() if c)
        rel = res_path.removeprefix("res://")
        top = rel.split("/")[0] if "/" in rel else "(root)"
        by_dir[top][0] += hit
        by_dir[top][1] += len(lines)
        total[0] += hit
        total[1] += len(lines)
        rows.append((rel, hit, len(lines)))

    def pct(h: int, n: int) -> str:
        return f"{100 * h / n:5.1f}%" if n else "  n/a"

    out = [
        f"GDScript line coverage over {runs} runs: {total[0]}/{total[1]} lines {pct(*total)}",
        "",
    ]
    out.append(f"{'directory':<20} {'covered':>8} {'lines':>7} {'pct':>7}")
    for top, (h, n) in sorted(by_dir.items(), key=lambda kv: -kv[1][1]):
        out.append(f"{top:<20} {h:>8} {n:>7} {pct(h, n):>7}")
    out += ["", f"{'file':<60} {'covered':>8} {'lines':>7} {'pct':>7}"]
    for rel, h, n in sorted(rows):
        out.append(f"{rel:<60} {h:>8} {n:>7} {pct(h, n):>7}")
    return "\n".join(out) + "\n"


def write_reports(cov_dir: Path) -> str:
    """Read <cov_dir>/map.json and <cov_dir>/hits/*.json. Write summary.txt and lcov.info."""
    cov_map = json.loads((cov_dir / "map.json").read_text())
    hit_files = sorted((cov_dir / "hits").glob("*.json"))
    files = per_file(cov_map, merge_hits(hit_files))
    text = summary(files, len(hit_files))
    (cov_dir / "summary.txt").write_text(text)
    write_lcov(files, cov_dir / "lcov.info")
    return text
