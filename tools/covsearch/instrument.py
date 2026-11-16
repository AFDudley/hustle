"""Coverage-directed-search instrumentation for GDScript 3 (Godot 3.5).

Extends tools/coverage/instrument.py (line probes, imported unchanged) with
two more probe kinds, needed by the design in tools/covsearch/README.md:

- branch probes: the true and false outcome of every `if`/`elif`/`while`
  condition.
- value-class probes: the value class (zero, negative, positive, type
  limit; see Probe.gd) of each argument at a `chara.<method>(...)` call,
  the game's FGObject native calls.

Only files under IN_SCOPE_DIRS are instrumented (the Ninja vs Ninja
simulation files named in the covsearch design). Everything else runs
uninstrumented, same speed as a plain run.

Probe id layout per file, in source order: for each statement, first its
line probe (if any), then zero or more branch probes (2 ids: true, false)
for an `if`/`elif`/`while` header on that statement's own physical line,
then zero or more value-class probe groups (4 ids each) for `chara.<m>(`
calls on that line. `map.json` records each probe's kind and location so
report.py-style tooling and the covsearch coordinator can group them.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


def _load_coverage_instrument():
    path = Path(__file__).resolve().parent.parent / "coverage" / "instrument.py"
    spec = importlib.util.spec_from_file_location("hustle_coverage_instrument", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_coverage = _load_coverage_instrument()
Block = _coverage.Block
first_word = _coverage.first_word
is_func_header = _coverage.is_func_header
scan = _coverage.scan

AUTOLOAD_NAME = "__COV__"
PROBE_SCRIPT = "res://tools/covsearch/Probe.gd"

# FGObject's native methods, called as `chara.<method>(...)` from obj/BaseObj.gd
# and its subclasses (lab book decision 2.1: `tbfg.so` exports 40 FGObject
# methods). Matched by name, not by a type check: GDScript holds no static
# type for `chara` and the method names do not collide with game-logic
# methods of the same object.
FGOBJECT_METHODS = {
    "set_gravity", "set_ground_friction", "set_air_friction",
    "set_max_ground_speed", "set_max_air_speed", "set_max_fall_speed",
    "set_facing", "set_vel", "set_x", "set_y", "set_position",
    "set_position_str", "set_snap_to_ground", "apply_force",
    "apply_force_str", "apply_force_relative", "apply_force_relative_str",
    "set_gravity_modifier", "apply_grav_custom", "apply_x_fric",
    "apply_y_fric", "move_directly", "move_directly_str",
    "move_directly_relative", "move_directly_relative_str", "set_grounded",
    "add_pushback",
}
CALL_RE = re.compile(r"\bchara\.(" + "|".join(sorted(FGOBJECT_METHODS)) + r")\(")

IN_SCOPE_DIRS = (
    "obj",
    "mechanics",
    "projectile",
)
IN_SCOPE_FILES = (
    "game.gd",
    "characters/BaseChar.gd",
    # Both paths below were wrong from this constant's first commit: the
    # repository has never had a characters/ObjectState.gd or a
    # characters/CharState.gd. ObjectState.gd lives at the repository
    # root; CharState.gd lives under characters/states/ (also matched by
    # IN_SCOPE_GLOBS below, so this entry is redundant for CharState.gd
    # but kept for an explicit, correct file list). The wrong
    # "characters/ObjectState.gd" entry never matched anything, so
    # ObjectState.gd -- central to every character's state machine --
    # was never instrumented; found and fixed while building free
    # coverage (tools/covsearch/free_coverage.md).
    "ObjectState.gd",
    "characters/states/CharState.gd",
)
# The shared code is always in scope. One character's own directory is also
# in scope; set_character() chooses it before instrument() runs. The
# default is Ninja (characters/stickman).
CHARACTER_DIRS = {
    "Ninja": "stickman",
    "Cowboy": "swordandgun",
    "Wizard": "wizard",
    "Robot": "robo",
    "Mutant": "mutant",
}
IN_SCOPE_GLOBS = [
    "characters/states/*.gd",
    "characters/stickman/**/*.gd",
]


def set_character(name: str) -> None:
    """Put the named character's own directory in scope, in place of Ninja's."""
    del IN_SCOPE_GLOBS[1:]
    IN_SCOPE_GLOBS.append(f"characters/{CHARACTER_DIRS[name]}/**/*.gd")


def set_all_characters() -> None:
    """Put all five characters' own directories in scope, for mixed pairings."""
    del IN_SCOPE_GLOBS[1:]
    for directory in CHARACTER_DIRS.values():
        IN_SCOPE_GLOBS.append(f"characters/{directory}/**/*.gd")


