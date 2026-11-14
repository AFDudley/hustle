#!/usr/bin/env python3
"""Compares RngDiff.gd's output with rust/src/main.rs's output.

Usage: compare.py <godot_out.jsonl> <rust_out.jsonl> <report.json>

The two files have one JSON object per line, in the same order, one of
five record kinds ("hash_int", "hash_string", "hash_rng",
"hash_static_rng", "seq"; see RngDiff.gd). This script lines them up by
position (both were produced from the same probe list), and for each
pair checks that every field except "result" is equal (so the two sides
agree on which probe they ran), then that "result" is equal: a plain
value for the first four kinds, a list of per-op values for "seq".

It groups every disagreement by record kind, and prints one example per
group. Exit code is 0 when every record matched, 1 when at least one did
not.
"""

import json
import sys
from collections import defaultdict


def load(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def fields_except_result(record):
    return {k: v for k, v in record.items() if k != "result"}


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    godot_path, rust_path, report_path = sys.argv[1:4]
    godot = load(godot_path)
    rust = load(rust_path)

    if len(godot) != len(rust):
        print(
            f"FATAL: line count differs: godot={len(godot)} rust={len(rust)}",
            file=sys.stderr,
        )
        return 2

    total_records = len(godot)
    per_kind_total = defaultdict(int)
    per_kind_match = defaultdict(int)
    mismatches = defaultdict(list)
    MAX_EXAMPLES = 5

    for g, r in zip(godot, rust):
        if fields_except_result(g) != fields_except_result(r):
            print(f"FATAL: probe diverged: godot={g} rust={r}", file=sys.stderr)
            return 2
        kind = g["kind"]
        per_kind_total[kind] += 1
        if g["result"] == r["result"]:
            per_kind_match[kind] += 1
        elif len(mismatches[kind]) < MAX_EXAMPLES:
            mismatches[kind].append({"probe": fields_except_result(g),
                                      "godot": g["result"], "rust": r["result"]})

    report = {
        "total_records": total_records,
        "per_kind": {
            k: {"records": per_kind_total[k], "matched": per_kind_match[k]}
            for k in sorted(per_kind_total)
        },
        "mismatches": mismatches,
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    total_matched = sum(per_kind_match.values())
    print(f"{total_matched}/{total_records} records matched exactly.")
    for kind in sorted(per_kind_total):
        print(f"  {kind}: {per_kind_match[kind]}/{per_kind_total[kind]} matched.")
    any_mismatch = False
    for kind, exs in mismatches.items():
        any_mismatch = True
        print(f"  MISMATCH {kind}: example: probe={exs[0]['probe']} "
              f"godot={exs[0]['godot']} rust={exs[0]['rust']}")
    if not any_mismatch:
        print("0 mismatches.")
    return 1 if any_mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
