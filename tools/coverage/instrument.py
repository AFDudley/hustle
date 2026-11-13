"""Line-coverage instrumentation for GDScript 3 (Godot 3.5).

See tools/coverage/README.md. Summary:

The instrumenter rewrites every .gd file in a disposable project copy. It
puts a probe in front of each statement in a function body, on the same
line, separated by a semicolon:

    __COV__.h[17] += 1; if x > 0:

GDScript 3 accepts this in front of simple and compound statements (if,
for, while, match), in static functions and in inner classes. Line numbers
do not change, so a SCRIPT ERROR in a coverage run points at the right line.

Lines that get no probe:
  - elif/else lines and match patterns: nothing can go in front of them.
    A body on the same line ("else: return x") cannot take a probe either:
    in the container, "TYPE_OBJECT: __COV__.h[1] += 1; valid = false" gave
    "Parse Error: Unexpected assign", and an inline else/elif/one-line func
    with a probe made the test scene fail to load. The repository has 70
    such inline bodies, against about 27,000 probed statements.
  - one-line functions ("func f(): return 1"), for the same reason.
  - class-level lines (var, const, signal, extends): not executable here.

__COV__ is an autoload (Coverage.gd). instrument() registers it as the first
autoload in the copy's project.godot, so the other autoloads can count
their own _init code.

Rejected alternative: jamie-pate/godot-code-coverage at e03df3be (the last
Godot 3.5 version). It reloads scripts at run time with inserted probe
lines. On this game it produced about 21,000 SCRIPT ERRORs: parse errors
after reload (inherited members not found, cyclic preloads, an inline
"else"), and the Game script failed to load, so no replay ran.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

AUTOLOAD_NAME = "__COV__"
PROBE = AUTOLOAD_NAME + ".h[{}] += 1; "
# Keywords that can come before "func" on a function header line.
FUNC_PREFIXES = {
    "static",
    "remote",
    "master",
    "puppet",
    "slave",
    "sync",
    "remotesync",
    "mastersync",
    "puppetsync",
}
WORD_RE = re.compile(r"[A-Za-z_]\w*")


@dataclass
class Logical:
    """One logical line: a statement that can span several physical lines."""

    first: int  # 0-based index of its first physical line
    indent: str  # leading whitespace of the first physical line
    code: str  # the text without comments, joined over its physical lines
    ends_block: bool  # the code ends with ":" (a block header, body below)


def scan(lines: list[str]) -> list[Logical]:
    """Split a GDScript file into logical lines.

    Tracks strings ("", '', triple-quoted), comments, bracket depth and
    backslash continuations, so brackets or "#" inside strings do not
    confuse it.
    """
    out: list[Logical] = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = line[: len(line) - len(line.lstrip())]
        first = i
        depth = 0
        quote = ""  # current string delimiter, if inside a string
        code_parts: list[str] = []
        last_colon_end = False
        while True:
            text = lines[i]
            j = len(indent) if i == first else 0
            buf = []
            while j < len(text):
                c = text[j]
                if quote:
                    buf.append(c)
                    if c == "\\":
                        if j + 1 < len(text):
                            buf.append(text[j + 1])
                        j += 2
                        continue
                    if text.startswith(quote, j):
                        buf.append(quote[1:])
                        j += len(quote)
                        quote = ""
                        continue
                    j += 1
                    continue
                if c == "#":
                    break
                if c in "\"'":
                    quote = c * 3 if text.startswith(c * 3, j) else c
                    buf.append(quote)
                    j += len(quote)
                    continue
                if c in "([{":
                    depth += 1
                elif c in ")]}":
                    depth -= 1
                buf.append(c)
                j += 1
            part = "".join(buf).rstrip()
            code_parts.append(part)
            continued = part.endswith("\\") and not quote
            if quote or depth > 0 or continued:
                i += 1
                if i >= n:
                    break
                continue
            last_colon_end = part.endswith(":")
            break
        code = " ".join(p.strip() for p in code_parts)
        out.append(Logical(first, indent, code, last_colon_end))
        i += 1
    return out


def first_word(code: str) -> str:
    m = WORD_RE.match(code)
    return m.group(0) if m else ""


def is_func_header(code: str) -> bool:
    words = code.replace("(", " ").split()
    for w in words:
        if w == "func":
            return True
        if w not in FUNC_PREFIXES:
            return False
    return False


@dataclass
class Block:
    indent: int
    kind: str  # "func", "class", "match", "pattern", "block"


@dataclass
class FileResult:
    lines: list[str]
    probes: list[int] = field(default_factory=list)  # 0-based line per probe


def instrument_source(text: str, next_id: int) -> tuple[str, list[tuple[int, int]]]:
    """Return the instrumented text and (probe id, 1-based line) pairs."""
    lines = text.split("\n")
    stack: list[Block] = []
    probes: list[tuple[int, int]] = []
    edits: dict[int, int] = {}  # line -> offset of the probe (the indent)

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
        lines[line_no] = text_line[:offset] + PROBE.format(next_id) + text_line[offset:]
        probes.append((next_id, line_no + 1))
        next_id += 1
    return "\n".join(lines), probes


COVERAGE_SCRIPT = "res://tools/coverage/Coverage.gd"


def instrument(
    project: Path, exclude_dirs: tuple[str, ...] = ("tools", ".scratch", ".import")
) -> dict:
    """Instrument every .gd file under `project` in place. Return the probe map.

    The map is {"files": {res_path: [[probe id, line], ...]}, "size": N}.
    It also writes the size into the copy's Coverage.gd and registers it as
    the first autoload.
    """
    files: dict[str, list[list[int]]] = {}
    next_id = 0
    for path in sorted(project.rglob("*.gd")):
        rel = path.relative_to(project)
        if rel.parts[0] in exclude_dirs:
            continue
        text = path.read_text(encoding="utf-8")
        new_text, probes = instrument_source(text, next_id)
        if probes:
            path.write_text(new_text, encoding="utf-8")
            files["res://" + rel.as_posix()] = [[pid, line] for pid, line in probes]
            next_id += len(probes)

    cov = project / "tools" / "coverage" / "Coverage.gd"
    cov.write_text(cov.read_text().replace("const SIZE = 0", f"const SIZE = {next_id}"))
    project_file = project / "project.godot"
    text = project_file.read_text()
    if "\n[autoload]\n" not in text:
        raise RuntimeError(f"{project_file}: no [autoload] section")
    autoload = f'{AUTOLOAD_NAME}="*{COVERAGE_SCRIPT}"'
    project_file.write_text(
        text.replace("\n[autoload]\n", f"\n[autoload]\n\n{autoload}\n", 1)
    )
    return {"size": next_id, "files": files}


def write_map(cov_map: dict, dest: Path) -> None:
    dest.write_text(json.dumps(cov_map))