def in_scope(rel: Path) -> bool:
    rel_s = rel.as_posix()
    if rel_s in IN_SCOPE_FILES:
        return True
    if rel.parts and rel.parts[0] in IN_SCOPE_DIRS:
        return True
    for pattern in IN_SCOPE_GLOBS:
        if rel.match(pattern):
            return True
    return False


def split_args(text: str) -> list[str]:
    """Split a call's argument text on top-level commas."""
    args: list[str] = []
    depth = 0
    quote = ""
    start = 0
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            args.append(text[start:i])
            start = i + 1
        i += 1
    args.append(text[start:])
    return [a for a in args if a.strip() != ""]


def find_call_end(text: str, open_paren: int) -> int:
    """Index just past the ')' matching the '(' at open_paren."""
    depth = 0
    i = open_paren
    quote = ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
            i += 1
            continue
        if c in "\"'":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unbalanced call")


def wrap_value_classes(line: str, next_id: int) -> tuple[str, list[tuple[int, str]]]:
    """Wrap each FGObject call's arguments with __COV__.vc(id, ...).

    Returns the rewritten line and (probe-group-base-id, description) pairs,
    one per wrapped argument, each description naming the method and the
    argument index.
    """
    out = []
    groups: list[tuple[int, str]] = []
    pos = 0
    for m in CALL_RE.finditer(line):
        if m.start() < pos:
            continue
        out.append(line[pos:m.start()])
        method = m.group(1)
        open_paren = m.end() - 1
        end = find_call_end(line, open_paren)
        args_text = line[open_paren + 1:end - 1]
        args = split_args(args_text)
        wrapped = []
        for idx, arg in enumerate(args):
            groups.append((next_id, f"{method} arg {idx}"))
            wrapped.append(f"__COV__.vc({next_id}, ({arg.strip()}))")
            next_id += 4
        out.append(f"chara.{method}(" + ", ".join(wrapped) + ")")
        pos = end
    out.append(line[pos:])
    return "".join(out), groups


COND_RE = re.compile(r"^(if|elif|while)\b(.*):\s*$")


