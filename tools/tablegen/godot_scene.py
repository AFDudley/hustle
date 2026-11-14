"""A generic reader for Godot 3 text scenes (`.tscn`) and the `export var`
declarations in their scripts (`.gd`).

This module knows nothing about any one state, hitbox, or character. It
reads whatever block or property text is in front of it. `tools/tablegen`'s
own generator (`gen.py`) decides which parsed fields a table keeps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------
# A small recursive-descent reader for one Godot property value.
#
# Godot's text resource format allows: numbers, quoted strings (which
# may hold a literal newline), `true`/`false`/`null`, `Ident(args)` calls
# (`ExtResource(1)`, `SubResource(2)`, `Color(1,0,1,1)`, `Vector2(1,2)`,
# `NodePath("...")`, `PoolStringArray(...)`), `[ ... ]` arrays, and
# `{ ... }` dictionaries. This reader covers exactly that grammar, since
# that is everything the game's own `.tscn` files use.
# ---------------------------------------------------------------------


class ParseError(Exception):
    pass


@dataclass(frozen=True)
class Call:
    """`Ident(arg, arg, ...)`, e.g. `ExtResource(3)`, `Color(1, 0, 1, 1)`.
    Frozen and tuple-valued so a `Call` (an `ExtResource`/`Resource`
    reference to a texture, say) can be a dict key -- `throw_positions`
    keys by the sprite texture resource itself."""

    name: str
    args: tuple[Any, ...]


@dataclass
class EnumRef:
    """A dotted name in a `.gd` default, e.g. `AirType.Grounded` or
    `Hitbox.HitHeight.Mid`. `schema.py` resolves this to the enum's raw
    int against the game's own enum declarations; this module only
    records which names were written."""

    parts: list[str]


def _skip_ws(text: str, pos: int) -> int:
    n = len(text)
    while pos < n and text[pos] in " \t\r\n":
        pos += 1
    return pos


def _parse_string(text: str, pos: int) -> tuple[str, int]:
    assert text[pos] == '"'
    pos += 1
    out = []
    n = len(text)
    while True:
        if pos >= n:
            raise ParseError("unterminated string")
        c = text[pos]
        if c == "\\":
            nxt = text[pos + 1]
            if nxt == "n":
                out.append("\n")
            elif nxt == "t":
                out.append("\t")
            elif nxt == '"':
                out.append('"')
            elif nxt == "\\":
                out.append("\\")
            else:
                out.append(nxt)
            pos += 2
            continue
        if c == '"':
            return "".join(out), pos + 1
        out.append(c)
        pos += 1


_NUMBER_RE = re.compile(r"[+-]?(\d+\.\d+([eE][+-]?\d+)?|\d+[eE][+-]?\d+|\d+\.|\.\d+|\d+)")


def _parse_number(text: str, pos: int) -> tuple[int | float, int]:
    m = _NUMBER_RE.match(text, pos)
    if not m:
        raise ParseError(f"not a number at {pos}: {text[pos:pos + 20]!r}")
    raw = m.group(0)
    end = m.end()
    if any(c in raw for c in ".eE"):
        return float(raw), end
    return int(raw), end


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def parse_value(text: str, pos: int) -> tuple[Any, int]:
    pos = _skip_ws(text, pos)
    c = text[pos]
    if c == '"':
        return _parse_string(text, pos)
    if c == "[":
        pos += 1
        items: list[Any] = []
        pos = _skip_ws(text, pos)
        while text[pos] != "]":
            value, pos = parse_value(text, pos)
            items.append(value)
            pos = _skip_ws(text, pos)
            if text[pos] == ",":
                pos += 1
                pos = _skip_ws(text, pos)
        return items, pos + 1
    if c == "{":
        pos += 1
        out: dict[Any, Any] = {}
        pos = _skip_ws(text, pos)
        while text[pos] != "}":
            if text[pos] == '"':
                key, pos = _parse_string(text, pos)
            else:
                key, pos = parse_value(text, pos)
            if isinstance(key, (list, dict)):
                # `__meta__` dictionaries (editor-only data this
                # generator never reads) can nest a literal list or
                # dict as a key; fall back to a stable but
                # non-semantic hashable key rather than crashing.
                # `Call` (an `ExtResource`/`Resource` reference) is
                # hashable directly: `throw_positions` keys by one.
                key = repr(key)
            pos = _skip_ws(text, pos)
            assert text[pos] == ":", f"expected ':' at {pos}: {text[pos:pos + 20]!r}"
            pos = _skip_ws(text, pos + 1)
            value, pos = parse_value(text, pos)
            out[key] = value
            pos = _skip_ws(text, pos)
            if text[pos] == ",":
                pos += 1
                pos = _skip_ws(text, pos)
        return out, pos + 1
    if c in "+-." or c.isdigit():
        return _parse_number(text, pos)
    m = _IDENT_RE.match(text, pos)
    if not m:
        raise ParseError(f"cannot parse value at {pos}: {text[pos:pos + 20]!r}")
    parts = [m.group(0)]
    pos = m.end()
    while pos < len(text) and text[pos] == "." and _IDENT_RE.match(text, pos + 1):
        m2 = _IDENT_RE.match(text, pos + 1)
        parts.append(m2.group(0))
        pos = m2.end()
    if len(parts) > 1:
        return EnumRef(parts), pos
    ident = parts[0]
    if ident == "true":
        return True, pos
    if ident == "false":
        return False, pos
    if ident == "null":
        return None, pos
    pos = _skip_ws(text, pos)
    if pos < len(text) and text[pos] == "(":
        pos += 1
        args: list[Any] = []
        pos = _skip_ws(text, pos)
        while text[pos] != ")":
            value, pos = parse_value(text, pos)
            args.append(value)
            pos = _skip_ws(text, pos)
            if text[pos] == ",":
                pos += 1
                pos = _skip_ws(text, pos)
        return Call(ident, tuple(args)), pos + 1
    # A bare identifier used as a dictionary key or enum-like token.
    return ident, pos


_PROP_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_/]*)\s*=\s*', re.MULTILINE)


def parse_properties(text: str) -> dict[str, Any]:
    """Parses a run of `name = value` assignments, Godot resource style."""
    out: dict[str, Any] = {}
    pos = 0
    n = len(text)
    while True:
        pos = _skip_ws(text, pos)
        if pos >= n:
            break
        m = _PROP_RE.match(text, pos)
        if not m:
            raise ParseError(f"expected a property at {pos}: {text[pos:pos + 40]!r}")
        name = m.group(1)
        pos = m.end()
        value, pos = parse_value(text, pos)
        out[name] = value
    return out


# ---------------------------------------------------------------------
# `.tscn` file structure: ext_resources, sub_resources, and nodes.
# ---------------------------------------------------------------------


@dataclass
class Node:
    name: str
    type: str | None  # None means "inherited from the base/instanced node"
    parent: str | None  # relative node path, "." for the scene root
    instance: int | None  # ext_resource id, only set on the scene's own root
    properties: dict[str, Any] = field(default_factory=dict)

    def path(self) -> str:
        if self.parent in (None, "."):
            return self.name
        return f"{self.parent}/{self.name}"


@dataclass
class Scene:
    path: str
    ext_resources: dict[int, tuple[str, str]]  # id -> (type, res://path)
    sub_resources: dict[int, tuple[str, dict[str, Any]]]  # id -> (type, props)
    nodes: list[Node]

    def root(self) -> Node:
        return self.nodes[0]

    def resolve_ext(self, call: Call | None) -> str | None:
        if call is None:
            return None
        assert call.name == "ExtResource" and len(call.args) == 1
        rid = call.args[0]
        return self.ext_resources[rid][1]


_HEADER_RE = re.compile(r"^\[([a-z_]+)(.*)\]\s*$", re.MULTILINE)
_ATTR_KEY_RE = re.compile(r"(\w+)=")


def _parse_header_attrs(text: str) -> dict[str, Any]:
    """A header's attributes (`name="X" type="Y" instance=ExtResource(1)`),
    parsed with the same value grammar as a property's right-hand side,
    so a bracketed or call-shaped attribute value (`groups=["Fighter"]`,
    `instance=ExtResource( 3 )`) reads correctly even with embedded
    spaces or nested brackets."""
    out: dict[str, Any] = {}
    pos = 0
    n = len(text)
    while True:
        pos = _skip_ws(text, pos)
        if pos >= n:
            break
        m = _ATTR_KEY_RE.match(text, pos)
        if not m:
            raise ParseError(f"expected an attribute at {pos}: {text[pos:pos + 40]!r}")
        key = m.group(1)
        pos = m.end()
        value, pos = parse_value(text, pos)
        out[key] = value
    return out


def parse_tscn(path: Path) -> Scene:
    text = path.read_text(encoding="utf-8")
    headers = list(_HEADER_RE.finditer(text))
    ext_resources: dict[int, tuple[str, str]] = {}
    sub_resources: dict[int, tuple[str, dict[str, Any]]] = {}
    nodes: list[Node] = []

    for i, m in enumerate(headers):
        kind = m.group(1)
        attrs = _parse_header_attrs(m.group(2))
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[body_start:body_end]

        if kind == "ext_resource":
            rid = int(attrs["id"])
            ext_resources[rid] = (attrs["type"], attrs["path"])
        elif kind == "sub_resource":
            rid = int(attrs["id"])
            props = parse_properties(body)
            sub_resources[rid] = (attrs["type"], props)
        elif kind == "node":
            props = parse_properties(body)
            instance = None
            if "instance" in attrs:
                inst_val = attrs["instance"]
                assert isinstance(inst_val, Call) and inst_val.name == "ExtResource"
                instance = inst_val.args[0]
            nodes.append(
                Node(
                    name=attrs["name"],
                    type=attrs.get("type"),
                    parent=attrs.get("parent"),
                    instance=instance,
                    properties=props,
                )
            )
        # "gd_scene", "connection", "editable" headers carry nothing this
        # generator reads.
    return Scene(path=str(path), ext_resources=ext_resources, sub_resources=sub_resources, nodes=nodes)


# ---------------------------------------------------------------------
# `.gd` script headers: `extends`, `class_name`, and `export var` defaults.
# ---------------------------------------------------------------------


@dataclass
class ScriptInfo:
    path: Path
    extends: str | None  # a class_name, or a "res://..." path, or None (engine type)
    class_name: str | None
    # name -> (default value text, already run through parse_value)
    exports: dict[str, Any]
    # name -> True if the property has no default in this file (Godot then
    # uses the type's zero value, decided by the caller from the export hint).
    exports_no_default: set[str]
    # name -> the `export(...)` hint text, e.g. "PackedScene", "AirType".
    export_hints: dict[str, str]


_EXTENDS_RE = re.compile(r'^extends\s+("(?:[^"\\]|\\.)*"|[A-Za-z_][A-Za-z0-9_]*)', re.MULTILINE)
_CLASS_NAME_RE = re.compile(r"^class_name\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)
# `export var name = default`, `export(Hint) var name = default`,
# `export(Hint) var name: Type = default`, or no default at all. Only
# finds where a declaration starts; the default value itself (which
# may be a `{...}`/`[...]` literal spanning several lines, e.g.
# `ObjectState.gd`'s `host_commands`) is read by `parse_value` from
# here, not by this regex, so a multi-line default parses correctly.
_EXPORT_START_RE = re.compile(
    r"^export(?:\(([^)]*)\))?\s+var\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z_][A-Za-z0-9_]*\s*)?",
    re.MULTILINE,
)


def parse_gd_script(path: Path) -> ScriptInfo:
    text = path.read_text(encoding="utf-8")
    extends_m = _EXTENDS_RE.search(text)
    extends = None
    if extends_m:
        raw = extends_m.group(1)
        extends = raw[1:-1] if raw.startswith('"') else raw
    class_name_m = _CLASS_NAME_RE.search(text)
    class_name = class_name_m.group(1) if class_name_m else None

    exports: dict[str, Any] = {}
    exports_no_default: set[str] = set()
    export_hints: dict[str, str] = {}
    for m in _EXPORT_START_RE.finditer(text):
        hint, name = m.group(1), m.group(2)
        if hint:
            export_hints[name] = hint
        pos = m.end()
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and text[pos] == "=":
            value, _ = parse_value(text, pos + 1)
            exports[name] = value
        else:
            exports_no_default.add(name)

    return ScriptInfo(
        path=path,
        extends=extends,
        class_name=class_name,
        exports=exports,
        exports_no_default=exports_no_default,
        export_hints=export_hints,
    )
