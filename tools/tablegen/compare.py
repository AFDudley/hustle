#!/usr/bin/env python3
"""Compares TableGenProbe.gd's output with table_dump's output.

Usage: compare.py <godot_out.jsonl> <dump_out.jsonl> <manifest.json> <report.json>

Both files hold one JSON object per line: one record per state, hitbox,
or root, tagged `"scope"`, `"record"` ("state"|"hitbox"|"root"), and (for
state/hitbox) `"state"` and (for hitbox) `"hitbox_index"`. Records join
by that key, not by line position, because the engine's own child-node
order and the generated Rust's declaration order need not match.

`manifest.json`'s `field_tags` (name -> type tag) says how to compare
each field: a "fixed" field converts through `fixed_parse` (the same
parser the generated tables use) before comparing; a "categories"/
"stances" field compares as a set of tokens, split the same way the
game's own `Utils.split_lines` does; a "state_self_opt"/"misc_tag"/
"orb_trigger"/"stance_self_opt" field treats an empty string as the
JSON `null` the generated side uses for "none"; everything else
compares directly. See `tools/tablegen/README.md`.

A field table_dump carries but the probe's record omits is not a
mismatch: that field belongs to a mixin this particular state does not
use (RobotState's fields on a Ninja state, say), so the table holds
that mixin's own declared default and the engine has no property there
to compare against. A field the probe reports but table_dump omits is
a real bug (a schema gap) and fails the run.

Exit code is 0 when every comparable field matched, 1 otherwise.
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections import defaultdict

META_KEYS = {"scope", "record", "state", "hitbox_index"}


def load_jsonl(path: str) -> list[dict]:
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def record_key(r: dict):
    if r["record"] == "root":
        return (r["scope"], "root")
    if r["record"] == "state":
        return (r["scope"], "state", r["state"])
    return (r["scope"], "hitbox", r["state"], r["hitbox_index"])


def split_lines(s: str) -> set[str]:
    return {line.strip() for line in s.split("\n") if line.strip()}


def fixed_raw_batch(values: list[str]) -> list[int | None]:
    if not values:
        return []
    out = subprocess.run(
        ["fixed_parse"], input="\n".join(values) + "\n", capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert len(out) == len(values)
    return [None if v == "INVALID" else int(v) for v in out]


def main() -> int:
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    godot_path, dump_path, manifest_path, report_path = sys.argv[1:5]
    godot = load_jsonl(godot_path)
    dump = load_jsonl(dump_path)
    manifest = json.load(open(manifest_path))
    field_tags: dict[str, str] = manifest["field_tags"]

    godot_by_key = {record_key(r): r for r in godot}
    dump_by_key = {record_key(r): r for r in dump}
    assert len(godot_by_key) == len(godot), "duplicate key in probe output"
    assert len(dump_by_key) == len(dump), "duplicate key in dump output"

    godot_keys = set(godot_by_key)
    dump_keys = set(dump_by_key)
    only_godot = sorted(map(str, godot_keys - dump_keys))
    only_dump = sorted(map(str, dump_keys - godot_keys))

    # Batch every "fixed" string value in the probe's output through
    # fixed_parse once, up front.
    fixed_names = {name for name, tag in field_tags.items() if tag == "fixed"}
    fixed_strings = sorted(
        {r[name] for r in godot for name in fixed_names if name in r}
    )
    fixed_lookup = dict(zip(fixed_strings, fixed_raw_batch(fixed_strings)))

    per_scope_counts = defaultdict(lambda: {"states": 0, "hitboxes": 0, "fields": 0})
    for r in godot:
        if r["record"] == "state":
            per_scope_counts[r["scope"]]["states"] += 1
        elif r["record"] == "hitbox":
            per_scope_counts[r["scope"]]["hitboxes"] += 1

    mismatches = []
    fields_compared = 0

    def compare_field(name: str, tag: str, g_val, d_val, key) -> bool:
        if tag == "fixed":
            return fixed_lookup[g_val] == d_val
        if tag in ("stances", "categories") or tag.startswith("categories:"):
            return split_lines(g_val) == set(d_val)
        if tag == "throw_positions":
            g_set = {(e["animation"], e["frame"], int(e["x"]), int(e["y"])) for e in g_val}
            d_set = {(e["animation"], e["frame"], int(e["x"]), int(e["y"])) for e in d_val}
            return g_set == d_set
        if tag in ("state_self_opt", "misc_tag", "orb_trigger", "stance_self_opt"):
            g_norm = None if g_val == "" else g_val
            return g_norm == d_val
        return g_val == d_val

    for key in sorted(godot_keys & dump_keys, key=str):
        g = godot_by_key[key]
        d = dump_by_key[key]
        names = (set(g) | set(d)) - META_KEYS
        for name in sorted(names):
            tag = field_tags.get(name)
            if tag is None:
                continue
            if name not in g:
                continue  # a mixin field not applicable to this record; see module docstring.
            if name not in d:
                mismatches.append({"key": key, "field": name, "error": "missing from table_dump"})
                continue
            per_scope_counts[key[0]]["fields"] += 1
            fields_compared += 1
            if not compare_field(name, tag, g[name], d[name], key):
                mismatches.append({"key": key, "field": name, "godot": g[name], "table_dump": d[name]})

    report = {
        "total_godot_records": len(godot),
        "total_dump_records": len(dump),
        "fields_compared": fields_compared,
        "only_in_godot": only_godot,
        "only_in_table_dump": only_dump,
        "per_scope": per_scope_counts,
        "mismatches": mismatches[:200],
        "mismatch_count": len(mismatches),
    }
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"{fields_compared} fields compared across {len(godot_keys & dump_keys)} records.")
    for scope in sorted(per_scope_counts):
        c = per_scope_counts[scope]
        print(f"  {scope}: {c['states']} states, {c['hitboxes']} hitboxes, {c['fields']} fields")
    if only_godot:
        print(f"FATAL: {len(only_godot)} record(s) only in the probe output, e.g. {only_godot[:5]}")
    if only_dump:
        print(f"FATAL: {len(only_dump)} record(s) only in table_dump output, e.g. {only_dump[:5]}")
    if mismatches:
        print(f"MISMATCH: {len(mismatches)} field(s) disagreed, e.g. {mismatches[0]}")
    else:
        print("0 mismatches.")

    return 0 if not (only_godot or only_dump or mismatches) else 1


if __name__ == "__main__":
    sys.exit(main())