def is_self_contained(code: str) -> bool:
    """True when `code` has balanced brackets and no open string or
    backslash continuation, so it is a condition that fits on one physical
    line (the common GDScript style)."""
    depth = 0
    quote = ""
    i = 0
    while i < len(code):
        c = code[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if code.startswith(quote, i):
                i += len(quote)
                quote = ""
                continue
            i += 1
            continue
        if c == "#":
            break
        if c in "\"'":
            quote = c * 3 if code.startswith(c * 3, i) else c
            i += len(quote)
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        i += 1
    return depth == 0 and quote == "" and not code.rstrip().endswith("\\")


def wrap_branch(line: str, indent: str, true_id: int, false_id: int) -> str | None:
    """Wrap an if/elif/while condition in __COV__.b(true_id, false_id, (cond)):.

    Returns None when the line is not a simple single-line condition header.
    """
    stripped = line[len(indent):]
    if not is_self_contained(stripped):
        return None
    m = COND_RE.match(stripped)
    if not m:
        return None
    keyword, cond = m.group(1), m.group(2).strip()
    if not cond:
        return None
    return f"{indent}{keyword} __COV__.b({true_id}, {false_id}, ({cond})):"


def instrument_source(text: str, next_id: int) -> tuple[str, list[dict]]:
    """Return the instrumented text and a list of probe records.

    Each record is {"id": int, "kind": "line"|"branch_true"|"branch_false"|
    "value_class", "line": 1-based line, "detail": str or null}.
    """
    lines = text.split("\n")
    stack: list[Block] = []
    probes: list[dict] = []
    edits: dict[int, int] = {}

    for lg in scan(lines):
        width = len(lg.indent)
        while stack and width <= stack[-1].indent:
            stack.pop()
        parent = stack[-1].kind if stack else "class"
        in_func = any(b.kind == "func" for b in stack)
        word = first_word(lg.code)

        if parent == "match":
            kind = "pattern"
        elif word in ("elif", "else"):
            kind = "block"
        elif is_func_header(lg.code):
            kind = "func"
        elif word == "class":
            kind = "class"
        elif in_func:
            kind = "match" if word == "match" else "block"
            edits[lg.first] = len(lg.indent)
        else:
            kind = "block"
        if lg.ends_block:
            stack.append(Block(width, kind))

    for line_no in sorted(edits):
        offset = edits[line_no]
        text_line = lines[line_no]
        indent = text_line[:offset]
        body = text_line[offset:]

        word = first_word(body.strip())
        if word in ("if", "elif", "while"):
            true_id, false_id = next_id, next_id + 1
            rewritten = wrap_branch(body, "", true_id, false_id)
            if rewritten is not None:
                body = rewritten
                probes.append({"id": true_id, "kind": "branch_true", "line": line_no + 1, "detail": word})
                probes.append({"id": false_id, "kind": "branch_false", "line": line_no + 1, "detail": word})
                next_id += 2
        if CALL_RE.search(body):
            body, groups = wrap_value_classes(body, next_id)
            for base_id, detail in groups:
                probes.append({"id": base_id, "kind": "value_class", "line": line_no + 1, "detail": detail})
                next_id += 4

        line_probe_id = next_id
        next_id += 1
        lines[line_no] = f"{indent}{AUTOLOAD_NAME}.h[{line_probe_id}] += 1; {body}"
        probes.append({"id": line_probe_id, "kind": "line", "line": line_no + 1, "detail": None})

    return "\n".join(lines), probes


def instrument(project: Path) -> dict:
    """Instrument every in-scope .gd file under `project` in place.

    Returns {"size": N, "files": {res_path: [probe record, ...]}}.
    """
    files: dict[str, list[dict]] = {}
    next_id = 0
    for path in sorted(project.rglob("*.gd")):
        rel = path.relative_to(project)
        if rel.parts[0] in ("tools", ".scratch", ".import"):
            continue
        if not in_scope(rel):
            continue
        text = path.read_text(encoding="utf-8")
        new_text, probes = instrument_source(text, next_id)
        if probes:
            path.write_text(new_text, encoding="utf-8")
            files["res://" + rel.as_posix()] = probes
            next_id += sum(4 if p["kind"] == "value_class" else 1 for p in probes)

    probe_script = project / "tools" / "covsearch" / "Probe.gd"
    probe_script.write_text(probe_script.read_text().replace("const SIZE = 0", f"const SIZE = {next_id}"))
    project_file = project / "project.godot"
    text = project_file.read_text()
    if "\n[autoload]\n" not in text:
        raise RuntimeError(f"{project_file}: no [autoload] section")
    autoload = f'{AUTOLOAD_NAME}="*{PROBE_SCRIPT}"'
    project_file.write_text(text.replace("\n[autoload]\n", f"\n[autoload]\n\n{autoload}\n", 1))
    return {"size": next_id, "files": files}


def write_map(cov_map: dict, dest: Path) -> None:
    dest.write_text(json.dumps(cov_map))


if __name__ == "__main__":
    project_dir = Path(sys.argv[1])
    result = instrument(project_dir)
    write_map(result, Path(sys.argv[2]))
    print(f"instrumented {len(result['files'])} files, {result['size']} probes", file=sys.stderr)
