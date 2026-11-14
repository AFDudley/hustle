#!/usr/bin/env python3
"""Reads the five official characters' `.tscn`/`.gd` files and writes:

  1. `port/hustle_core/src/tables/*.rs` -- the data tables themselves.
  2. a `table_dump` Rust source file that prints the same tables as JSON,
     for the probe comparison (`tools/tablegen/dump/rust/src/main.rs`).
  3. `tools/tablegen/manifest.json` -- which scenes and fields the probe
     (`tools/tablegen/TableGenProbe.gd`) must read from the real engine.

See `tools/tablegen/README.md` for the schema and the kept/dropped field
lists. See `schema.py` for the field manifests this module renders.

Usage: gen.py REPO_ROOT OUT_TABLES_DIR OUT_DUMP_MAIN OUT_MANIFEST
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import schema
from godot_scene import Call, EnumRef, Node, parse_tscn

REPO: Path

# ---------------------------------------------------------------------
# Scene loading and inheritance merge.
# ---------------------------------------------------------------------


def res_path(repo: Path, res: str) -> Path:
    assert res.startswith("res://"), res
    return repo / res[len("res://"):]


def resolve_texture_path(scene, key) -> str | None:
    """A `throw_positions` key, or a `SpriteFrames` animation frame: an
    `ExtResource(N)` (this scene's own id space), a
    `Resource("res://...")` literal, or `null` (a blank animation
    beat, which `throw_positions` -- always keyed by a real texture --
    can never match)."""
    if key is None:
        return None
    assert isinstance(key, Call), key
    if key.name == "ExtResource":
        return scene.ext_resources[key.args[0]][1]
    if key.name == "Resource":
        return key.args[0]
    raise ValueError(f"unexpected resource reference {key!r}")


def resolve_sprite_frames(scene, call: Call) -> dict[str, list[str]]:
    """A `Sprite.frames = SubResource(N)` reference, resolved to
    `{animation_name: [frame_texture_res_path, ...]}`, in this scene's
    own id space. A texture can repeat within one animation's frame
    list (a held pose, e.g. `stickman26.png` 4 times running); this
    keeps every repeat, in order, since `throw_positions` must find
    every (animation, frame index) a texture appears at."""
    assert call.name == "SubResource", call
    typ, props = scene.sub_resources[call.args[0]]
    assert typ == "SpriteFrames", (call, typ)
    out: dict[str, list[str]] = {}
    for anim in props.get("animations", []):
        out[anim["name"]] = [resolve_texture_path(scene, f) for f in anim["frames"]]
    return out


def resolve_ext_props(scene, node: Node) -> None:
    """Replaces an ExtResource-valued `script`/`projectile_scene` property
    with the resolved `(type, res://path)` tuple, a `SpriteFrames`-typed
    `frames` property with its resolved animation/frame table, and a
    `throw_positions` dict's texture keys with their resolved paths --
    all in this scene's own id space, so a later cross-scene merge
    never has to know which scene a raw id came from."""
    for key in ("script", "projectile_scene"):
        value = node.properties.get(key)
        if isinstance(value, Call) and value.name == "ExtResource":
            node.properties[key] = scene.ext_resources[value.args[0]]
    frames = node.properties.get("frames")
    if isinstance(frames, Call) and frames.name == "SubResource":
        typ, _props = scene.sub_resources.get(frames.args[0], (None, None))
        if typ == "SpriteFrames":
            node.properties["frames"] = resolve_sprite_frames(scene, frames)
    throw_positions = node.properties.get("throw_positions")
    if isinstance(throw_positions, dict) and throw_positions:
        resolved = {}
        for key, value in throw_positions.items():
            if not isinstance(key, Call):
                # `characters/swordandgun/states/UpwardSwipe.gd`'s
                # `UpwardSwipeAir` node sets `throw_positions = { 12:
                # "try_shoot" }` (checked). `12` is not a texture, and
                # the value is not an `{x, y}` position: this is
                # `ObjectState.host_commands` data (tick -> method
                # name) written into the wrong exported field by
                # mistake. `UpwardSwipe.gd` extends `CharacterState`
                # directly, not `ThrowState`, so `update_throw_position`
                # (the only reader of `throw_positions`) never runs for
                # this state either way. Dropping this one key is the
                # position it reaches: none.
                if node.name == "UpwardSwipeAir" and key == 12 and value == "try_shoot":
                    continue
                raise RuntimeError(f"{node.name}: throw_positions key {key!r} is not a texture reference")
            resolved[resolve_texture_path(scene, key)] = value
        node.properties["throw_positions"] = resolved



_scene_cache: dict[Path, dict[str, dict]] = {}


def load_merged_scene(path: Path) -> dict[str, dict]:
    """A scene's effective `node path -> merged properties` map, after
    resolving Godot's own scene-inheritance rule: a base scene's nodes,
    overlaid by this scene's own overrides and additions. The scene root
    itself is keyed `"."`."""
    if path in _scene_cache:
        return _scene_cache[path]
    scene = parse_tscn(path)
    for node in scene.nodes:
        resolve_ext_props(scene, node)

    root = scene.root()
    if root.instance is not None:
        base_res = scene.ext_resources[root.instance][1]
        base_path = res_path(REPO, base_res)
        merged = {k: dict(v) for k, v in load_merged_scene(base_path).items()}
    else:
        merged = {}

    for node in scene.nodes:
        key = "." if node is root else f"{node.parent}/{node.name}" if node.parent != "." else node.name
        merged.setdefault(key, {}).update(node.properties)

    _scene_cache[path] = merged
    return merged


# ---------------------------------------------------------------------
# Script class-chain classification.
# ---------------------------------------------------------------------

_registry: dict[str, Path] = {}
_chain_cache: dict = {}


def script_path_of(props: dict) -> Path | None:
    value = props.get("script")
    if value is None:
        return None
    _typ, res = value
    return res_path(REPO, res)


def class_chain(props: dict) -> schema.Chain | None:
    gd = script_path_of(props)
    if gd is None:
        return None
    return schema.resolve_chain(REPO, gd, _registry, _chain_cache)


def chain_has(chain: schema.Chain | None, *names: Path) -> bool:
    if chain is None:
        return False
    paths = {s.path.resolve() for s in chain.scripts}
    return any(n.resolve() in paths for n in names)


PATH_OBJECT_STATE: Path
PATH_CHARACTER_STATE: Path
PATH_SUPERMOVE: Path
PATH_THROWSTATE: Path
PATH_GRAB: Path
PATH_ROBOTSTATE: Path
PATH_BEASTSTATE: Path
PATH_WIZARDSTATE: Path
PATH_COLLISIONBOX: Path
PATH_HITBOX: Path
PATH_THROWBOX: Path
PATH_SWEPTHITBOX: Path
PATH_BASEOBJ: Path
PATH_FIGHTER: Path
PATH_BASEPROJECTILE: Path


def setup_paths() -> None:
    global PATH_OBJECT_STATE, PATH_CHARACTER_STATE, PATH_SUPERMOVE, PATH_THROWSTATE, PATH_GRAB
    global PATH_ROBOTSTATE, PATH_BEASTSTATE, PATH_WIZARDSTATE, PATH_COLLISIONBOX, PATH_HITBOX
    global PATH_THROWBOX, PATH_SWEPTHITBOX, PATH_BASEOBJ, PATH_FIGHTER, PATH_BASEPROJECTILE
    PATH_OBJECT_STATE = REPO / "ObjectState.gd"
    PATH_CHARACTER_STATE = REPO / "characters/states/CharState.gd"
    PATH_SUPERMOVE = REPO / "characters/states/SuperMove.gd"
    PATH_THROWSTATE = REPO / "characters/states/ThrowState.gd"
    PATH_GRAB = REPO / "characters/states/Grab.gd"
    PATH_ROBOTSTATE = REPO / "characters/robo/RobotState.gd"
    PATH_BEASTSTATE = REPO / "characters/mutant/states/BeastState.gd"
    PATH_WIZARDSTATE = REPO / "characters/wizard/states/WizardState.gd"
    PATH_COLLISIONBOX = REPO / "addons/collision_box/CollisionBox.gd"
    PATH_HITBOX = REPO / "mechanics/Hitbox.gd"
    PATH_THROWBOX = REPO / "characters/ThrowBox.gd"
    PATH_SWEPTHITBOX = REPO / "mechanics/SweptHitbox.gd"
    PATH_BASEOBJ = REPO / "obj/BaseObj.gd"
    PATH_FIGHTER = REPO / "characters/BaseChar.gd"
    PATH_BASEPROJECTILE = REPO / "projectile/BaseProjectile.gd"
    EXTRA_MIXIN_FOR_CHARACTER["robot"] = (PATH_ROBOTSTATE, schema.ROBOTSTATE_FIELDS, "robot")
    EXTRA_MIXIN_FOR_CHARACTER["mutant"] = (PATH_BEASTSTATE, schema.BEASTSTATE_FIELDS, "beast")
    EXTRA_MIXIN_FOR_CHARACTER["wizard"] = (PATH_WIZARDSTATE, schema.WIZARDSTATE_FIELDS, "wizard")


# ---------------------------------------------------------------------
# Field value resolution: tscn override, else the class chain's own
# default, else the field's canonical (union) default.
# ---------------------------------------------------------------------


def field_raw(props: dict, defaults: dict, name: str, canonical):
    if name in props:
        return props[name]
    if name in defaults:
        return defaults[name]
    return canonical


_FIXED_CACHE: dict[str, int | None] = {}
_FIXED_INVALID = "INVALID"


def fixed_raw(value: str) -> int | None:
    return _FIXED_CACHE[value]


def collect_fixed_strings(values: set[str]) -> None:
    if not values:
        return
    todo = sorted(v for v in values if v not in _FIXED_CACHE)
    if not todo:
        return
    result = subprocess.run(
        ["fixed_parse"], input="\n".join(todo) + "\n", capture_output=True, text=True
    )
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        result.check_returncode()
    out = result.stdout.splitlines()
    assert len(out) == len(todo), (len(out), len(todo))
    for v, raw in zip(todo, out):
        _FIXED_CACHE[v] = None if raw == _FIXED_INVALID else int(raw)


# ---------------------------------------------------------------------
# Rust identifier helpers.
# ---------------------------------------------------------------------

RENAME_FIELD = {"type": "action_type"}


def rust_field(name: str) -> str:
    return RENAME_FIELD.get(name, name)


def rust_variant(name: str) -> str:
    if name[0].isdigit():
        name = "N" + name
    return name


def rust_mod_name(stem: str) -> str:
    out = []
    for i, c in enumerate(stem):
        if c.isupper() and i > 0 and not stem[i - 1].isupper():
            out.append("_")
        out.append(c.lower())
    return "".join(out).replace("__", "_")


def rust_str_enum(prefix: str, token: str) -> str:
    # A raw category/stance/tag token becomes a PascalCase Rust variant.
    parts = [p for p in token.replace("-", "_").split("_") if p]
    name = "".join(p[0:1].upper() + p[1:] for p in parts) if parts else token
    return rust_variant(name)


def split_lines(s: str) -> list[str]:
    return [line.strip() for line in s.split("\n") if line.strip()]


CHARACTERS = {
    "base": "characters/BaseChar.tscn",
    "ninja": "characters/stickman/NinjaGuy.tscn",
    "cowboy": "characters/swordandgun/SwordGuy.tscn",
    "wizard": "characters/wizard/Wizard.tscn",
    "robot": "characters/robo/Robot.tscn",
    "mutant": "characters/mutant/Mutant.tscn",
}

EXTRA_MIXIN_FOR_CHARACTER: dict[str, tuple[Path, list, str]] = {}


class StateEntry:
    def __init__(self, name):
        self.name = name
        self.fields: dict[str, object] = {}
        self.hitboxes: list[tuple[str, dict[str, object]]] = []  # (kind, fields)


class Scope:
    def __init__(self, key, kind, tscn_path):
        self.key = key
        self.kind = kind  # "character" | "projectile"
        self.tscn_path = tscn_path
        self.states: list[StateEntry] = []
        self.root_fields: dict[str, object] = {}


def mixin_defaults(path: Path) -> dict:
    return schema.resolve_chain(REPO, path, _registry, _chain_cache).merged_defaults()


def state_names_of(merged: dict) -> list[str]:
    names = []
    for path in merged:
        if path.startswith("StateMachine/") and path.count("/") == 1:
            names.append(path[len("StateMachine/"):])
    return sorted(names)


def hitbox_children_of(merged: dict, state_name: str) -> list[str]:
    prefix = f"StateMachine/{state_name}/"
    return sorted(
        p for p in merged if p.startswith(prefix) and p.count("/") == 2
    )


def resolve_throw_positions(merged: dict, label: str, state_name: str, raw) -> list[tuple[str, int, int, int]]:
    """`throw_positions` keys by the sprite texture resource that is
    showing when the position applies (`characters/ThrowState.gd`'s
    `update_throw_position`: `host.get_current_sprite_frame()` reads
    `host.sprite.frame`'s texture, not a tick number). Resolves every
    texture key to every `(animation, frame index)` the character's
    `SpriteFrames` resource shows it at -- a texture can repeat across
    frames, e.g. a held pose -- and fails loudly, naming the texture
    and the state, if a key matches none: a key with no frame would be
    silently unreachable data, not a value this generator may guess
    at."""
    if not raw:
        return []
    sprite_props = merged.get("Flip/Sprite")
    frames_map = sprite_props.get("frames") if sprite_props else None
    if not isinstance(frames_map, dict):
        raise RuntimeError(f"{label}: no resolved SpriteFrames at Flip/Sprite for state {state_name!r}")
    out: list[tuple[str, int, int, int]] = []
    for respath, pos in raw.items():
        matches = [
            (anim_name, idx)
            for anim_name, textures in frames_map.items()
            for idx, tex in enumerate(textures)
            if tex == respath
        ]
        if not matches:
            # `characters/stickman/NinjaGuy.tscn`'s `HitGrabFollowup`
            # keys a position by `hitgrab_followup6.png`, a real
            # texture file, but the `HitGrabFollowup` animation's own
            # frame list (`"frames": [ ExtResource(221), ...
            # ExtResource(226) ]`, checked) never shows it: five
            # textures cycle, not six. The engine's sprite frame can
            # never become this texture, so `update_throw_position`'s
            # `if frame in throw_positions:` can never match this key
            # either; omitting the entry is the position this key
            # reaches, not a guess at one.
            if label.endswith("NinjaGuy.tscn") and state_name == "HitGrabFollowup" and respath.endswith(
                "hitgrab_followup6.png"
            ):
                continue
            raise RuntimeError(
                f"{label}: throw_positions texture {respath!r} on state {state_name!r} matches no SpriteFrames frame"
            )
        for anim_name, idx in matches:
            out.append((anim_name, idx, int(pos["x"]), int(pos["y"])))
    return out

def extract_fields(props: dict, defaults: dict, field_list: list[tuple[str, str]], fallback: dict) -> dict:
    out = {}
    for name, tag in field_list:
        if name in props:
            raw = props[name]
        elif name in defaults:
            raw = defaults[name]
        else:
            raw = fallback[name]
        if tag == "fixed" and isinstance(raw, (int, float)):
            # A modder's `.tscn` can write an unquoted number for a
            # `String`-typed export (`characters/BaseChar.tscn`'s
            # `dir_x = -1`, found by scanning every `.tscn` for this).
            # Godot's loader coerces it to the declared type at load
            # time; match that by stringifying it the same way an int
            # converts to `String` in GDScript: plain digits, no
            # trailing `.0`.
            raw = str(int(raw))
        # Two upstream data anomalies, found by scanning every `.tscn`
        # for a "fixed" field whose text is not a plain decimal:
        # `characters/wizard/Wizard.tscn`'s `ConfusingTouch2` sets both
        # its hitboxes' `dir_y = ""`, and `characters/robo/Robot.tscn`'s
        # `JumpGrab` `ThrowBox` sets `block_pushback_modifier =
        # "False"`. Neither is substituted: `raw` passes through
        # unchanged to `fixed_parse`, which reports it `INVALID` the
        # same way the game's own `FixedMath` would reject it, and
        # `emit_value`'s "fixed" case stores that as `None`
        # (`Option<Fixed>`), not a guessed number.
        out[name] = (raw, tag)
    return out


HITBOX_EXTRA_FALLBACK = {"throw_state": "", "to_x": 0, "to_y": 0}


def extract_hitbox(hprops: dict, hchain) -> tuple[str, dict]:
    hdefaults = hchain.merged_defaults()
    fields = extract_fields(hprops, hdefaults, schema.HITBOX_FIELDS, {})
    kind = "Hitbox"
    if chain_has(hchain, PATH_THROWBOX):
        kind = "ThrowBox"
    elif chain_has(hchain, PATH_SWEPTHITBOX):
        kind = "SweptHitbox"
    fields.update(extract_fields(hprops, hdefaults, schema.THROWBOX_FIELDS, HITBOX_EXTRA_FALLBACK))
    fields.update(extract_fields(hprops, hdefaults, schema.SWEPTHITBOX_FIELDS, HITBOX_EXTRA_FALLBACK))
    return kind, fields


def build_character_scope(key: str, rel_path: str) -> tuple[Scope, list[str]]:
    """Returns the scope plus every `res://...` projectile scene path its
    states reference, for the caller's BFS queue."""
    tscn_path = REPO / rel_path
    merged = load_merged_scene(tscn_path)
    scope = Scope(key, "character", tscn_path)

    root_props = merged["."]
    root_chain = class_chain(root_props)
    root_defaults = root_chain.merged_defaults() if root_chain else {}
    scope.root_fields = extract_fields(root_props, root_defaults, schema.FIGHTER_ROOT_FIELDS, {})

    extra_mixin = EXTRA_MIXIN_FOR_CHARACTER.get(key)
    base_fallback = {
        **mixin_defaults(PATH_SUPERMOVE),
        **mixin_defaults(PATH_THROWSTATE),
        **mixin_defaults(PATH_GRAB),
    }
    if extra_mixin:
        base_fallback.update(mixin_defaults(extra_mixin[0]))

    projectile_refs: list[str] = []
    for name in state_names_of(merged):
        props = merged[f"StateMachine/{name}"]
        chain = class_chain(props)
        if chain is None or not chain_has(chain, PATH_OBJECT_STATE):
            raise RuntimeError(f"{rel_path}: state {name!r} has no ObjectState-derived script")
        defaults = chain.merged_defaults()
        entry = StateEntry(name)
        entry.fields.update(extract_fields(props, defaults, schema.OBJECT_STATE_FIELDS, {}))
        tp_raw = props.get("throw_positions", defaults.get("throw_positions", {}))
        entry.fields["throw_positions"] = (
            resolve_throw_positions(merged, rel_path, name, tp_raw), "throw_positions"
        )
        if not chain_has(chain, PATH_CHARACTER_STATE):
            raise RuntimeError(f"{rel_path}: state {name!r} is not CharacterState-derived")
        entry.fields.update(extract_fields(props, defaults, schema.CHARACTER_STATE_FIELDS, {}))
        entry.fields.update(extract_fields(props, defaults, schema.SUPERMOVE_FIELDS, base_fallback))
        entry.fields.update(extract_fields(props, defaults, schema.THROWSTATE_FIELDS, base_fallback))
        entry.fields.update(extract_fields(props, defaults, schema.GRAB_FIELDS, base_fallback))
        if extra_mixin:
            entry.fields.update(extract_fields(props, defaults, extra_mixin[1], base_fallback))

        ext = props.get("projectile_scene") or defaults.get("projectile_scene")
        if ext:
            projectile_refs.append(ext[1])

        for child_path in hitbox_children_of(merged, name):
            hprops = merged[child_path]
            hchain = class_chain(hprops)
            if not chain_has(hchain, PATH_COLLISIONBOX) or not chain_has(hchain, PATH_HITBOX):
                continue
            entry.hitboxes.append(extract_hitbox(hprops, hchain))
        scope.states.append(entry)
    return scope, projectile_refs


def build_projectile_scope(key: str, tscn_path: Path) -> tuple[Scope, list[str]]:
    merged = load_merged_scene(tscn_path)
    scope = Scope(key, "projectile", tscn_path)

    root_props = merged["."]
    root_chain = class_chain(root_props)
    root_defaults = root_chain.merged_defaults() if root_chain else {}
    scope.root_fields = extract_fields(root_props, root_defaults, schema.PROJECTILE_ROOT_FIELDS, {})

    projectile_refs: list[str] = []
    for name in state_names_of(merged):
        props = merged[f"StateMachine/{name}"]
        chain = class_chain(props)
        if chain is None or not chain_has(chain, PATH_OBJECT_STATE):
            raise RuntimeError(f"{tscn_path}: state {name!r} has no ObjectState-derived script")
        defaults = chain.merged_defaults()
        entry = StateEntry(name)
        entry.fields.update(extract_fields(props, defaults, schema.OBJECT_STATE_FIELDS, {}))
        tp_raw = props.get("throw_positions", defaults.get("throw_positions", {}))
        entry.fields["throw_positions"] = (
            resolve_throw_positions(merged, str(tscn_path), name, tp_raw), "throw_positions"
        )
        ext = props.get("projectile_scene") or defaults.get("projectile_scene")
        if ext:
            projectile_refs.append(ext[1])
        for child_path in hitbox_children_of(merged, name):
            hprops = merged[child_path]
            hchain = class_chain(hprops)
            if not chain_has(hchain, PATH_COLLISIONBOX) or not chain_has(hchain, PATH_HITBOX):
                continue
            entry.hitboxes.append(extract_hitbox(hprops, hchain))
        scope.states.append(entry)
    return scope, projectile_refs


# ---------------------------------------------------------------------
# Dynamic token discovery: every distinct cancel category, stance, misc
# tag, and orb-trigger value actually written in the five characters'
# own data becomes one enum variant. This is a scan of the real data,
# not a guessed list, so a token this generator has never seen cannot
# be silently missing.
# ---------------------------------------------------------------------


def collect_tokens(scopes: dict[str, Scope]):
    cancel_tokens: set[str] = set()
    stance_tokens: set[str] = set()
    misc_tokens: set[str] = set()
    orb_tokens: set[str] = set()
    fixed_strings: set[str] = set()
    throw_anim_tokens: set[str] = set()

    def visit(name, raw, tag):
        if tag.startswith("categories:"):
            cancel_tokens.update(split_lines(raw))
        elif tag == "stances":
            stance_tokens.update(split_lines(raw))
        elif tag == "stance_self_opt":
            if raw:
                stance_tokens.add(raw)
        elif tag == "misc_tag":
            if raw:
                misc_tokens.add(raw)
        elif tag == "orb_trigger":
            if raw:
                orb_tokens.add(raw)
        elif tag == "fixed":
            fixed_strings.add(raw)
        elif tag == "throw_positions":
            throw_anim_tokens.update(anim for anim, _frame, _x, _y in raw)

    for scope in scopes.values():
        for entry in scope.states:
            for name, (raw, tag) in entry.fields.items():
                visit(name, raw, tag)
            for _kind, hfields in entry.hitboxes:
                for name, (raw, tag) in hfields.items():
                    visit(name, raw, tag)
        for name, (raw, tag) in scope.root_fields.items():
            visit(name, raw, tag)
    return cancel_tokens, stance_tokens, misc_tokens, orb_tokens, fixed_strings, throw_anim_tokens


# ---------------------------------------------------------------------
# Rust value/type emission.
# ---------------------------------------------------------------------


def rust_type(tag: str, is_base: bool = False) -> str:
    if tag.startswith("enum:"):
        name = tag.split(":", 1)[1]
        return "BusyInterrupt" if name == "BusyInterrupt" else name
    if tag.startswith("categories:"):
        return "&'static [CancelCategory]"
    return {
        "bool": "bool",
        "int": "i32",
        "fixed": "Option<Fixed>",
        "state_self_req": "State",
        "state_self_opt": "Option<State>",
        "state_base_req": "State" if is_base else "base::State",
        "stance_self_opt": "Option<Stance>",
        "stances": "&'static [Stance]",
        "frame_list": "&'static [i32]",
        "throw_positions": "&'static [ThrowPositionEntry]",
        "misc_tag": "Option<MiscDataTag>",
        "orb_trigger": "Option<OrbTriggerType>",
        "projectile_kind": "Option<ProjectileKind>",
    }[tag]


def emit_value(tag: str, raw, scope: Scope, state_names: set[str], name: str = "") -> str:
    if tag == "bool":
        return "true" if raw else "false"
    if tag == "int":
        return str(int(raw))
    if tag == "fixed":
        n = fixed_raw(raw)
        # `None` here is one of the two upstream data anomalies
        # `extract_fields` documents and passes through unchanged
        # (a hitbox field holding `""` or `"False"` instead of a
        # decimal); `fixed_parse` reports those `INVALID`, so this
        # stores exactly what the engine itself holds: a `Fixed` the
        # value cannot become, not a guessed number.
        return "None" if n is None else f"Some(Fixed({n}))"
    if tag.startswith("enum:"):
        enum_name = tag.split(":", 1)[1]
        value = schema.resolve_enum_ref(raw) if isinstance(raw, EnumRef) else raw
        try:
            member = next(k for k, v in schema.GODOT_ENUMS[enum_name].items() if v == value)
        except StopIteration:
            raise ValueError(f"{scope.key}: no {enum_name} member for raw value {value!r}") from None
        rust_enum = "BusyInterrupt" if enum_name == "BusyInterrupt" else enum_name
        variant = "NoneKind" if rust_enum == "BusyInterrupt" and member == "None" else member
        return f"{rust_enum}::{variant}"
    if tag == "state_self_req":
        assert raw in state_names, (scope.key, "state_self_req", raw)
        return f"State::{rust_variant(raw)}"
    if tag == "state_self_opt":
        if not raw:
            return "None"
        if raw not in state_names:
            # `ObjectState.gd`'s `fallback_state` defaults to `"Wait"`
            # unconditionally for every state, character or
            # projectile. A projectile's own state machine never has a
            # `Wait` state (only a character does), so the name this
            # inherited default names cannot resolve in a projectile
            # scope; `process_feint()`, its only reader, is a
            # character-feint mechanic a projectile never calls either
            # way. `None` here is that: not a guess, the inherited
            # default naming a state this scope does not have.
            assert name == "fallback_state", (scope.key, "state_self_opt", name, raw)
            return "None"
        return f"Some(State::{rust_variant(raw)})"
    if tag == "state_base_req":
        assert raw in BASE_STATE_NAMES, ("state_base_req", raw)
        prefix = "State" if scope.key == "base" else "base::State"
        return f"{prefix}::{rust_variant(raw)}"
    if tag == "stance_self_opt":
        if not raw:
            return "None"
        return f"Some(Stance::{rust_str_enum('Stance', raw)})"
    if tag == "stances":
        items = ", ".join(f"Stance::{rust_str_enum('Stance', t)}" for t in split_lines(raw))
        return f"&[{items}]"
    if tag.startswith("categories:"):
        items = ", ".join(f"CancelCategory::{rust_str_enum('CancelCategory', t)}" for t in split_lines(raw))
        return f"&[{items}]"
    if tag == "frame_list":
        return "&[" + ", ".join(str(int(v)) for v in raw) + "]"
    if tag == "throw_positions":
        parts = []
        for anim_name, frame, x, y in raw:
            variant = rust_str_enum("ThrowAnimation", anim_name)
            parts.append(
                f"ThrowPositionEntry {{ animation: ThrowAnimation::{variant}, frame: {frame}, x: {x}, y: {y} }}"
            )
        return "&[" + ", ".join(parts) + "]"
    if tag == "misc_tag":
        if not raw:
            return "None"
        return f"Some(MiscDataTag::{rust_str_enum('MiscDataTag', raw)})"
    if tag == "orb_trigger":
        if not raw:
            return "None"
        return f"Some(OrbTriggerType::{rust_str_enum('OrbTriggerType', raw)})"
    if tag == "projectile_kind":
        if not raw:
            return "None"
        _typ, respath = raw
        key = PROJECTILE_KEY_BY_RESPATH[respath]
        variant = PROJECTILE_VARIANT_BY_RESPATH[key]
        return f"Some(ProjectileKind::{variant})"
    raise ValueError(f"unhandled tag {tag!r}")


BASE_STATE_NAMES: set[str] = set()
PROJECTILE_KEY_BY_RESPATH: dict[str, str] = {}
PROJECTILE_VARIANT_BY_RESPATH: dict[str, str] = {}

HITBOX_RECORD_FIELDS = list(schema.HITBOX_FIELDS) + list(schema.THROWBOX_FIELDS) + list(schema.SWEPTHITBOX_FIELDS)

HEADER = (
    "//! Generated by tools/tablegen/gen.py. Do not hand-edit.\n"
    "//! See tools/tablegen/README.md for the schema and the kept/dropped\n"
    "//! field lists, and docs/labbook/2026-11-13-core-game-scope-five-characters.md\n"
    "//! decision 5 for the Rust subset this file follows.\n"
)


def scope_field_list(scope: Scope) -> list[tuple[str, str]]:
    if scope.kind != "character":
        return list(schema.OBJECT_STATE_FIELDS)
    fields = (
        list(schema.OBJECT_STATE_FIELDS)
        + list(schema.CHARACTER_STATE_FIELDS)
        + list(schema.SUPERMOVE_FIELDS)
        + list(schema.THROWSTATE_FIELDS)
        + list(schema.GRAB_FIELDS)
    )
    extra = EXTRA_MIXIN_FOR_CHARACTER.get(scope.key)
    if extra:
        fields += extra[1]
    return fields


def emit_scope_rs(scope: Scope) -> str:
    state_names = {e.name for e in scope.states}
    field_list = scope_field_list(scope)
    base_import = "" if scope.key == "base" else "use crate::tables::base;"
    out = [HEADER, "", "use crate::fixed::Fixed;", "use crate::tables::common::*;", base_import, ""]

    out.append("#[derive(Clone, Copy, PartialEq, Eq, Debug)]")
    out.append("pub enum State {")
    for e in scope.states:
        out.append(f"    {rust_variant(e.name)},")
    out.append("}")
    out.append("")
    out.append(f"pub const ALL_STATES: [State; {len(scope.states)}] = [")
    for e in scope.states:
        out.append(f"    State::{rust_variant(e.name)},")
    out.append("];")
    out.append("")
    out.append("#[derive(Clone, Copy, Debug)]")
    out.append("pub struct HitboxRecord {")
    out.append("    pub kind: HitboxKind,")
    for name, tag in HITBOX_RECORD_FIELDS:
        out.append(f"    pub {rust_field(name)}: {rust_type(tag, scope.key == 'base')},")
    out.append("}")
    out.append("")
    out.append("#[derive(Clone, Copy, Debug)]")
    out.append("pub struct StateData {")
    for name, tag in field_list:
        out.append(f"    pub {rust_field(name)}: {rust_type(tag, scope.key == 'base')},")
    out.append("    pub hitboxes: &'static [HitboxRecord],")
    out.append("}")
    out.append("")
    out.append("pub fn state_data(s: State) -> StateData {")
    out.append("    match s {")
    for e in scope.states:
        out.append(f"        State::{rust_variant(e.name)} => StateData {{")
        for name, tag in field_list:
            raw, _ = e.fields[name]
            out.append(f"            {rust_field(name)}: {emit_value(tag, raw, scope, state_names, name)},")
        if e.hitboxes:
            out.append(f"            hitboxes: &HITBOXES_{rust_variant(e.name).upper()},")
        else:
            out.append("            hitboxes: &[],")
        out.append("        },")
    out.append("    }")
    out.append("}")
    out.append("")
    for e in scope.states:
        if not e.hitboxes:
            continue
        const_name = f"HITBOXES_{rust_variant(e.name).upper()}"
        out.append(f"const {const_name}: [HitboxRecord; {len(e.hitboxes)}] = [")
        for kind, hfields in e.hitboxes:
            out.append("    HitboxRecord {")
            out.append(f"        kind: HitboxKind::{kind},")
            for name, tag in HITBOX_RECORD_FIELDS:
                raw, _ = hfields[name]
                out.append(f"        {rust_field(name)}: {emit_value(tag, raw, scope, state_names, name)},")
            out.append("    },")
        out.append("];")
        out.append("")

    root_list = schema.FIGHTER_ROOT_FIELDS if scope.kind == "character" else schema.PROJECTILE_ROOT_FIELDS
    root_type = "FighterRootConstants" if scope.kind == "character" else "ProjectileRootConstants"
    out.append(f"pub const ROOT: {root_type} = {root_type} {{")
    for name, tag in root_list:
        raw, _ = scope.root_fields[name]
        out.append(f"    {rust_field(name)}: {emit_value(tag, raw, scope, state_names, name)},")
    out.append("};")
    out.append("")
    return "\n".join(out)


def emit_common_rs(cancel_order, stance_order, misc_order, orb_order, projectile_order, throw_anim_order) -> str:
    out = [HEADER, "", "#![allow(dead_code)]", "", "use crate::fixed::Fixed;", ""]

    def emit_enum(name, variants):
        out.append("#[derive(Clone, Copy, PartialEq, Eq, Debug)]")
        out.append(f"pub enum {name} {{")
        for v in variants:
            out.append(f"    {v},")
        out.append("}")
        out.append("")

    emit_enum("ActionType", ["Movement", "Attack", "Special", "Super", "Defense", "Hurt"])
    emit_enum("AirType", ["Grounded", "Aerial", "Both"])
    emit_enum("BusyInterrupt", ["Normal", "Hurt", "NoneKind"])
    emit_enum("HitboxType", ["Normal", "Flip", "ThrowHit", "OffensiveBurst", "Burst", "NoHitstun", "Detect"])
    emit_enum("HitHeight", ["High", "Mid", "Low"])
    emit_enum("HitboxKind", ["Hitbox", "ThrowBox", "SweptHitbox"])

    cancel_variants = [rust_str_enum("CancelCategory", t) for t in cancel_order]
    assert len(set(cancel_variants)) == len(cancel_variants), cancel_variants
    emit_enum("CancelCategory", cancel_variants)

    stance_variants = [rust_str_enum("Stance", t) for t in stance_order]
    assert len(set(stance_variants)) == len(stance_variants), stance_variants
    emit_enum("Stance", stance_variants)

    misc_variants = [rust_str_enum("MiscDataTag", t) for t in misc_order]
    assert len(set(misc_variants)) == len(misc_variants), misc_variants
    emit_enum("MiscDataTag", misc_variants)

    orb_variants = [rust_str_enum("OrbTriggerType", t) for t in orb_order]
    assert len(set(orb_variants)) == len(orb_variants), orb_variants
    emit_enum("OrbTriggerType", orb_variants)

    emit_enum("ProjectileKind", [v for _k, v in projectile_order])

    throw_anim_variants = [rust_str_enum("ThrowAnimation", t) for t in throw_anim_order]
    assert len(set(throw_anim_variants)) == len(throw_anim_variants), throw_anim_variants
    emit_enum("ThrowAnimation", throw_anim_variants)

    out.append("#[derive(Clone, Copy, Debug)]")
    out.append("pub struct ThrowPositionEntry {")
    out.append("    pub animation: ThrowAnimation,")
    out.append("    pub frame: i32,")
    out.append("    pub x: i32,")
    out.append("    pub y: i32,")
    out.append("}")
    out.append("")
    out.append("/// `Fighter.MAX_HEALTH` (`characters/BaseChar.gd`): a plain `var`, not an")
    out.append("/// exported field, so no `.tscn` can change it per character. Checked:")
    out.append("/// none of the five characters' own root scripts assigns `MAX_HEALTH`.")
    out.append("pub const MAX_HEALTH: i32 = 1500;")
    out.append("")
    out.append("#[derive(Clone, Copy, Debug)]")
    out.append("pub struct FighterRootConstants {")
    for name, tag in schema.FIGHTER_ROOT_FIELDS:
        out.append(f"    pub {rust_field(name)}: {rust_type(tag)},")
    out.append("}")
    out.append("")
    out.append("#[derive(Clone, Copy, Debug)]")
    out.append("pub struct ProjectileRootConstants {")
    for name, tag in schema.PROJECTILE_ROOT_FIELDS:
        out.append(f"    pub {rust_field(name)}: {rust_type(tag)},")
    out.append("}")
    out.append("")
    return "\n".join(out)


def emit_tables_mod(character_keys: list[str], projectile_keys: list[str]) -> str:
    out = [HEADER, "", "pub mod common;"]
    for k in character_keys:
        out.append(f"pub mod {k};")
    out.append("pub mod projectiles {")
    for k in projectile_keys:
        out.append(f"    pub mod {k};")
    out.append("}")
    out.append("")
    return "\n".join(out)


def dump_field_expr(tag: str, accessor: str, owner_name_fn: str) -> str:
    if tag in ("bool", "int"):
        return accessor
    if tag == "fixed":
        return f"{accessor}.map(|v| v.0)"
    if tag.startswith("enum:"):
        return f"({accessor} as i32)"
    if tag == "state_self_req":
        return f"{owner_name_fn}({accessor})"
    if tag == "state_self_opt":
        return f"{accessor}.map({owner_name_fn})"
    if tag == "state_base_req":
        return f"name_of_base({accessor})"
    if tag == "stance_self_opt":
        return f"{accessor}.map(token_of_stance)"
    if tag == "stances":
        return f"{accessor}.iter().map(|v| token_of_stance(*v)).collect::<Vec<_>>()"
    if tag.startswith("categories:"):
        return f"{accessor}.iter().map(|v| token_of_category(*v)).collect::<Vec<_>>()"
    if tag == "frame_list":
        return f"{accessor}.to_vec()"
    if tag == "throw_positions":
        return (
            f"{accessor}.iter().map(|p| serde_json::json!("
            '{"animation": token_of_throw_animation(p.animation), "frame": p.frame, "x": p.x, "y": p.y}'
            ")).collect::<Vec<_>>()"
        )
    if tag == "misc_tag":
        return f"{accessor}.map(token_of_misc)"
    if tag == "orb_trigger":
        return f"{accessor}.map(token_of_orb)"
    if tag == "projectile_kind":
        return f"{accessor}.map(token_of_projectile)"
    raise ValueError(f"unhandled dump tag {tag!r}")


def emit_dump_main(
    scopes: dict[str, Scope],
    character_keys: list[str],
    projectile_keys: list[str],
    cancel_order: list[str],
    stance_order: list[str],
    misc_order: list[str],
    orb_order: list[str],
    projectile_order: list[tuple[str, str]],
    throw_anim_order: list[str],
) -> str:
    out = [
        "// Generated by tools/tablegen/gen.py. Do not hand-edit.",
        "// Prints every field tools/tablegen keeps, for every state, hitbox and",
        "// root record of the five characters and their projectiles, as JSON",
        "// Lines. tools/tablegen/compare.py checks this against the engine's own",
        "// values (tools/tablegen/TableGenProbe.gd). See tools/tablegen/README.md.",
        "use hustle_core::tables::common;",
        "use hustle_core::tables::*;",
        "",
    ]

    def name_array(const_name: str, fn_name: str, enum_ty: str, names: list[str]):
        quoted = ", ".join(json.dumps(n) for n in names)
        out.append(f"const {const_name}: [&str; {len(names)}] = [{quoted}];")
        out.append(f"fn {fn_name}(v: {enum_ty}) -> &'static str {{ {const_name}[v as usize] }}")
        out.append("")

    name_array("CANCEL_CATEGORY_NAMES", "token_of_category", "common::CancelCategory", cancel_order)
    name_array("STANCE_NAMES", "token_of_stance", "common::Stance", stance_order)
    name_array("MISC_DATA_TAG_NAMES", "token_of_misc", "common::MiscDataTag", misc_order)
    name_array("ORB_TRIGGER_TYPE_NAMES", "token_of_orb", "common::OrbTriggerType", orb_order)
    name_array(
        "PROJECTILE_KIND_PATHS", "token_of_projectile", "common::ProjectileKind",
        [respath for respath, _variant in projectile_order],
    )
    name_array("THROW_ANIMATION_NAMES", "token_of_throw_animation", "common::ThrowAnimation", throw_anim_order)

    all_keys = [("base", "base")] + [(k, k) for k in character_keys if k != "base"] + [
        (k, f"projectiles::{k}") for k in projectile_keys
    ]
    for key, modpath in all_keys:
        scope = scopes[key]
        names = [e.name for e in scope.states]
        name_array(f"NAMES_{key.upper()}", f"name_of_{key}", f"{modpath}::State", names)

    for key, modpath in all_keys:
        scope = scopes[key]
        field_list = scope_field_list(scope)
        name_fn = f"name_of_{key}"
        out.append(f"fn dump_{key}(out: &mut Vec<serde_json::Value>) {{")
        out.append(f"    for s in {modpath}::ALL_STATES.iter().copied() {{")
        out.append(f"        let d = {modpath}::state_data(s);")
        out.append("        out.push(serde_json::json!({")
        out.append(f'            "scope": {json.dumps(key)},')
        out.append('            "record": "state",')
        out.append(f'            "state": {name_fn}(s),')
        for name, tag in field_list:
            expr = dump_field_expr(tag, f"d.{rust_field(name)}", name_fn)
            out.append(f'            {json.dumps(name)}: {expr},')
        out.append("        }));")
        out.append("        for (i, h) in d.hitboxes.iter().enumerate() {")
        out.append("            out.push(serde_json::json!({")
        out.append(f'                "scope": {json.dumps(key)},')
        out.append('                "record": "hitbox",')
        out.append(f'                "state": {name_fn}(s),')
        out.append('                "hitbox_index": i,')
        for name, tag in HITBOX_RECORD_FIELDS:
            expr = dump_field_expr(tag, f"h.{rust_field(name)}", name_fn)
            out.append(f'                {json.dumps(name)}: {expr},')
        out.append("            }));")
        out.append("        }")
        out.append("    }")
        root_list = schema.FIGHTER_ROOT_FIELDS if scope.kind == "character" else schema.PROJECTILE_ROOT_FIELDS
        out.append("    out.push(serde_json::json!({")
        out.append(f'        "scope": {json.dumps(key)},')
        out.append('        "record": "root",')
        for name, tag in root_list:
            expr = dump_field_expr(tag, f"{modpath}::ROOT.{rust_field(name)}", name_fn)
            out.append(f'        {json.dumps(name)}: {expr},')
        out.append("    }));")
        out.append("}")
        out.append("")

    out.append("fn main() {")
    out.append("    let mut out: Vec<serde_json::Value> = Vec::new();")
    for key, _modpath in all_keys:
        out.append(f"    dump_{key}(&mut out);")
    out.append("    for v in out {")
    out.append('        println!("{}", v);')
    out.append("    }")
    out.append("}")
    out.append("")
    return "\n".join(out)


def build_manifest(scopes: dict[str, Scope], character_keys, projectile_keys) -> dict:
    state_field_names = set()
    for fields in (
        schema.OBJECT_STATE_FIELDS,
        schema.CHARACTER_STATE_FIELDS,
        schema.SUPERMOVE_FIELDS,
        schema.THROWSTATE_FIELDS,
        schema.GRAB_FIELDS,
        schema.ROBOTSTATE_FIELDS,
        schema.BEASTSTATE_FIELDS,
        schema.WIZARDSTATE_FIELDS,
    ):
        state_field_names.update(name for name, _tag in fields)
    hitbox_field_names = {name for name, _tag in HITBOX_RECORD_FIELDS}
    root_field_names = {name for name, _tag in schema.FIGHTER_ROOT_FIELDS} | {
        name for name, _tag in schema.PROJECTILE_ROOT_FIELDS
    }

    field_tags: dict[str, str] = {}
    for fields in (
        schema.OBJECT_STATE_FIELDS,
        schema.CHARACTER_STATE_FIELDS,
        schema.SUPERMOVE_FIELDS,
        schema.THROWSTATE_FIELDS,
        schema.GRAB_FIELDS,
        schema.ROBOTSTATE_FIELDS,
        schema.BEASTSTATE_FIELDS,
        schema.WIZARDSTATE_FIELDS,
        HITBOX_RECORD_FIELDS,
        schema.FIGHTER_ROOT_FIELDS,
        schema.PROJECTILE_ROOT_FIELDS,
    ):
        for name, tag in fields:
            field_tags.setdefault(name, tag)

    scope_list = []
    for key in character_keys:
        scope_list.append({"key": key, "kind": "character", "tscn": CHARACTERS[key]})
    for key in projectile_keys:
        scope = scopes[key]
        res = "res://" + str(scope.tscn_path.relative_to(REPO))
        scope_list.append({"key": key, "kind": "projectile", "tscn": res})

    return {
        "scopes": scope_list,
        "state_field_names": sorted(state_field_names),
        "hitbox_field_names": sorted(hitbox_field_names),
        "root_field_names": sorted(root_field_names),
        "field_tags": field_tags,
        "projectile_scene_field": "projectile_scene",
    }


def main() -> None:
    global REPO, BASE_STATE_NAMES, PROJECTILE_KEY_BY_RESPATH, PROJECTILE_VARIANT_BY_RESPATH
    repo_root, out_tables_dir, out_dump_main, out_manifest = sys.argv[1:5]
    REPO = Path(repo_root).resolve()
    out_tables_dir = Path(out_tables_dir)
    out_dump_main = Path(out_dump_main)
    out_manifest = Path(out_manifest)
    setup_paths()

    _registry.update(schema.build_class_registry(REPO))

    base_scope, _ = build_character_scope("base", CHARACTERS["base"])
    BASE_STATE_NAMES = {e.name for e in base_scope.states}

    scopes: dict[str, Scope] = {"base": base_scope}
    character_keys = ["base"]
    all_refs: list[str] = []
    for key, rel_path in CHARACTERS.items():
        if key == "base":
            continue
        scope, refs = build_character_scope(key, rel_path)
        scopes[key] = scope
        character_keys.append(key)
        all_refs.extend(refs)

    projectile_keys: list[str] = []
    queue = list(dict.fromkeys(all_refs))
    seen_respaths: set[str] = set()
    while queue:
        respath = queue.pop(0)
        if respath in seen_respaths:
            continue
        seen_respaths.add(respath)
        stem = Path(respath).stem
        key = rust_mod_name(stem)
        assert key not in scopes, f"duplicate projectile module name {key!r} from {respath!r}"
        tscn_path = res_path(REPO, respath)
        scope, refs = build_projectile_scope(key, tscn_path)
        scope.res_path = respath
        scopes[key] = scope
        projectile_keys.append(key)
        PROJECTILE_KEY_BY_RESPATH[respath] = key
        PROJECTILE_VARIANT_BY_RESPATH[key] = rust_str_enum("ProjectileKind", stem)
        for r in refs:
            if r not in seen_respaths:
                queue.append(r)

    cancel_tokens, stance_tokens, misc_tokens, orb_tokens, fixed_strings, throw_anim_tokens = collect_tokens(scopes)
    collect_fixed_strings(fixed_strings)

    cancel_order = sorted(cancel_tokens)
    stance_order = sorted(stance_tokens)
    misc_order = sorted(misc_tokens)
    orb_order = sorted(orb_tokens)
    throw_anim_order = sorted(throw_anim_tokens)
    projectile_order = [
        (respath, PROJECTILE_VARIANT_BY_RESPATH[key]) for respath, key in sorted(PROJECTILE_KEY_BY_RESPATH.items())
    ]
    # ProjectileKind's variant order must match the order emit_value's
    # lookups and the dump's index-based name arrays both use.
    projectile_order.sort(key=lambda pair: pair[1])

    out_tables_dir.mkdir(parents=True, exist_ok=True)
    (out_tables_dir / "mod.rs").write_text(emit_tables_mod(character_keys, projectile_keys))
    (out_tables_dir / "common.rs").write_text(
        emit_common_rs(cancel_order, stance_order, misc_order, orb_order, projectile_order, throw_anim_order)
    )
    for key in character_keys:
        (out_tables_dir / f"{key}.rs").write_text(emit_scope_rs(scopes[key]))
    proj_dir = out_tables_dir / "projectiles"
    proj_dir.mkdir(exist_ok=True)
    for key in projectile_keys:
        (proj_dir / f"{key}.rs").write_text(emit_scope_rs(scopes[key]))

    out_dump_main.parent.mkdir(parents=True, exist_ok=True)
    out_dump_main.write_text(
        emit_dump_main(
            scopes, character_keys, projectile_keys, cancel_order, stance_order, misc_order, orb_order,
            projectile_order, throw_anim_order,
        )
    )

    manifest = build_manifest(scopes, character_keys, projectile_keys)
    out_manifest.parent.mkdir(parents=True, exist_ok=True)
    out_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(
        f"[tablegen] {len(character_keys)} character scopes, {len(projectile_keys)} projectile scopes, "
        f"{sum(len(s.states) for s in scopes.values())} states, "
        f"{sum(len(e.hitboxes) for s in scopes.values() for e in s.states)} hitboxes, "
        f"{len(cancel_order)} cancel categories, {len(stance_order)} stances, "
        f"{len(misc_order)} misc tags, {len(orb_order)} orb triggers",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
