#!/usr/bin/env python3
"""Compares FixedMathDiff.gd's output with rust/src/main.rs's output.

Usage: compare.py <godot_out.jsonl> <rust_out.jsonl> <report.json>

The two files have one JSON object per line, in the same order:
{"method", "args", "result", "error"}. This script lines them up by
position (both were produced from the same input list; see README.md), and
for each pair checks:

  - the two "error" flags agree (both the game and the Rust port saw the
    same call as unparsable, divide-by-zero, or similar), and
  - when neither errored, "result" is equal (deep equality: a dict result
    compares key by key).

It groups every disagreement by method, and prints one example per group.
Exit code is 0 when every call matched, 1 when at least one did not.
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


def values_equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(values_equal(a[k], b[k]) for k in a)
    return a == b


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

    total_calls = len(godot)
    per_method_total = defaultdict(int)
    per_method_match = defaultdict(int)
    # mismatch_class -> method -> list of example dicts (capped)
    mismatches = defaultdict(lambda: defaultdict(list))
    MAX_EXAMPLES = 5

    for g, r in zip(godot, rust):
        if g["method"] != r["method"] or g["args"] != r["args"]:
            print(
                f"FATAL: call order diverged: godot={g} rust={r}",
                file=sys.stderr,
            )
            return 2
        method = g["method"]
        per_method_total[method] += 1
        g_err, r_err = g["error"], r["error"]
        example = {"args": g["args"], "godot": g, "rust": r}
        if g_err != r_err:
            cls = "godot_error_rust_ok" if g_err else "rust_error_godot_ok"
            if len(mismatches[cls][method]) < MAX_EXAMPLES:
                mismatches[cls][method].append(example)
            continue
        if g_err and r_err:
            # Both sides agree the call fails. A match: neither produced a
            # value to compare.
            per_method_match[method] += 1
            continue
        if values_equal(g["result"], r["result"]):
            per_method_match[method] += 1
        else:
            if len(mismatches["value_mismatch"][method]) < MAX_EXAMPLES:
                mismatches["value_mismatch"][method].append(example)

    report = {
        "total_calls": total_calls,
        "methods": sorted(per_method_total.keys()),
        "per_method": {
            m: {"calls": per_method_total[m], "matched": per_method_match[m]}
            for m in sorted(per_method_total)
        },
        "mismatch_classes": {
            cls: {m: exs for m, exs in by_method.items()}
            for cls, by_method in mismatches.items()
        },
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    total_matched = sum(per_method_match.values())
    print(f"{total_matched}/{total_calls} calls matched exactly.")
    any_mismatch = False
    for cls, by_method in mismatches.items():
        for method, exs in by_method.items():
            any_mismatch = True
            total_for_method = per_method_total[method]
            mismatched_for_method = total_for_method - per_method_match[method]
            print(
                f"  {cls}: {method}: {mismatched_for_method}/{total_for_method} "
                f"calls differ. Example: {exs[0]['args']} -> "
                f"godot={exs[0]['godot'].get('result')} "
                f"(error={exs[0]['godot']['error']}) "
                f"rust={exs[0]['rust'].get('result')} "
                f"(error={exs[0]['rust']['error']})"
            )
    if not any_mismatch:
        print("0 mismatches.")
    return 1 if any_mismatch else 0


if __name__ == "__main__":
    sys.exit(main())
