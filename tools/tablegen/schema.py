"""What tools/tablegen keeps, and how a node's class chain resolves.

This module is the one place that names which `export var` fields the
generated tables carry. `gen.py` walks the game's own `.tscn`/`.gd` files
and asks this module two questions: "what is this script's class chain,
root to leaf", and "which of its merged fields does a table keep".

See `tools/tablegen/README.md`, "Kept and dropped fields", for the full
list and the reason for every drop. That list is generated from the
tables below, so this module and the README cannot drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godot_scene import EnumRef, parse_gd_script

# ---------------------------------------------------------------------
# Directories this generator reads `.gd` scripts from, to build the
# `class_name -> file` registry that `extends SomeClassName` resolves
# against. Every script a character's or a projectile's state/hitbox
# chain can reach lives under one of these.
# ---------------------------------------------------------------------
SCRIPT_ROOTS = [
    "characters",
    "mechanics",
    "obj",
    "projectile",
    "addons/collision_box",
    "framework/FiniteStateMachine",
]

# Godot's own enums that a `.gd` default can reference by dotted name
# (`AirType.Grounded`, `Hitbox.HitHeight.Mid`). Keyed by the enum's own
# name (the second-to-last dotted part); Godot enum names are unique
# across this game's scripts, so no class qualifier is needed to tell
# them apart. Each value is `{member_name: raw_int}`, in the exact
# declaration order of the `.gd` source (`characters/states/CharState.gd`,
# `mechanics/Hitbox.gd`).
GODOT_ENUMS: dict[str, dict[str, int]] = {
    "ActionType": {"Movement": 0, "Attack": 1, "Special": 2, "Super": 3, "Defense": 4, "Hurt": 5},
    "AirType": {"Grounded": 0, "Aerial": 1, "Both": 2},
    "BusyInterrupt": {"Normal": 0, "Hurt": 1, "None": 2},
    "HitboxType": {
        "Normal": 0,
        "Flip": 1,
        "ThrowHit": 2,
        "OffensiveBurst": 3,
        "Burst": 4,
        "NoHitstun": 5,
        "Detect": 6,
    },
    "HitHeight": {"High": 0, "Mid": 1, "Low": 2},
}


def resolve_enum_ref(ref: EnumRef) -> int:
    enum_name, member = ref.parts[-2], ref.parts[-1]
    try:
        return GODOT_ENUMS[enum_name][member]
    except KeyError as exc:
        raise ValueError(f"unknown enum reference {'.'.join(ref.parts)}") from exc


# ---------------------------------------------------------------------
# Field manifests. Each entry is (field_name, type_tag). Type tags:
#   "bool", "int", "fixed" (decimal string -> Fixed), "enum:X" (X is a
#   key into GODOT_ENUMS), "state_self" (a state name in the same
#   character/projectile's own state machine), "state_base" (a state
#   name in the base Fighter's 58-state machine -- always valid for any
#   victim), "stance_self", "throw_positions", "frame_list"
#   (list[int]), "categories" (a newline list of cancel-category
#   tokens), "misc_tag" (the Caltrops/Frozen free-text tag),
#   "orb_trigger" (Wizard's `trigger_orb_type`), "projectile_kind" (a
#   `projectile_scene` reference, resolved to which projectile table it
#   names).
# ---------------------------------------------------------------------

Field = tuple[str, str]

OBJECT_STATE_FIELDS: list[Field] = [
    ("apply_forces", "bool"),
    ("apply_fric", "bool"),
    ("apply_grav", "bool"),
    ("reset_momentum", "bool"),
    ("reset_x_momentum", "bool"),
    ("reset_y_momentum", "bool"),
    ("apply_custom_x_fric", "bool"),
    ("apply_custom_y_fric", "bool"),
    ("apply_custom_grav", "bool"),
    ("apply_forces_no_limit", "bool"),
    ("custom_x_fric", "fixed"),
    ("custom_y_fric", "fixed"),
    ("custom_grav", "fixed"),
    ("custom_grav_max_fall_speed", "fixed"),
    ("apply_custom_limits", "bool"),
    ("custom_max_air_speed", "fixed"),
    ("custom_max_ground_speed", "fixed"),
    ("anim_length", "int"),
    ("endless", "bool"),
    ("disable_at_end", "bool"),
    ("force_dir_x", "fixed"),
    ("force_dir_y", "fixed"),
    ("force_speed", "fixed"),
    ("force_tick", "int"),
    ("enter_force_dir_x", "fixed"),
    ("enter_force_dir_y", "fixed"),
    ("enter_force_speed", "fixed"),
    ("projectile_scene", "projectile_kind"),
    ("projectile_tick", "int"),
    ("projectile_pos_x", "int"),
    ("projectile_pos_y", "int"),
    ("projectile_local_pos", "bool"),
    ("projectile_match_facing", "bool"),
    ("extra_parry_hitlag", "int"),
    ("flip_frame", "int"),
    ("force_same_direction_as_previous_state", "bool"),
    ("earliest_hitbox", "int"),
    ("fallback_state", "state_self_opt"),
    ("throw_positions", "throw_positions"),
]

# Dropped ObjectState fields, and why: see README "Kept and dropped fields".
OBJECT_STATE_DROPPED = {
    "sprite_animation": "presentation: a sprite-sheet animation name",
    "sprite_anim_length": "presentation: only sizes the sprite's own frame loop",
    "ticks_per_frame": "presentation: only slows the sprite's own frame loop",
    "loop_animation": "presentation: sprite frame looping",
    "animation_loop_start": "presentation: sprite frame looping",
    "absolute_loop": "presentation: sprite frame looping",
    "refresh_loop": "presentation: sprite frame looping",
    "particle_scene": "presentation: a visual effect scene",
    "particle_position": "presentation: a visual effect's offset",
    "spawn_particle_on_enter": "presentation: a visual effect trigger",
    "state_screenshake_tick": "presentation: a camera shake cue",
    "state_screenshake_dir": "presentation: a camera shake cue",
    "state_screenshake_length": "presentation: a camera shake cue",
    "state_screenshake_amount": "presentation: a camera shake cue",
    "timed_particle_scene": "presentation: a visual effect scene",
    "timed_particle_position": "presentation: a visual effect's offset",
    "timed_spawn_particle_tick": "presentation: a visual effect trigger",
    "enter_sfx": "presentation: an audio clip",
    "enter_sfx_volume": "presentation: an audio clip's volume",
    "sfx": "presentation: an audio clip",
    "sfx_tick": "presentation: an audio clip's cue tick",
    "sfx_volume": "presentation: an audio clip's volume",
    "sfx_pitch_variation": "presentation: an audio clip's pitch jitter",
    "override_same_sfx": "presentation: an audio-overlap rule",
    "override_same_wav": "presentation: an audio-overlap rule",
    "host_commands": "always the empty dict in all five characters' data (checked)",
    "extra_state_variables": "names rollback-snapshot vars for the GDScript engine; the port's state is already explicit (decision 9), so it has no counterpart",
}

CHARACTER_STATE_FIELDS: list[Field] = [
    ("uses_air_movement", "bool"),
    ("ignore_air_option_use_in_combos", "bool"),
    ("land_cancel", "bool"),
    ("landing_recovery", "int"),
    ("min_land_cancel_frame", "int"),
    ("land_cancel_state", "state_self_req"),
    ("iasa_at", "int"),
    ("iasa_on_hit", "int"),
    ("iasa_on_hit_on_block", "bool"),
    ("interrupt_frames", "frame_list"),
    ("throw_techable", "bool"),
    ("interruptible_on_opponent_turn", "bool"),
    ("update_facing_on_exit", "bool"),
    ("dynamic_iasa", "bool"),
    ("backdash_iasa", "bool"),
    ("allow_framecheat", "bool"),
    ("next_state_on_hold", "bool"),
    ("next_state_on_hold_on_opponent_turn", "bool"),
    ("combo_only", "bool"),
    ("neutral_only", "bool"),
    ("end_feint", "bool"),
    ("usable_from_whiff_cancel_if_possible", "bool"),
    ("count_projectile_hits", "bool"),
    ("hold_restart", "state_self_opt"),
    ("busy_interrupt_type", "enum:BusyInterrupt"),
    ("burst_cancellable", "bool"),
    ("burstable", "bool"),
    ("self_hit_cancellable", "bool"),
    ("self_interruptable", "bool"),
    ("reversible", "bool"),
    ("instant_cancellable", "bool"),
    ("force_feintable", "bool"),
    ("can_feint_if_possible", "bool"),
    ("selectable", "bool"),
    ("interrupt_from_string", "categories:interrupt_from"),
    ("interrupt_into_string", "categories:interrupt_into"),
    ("hit_cancel_into_string", "categories:hit_cancel_into"),
    ("interrupt_exceptions_string", "categories:interrupt_exceptions"),
    ("hit_cancel_exceptions_string", "categories:hit_cancel_exceptions"),
    ("always_usable", "bool"),
    ("allowed_stances_string", "stances"),
    ("change_stance_to", "stance_self_opt"),
    ("release_opponent_on_startup", "bool"),
    ("release_opponent_on_exit", "bool"),
    ("initiative_effect", "bool"),
    ("initiative_startup_reduction_amount", "int"),
    ("apply_pushback", "bool"),
    ("beats_backdash", "bool"),
    ("no_collision_start_frame", "int"),
    ("no_collision_end_frame", "int"),
    ("can_be_counterhit", "bool"),
    ("tick_priority", "int"),
    ("velocity_forward_meter_gain_multiplier", "fixed"),
    ("whiff_meter_gain_multiplier", "fixed"),
    ("draw_cancel_on_block", "bool"),
    ("draw_cancel_on_block_min_tick", "int"),
    ("type", "enum:ActionType"),
    ("air_type", "enum:AirType"),
    ("super_level_", "int"),
    ("supers_used_", "int"),
    ("super_freeze_ticks_", "int"),
    ("super_effect_", "bool"),
    ("scale_combo_meter_", "bool"),
    ("force_super_effect", "bool"),
]

CHARACTER_STATE_DROPPED = {
    "_c_Menu": "an inspector category marker, not data",
    "title": "presentation: the move's UI display name",
    "show_in_menu": "presentation: a UI visibility flag",
    "data_ui_scene": "presentation: a UI scene",
    "button_texture": "presentation: a UI icon",
    "flip_icon": "presentation: a UI icon mirroring rule",
    "flip_with_facing": "presentation: a UI icon mirroring rule",
    "_c_Air_Data": "an inspector category marker, not data",
    "_c_Interrupt_Data": "an inspector category marker, not data",
    "_c_Interrupt_Categories": "an inspector category marker, not data",
    "_c_Stances": "an inspector category marker, not data",
    "_c_Misc": "an inspector category marker, not data",
    "_c_Super": "an inspector category marker, not data",
}

SUPERMOVE_FIELDS: list[Field] = [
    ("super_level", "int"),
    ("supers_used", "int"),
    ("super_freeze_ticks", "int"),
    ("super_effect", "bool"),
    ("scale_combo_meter", "bool"),
]
SUPERMOVE_DROPPED = {"_c_Super_OBSOLETE": "an inspector category marker, not data"}

THROWSTATE_FIELDS: list[Field] = [
    ("release", "bool"),
    ("release_frame", "int"),
    ("use_start_throw_pos", "bool"),
    ("start_throw_pos_x", "int"),
    ("start_throw_pos_y", "int"),
    ("use_release_throw_pos", "bool"),
    ("release_throw_pos_x", "int"),
    ("release_throw_pos_y", "int"),
    ("hitstun_ticks", "int"),
    ("knockback", "fixed"),
    ("dir_x", "fixed"),
    ("dir_y", "fixed"),
    ("knockdown", "bool"),
    ("knockdown_extends_hitstun", "bool"),
    ("aerial_hit_state", "state_base_req"),
    ("grounded_hit_state", "state_base_req"),
    ("damage", "int"),
    ("damage_in_combo", "int"),
    ("reverse", "bool"),
    ("disable_collision", "bool"),
    ("ground_bounce", "bool"),
    ("hits_otg", "bool"),
    ("increment_combo", "bool"),
    ("hard_knockdown", "bool"),
    ("force_grounded", "bool"),
    ("air_ground_bounce", "bool"),
    ("wall_slam", "bool"),
    ("di_modifier", "fixed"),
    ("minimum_grounded_frames", "int"),
    ("damage_proration", "int"),
    ("hit_height", "enum:HitHeight"),
    ("misc_data", "misc_tag"),
]
THROWSTATE_DROPPED = {
    "_c_Throw_Data": "an inspector category marker, not data",
    "_c_Release_Data": "an inspector category marker, not data",
    "_c_Release_Sound": "an inspector category marker, not data",
    "release_sfx": "presentation: an audio clip",
    "release_sfx_volume": "presentation: an audio clip's volume",
    "play_release_sfx_bass": "presentation: an audio clip trigger",
    "screenshake_amount": "presentation: a camera shake cue",
    "screenshake_frames": "presentation: a camera shake cue",
}

ROBOTSTATE_FIELDS: list[Field] = [
    ("is_super", "bool"),
    ("can_fly", "bool"),
    ("throw_invuln_frames", "int"),
    ("super_scale_combo_meter", "bool"),
]
ROBOTSTATE_DROPPED = {"_c_Robot_Obsolete": "an inspector category marker, not data"}

BEASTSTATE_FIELDS: list[Field] = [
    ("moving_up_only", "bool"),
    ("moving_down_only", "bool"),
    ("force_air_juke", "bool"),
    ("force_air_juke_allow_down", "bool"),
    ("juke_on_block", "bool"),
    ("juke_pip_max_blocks", "int"),
]

WIZARDSTATE_FIELDS: list[Field] = [
    ("trigger_orb_attack", "bool"),
    ("trigger_orb_type", "orb_trigger"),
    ("trigger_orb_delay", "int"),
]

GRAB_FIELDS: list[Field] = [
    ("forward_throw_state", "state_self_req"),
    ("back_throw_state", "state_self_req"),
    ("down_throw_state", "state_self_req"),
]

HITBOX_FIELDS: list[Field] = [
    ("activated", "bool"),
    ("damage", "int"),
    ("damage_in_combo", "int"),
    ("minimum_damage", "int"),
    ("chip_damage_modifier", "fixed"),
    ("hitbox_type", "enum:HitboxType"),
    ("hitstun_ticks", "int"),
    ("combo_hitstun_ticks", "int"),
    ("hitlag_ticks", "int"),
    ("victim_hitlag", "int"),
    ("combo_victim_hitlag", "int"),
    ("damage_proration", "int"),
    ("scale_combo", "bool"),
    ("combo_scaling_amount", "int"),
    ("cancellable", "bool"),
    ("increment_combo", "bool"),
    ("hits_otg", "bool"),
    ("hits_vs_standing", "bool"),
    ("hits_vs_grounded", "bool"),
    ("hits_vs_aerial", "bool"),
    ("can_counter_hit", "bool"),
    ("di_modifier", "fixed"),
    ("sdi_modifier", "fixed"),
    ("meter_gain_modifier", "fixed"),
    ("parry_meter_gain", "int"),
    ("ignore_armor", "bool"),
    ("followup_state", "state_self_opt"),
    ("force_grounded", "bool"),
    ("can_clash", "bool"),
    ("hits_vs_dizzy", "bool"),
    ("beats_grab", "bool"),
    ("hits_projectiles", "bool"),
    ("ignore_projectile_armor", "bool"),
    ("allowed_to_hit_own_team", "bool"),
    ("guard_break", "bool"),
    ("guard_break_proration", "int"),
    ("block_punishable", "bool"),
    ("parriable", "bool"),
    ("block_cancel_allowed", "bool"),
    ("plus_frames", "int"),
    ("hit_height", "enum:HitHeight"),
    ("priority", "int"),
    ("group", "int"),
    ("dir_x", "fixed"),
    ("dir_y", "fixed"),
    ("knockback", "fixed"),
    ("launch_reversible", "bool"),
    ("vacuum", "bool"),
    ("send_away_from_center", "bool"),
    ("block_pushback_modifier", "fixed"),
    ("block_pushback_reversible", "bool"),
    ("block_reverse_pushback_modifier", "fixed"),
    ("pushback_x", "fixed"),
    ("grounded_hit_state", "state_base_req"),
    ("aerial_hit_state", "state_base_req"),
    ("minimum_grounded_frames", "int"),
    ("knockdown", "bool"),
    ("knockdown_extends_hitstun", "bool"),
    ("hard_knockdown", "bool"),
    ("disable_collision", "bool"),
    ("air_ground_bounce", "bool"),
    ("ground_bounce", "bool"),
    ("ground_bounce_knockback_modifier", "fixed"),
    ("wall_slam", "bool"),
    ("x", "int"),
    ("y", "int"),
    ("width", "int"),
    ("height", "int"),
    ("start_tick", "int"),
    ("active_ticks", "int"),
    ("always_on", "bool"),
    ("looping", "bool"),
    ("loop_active_ticks", "int"),
    ("loop_inactive_ticks", "int"),
    ("misc_data", "misc_tag"),
]
HITBOX_DROPPED = {
    "_c_Damage": "an inspector category marker, not data",
    "_c_Hit_Properties": "an inspector category marker, not data",
    "_c_Block_Properties": "an inspector category marker, not data",
    "_c_Grouping": "an inspector category marker, not data",
    "_c_Fx": "an inspector category marker, not data",
    "_c_Sfx": "an inspector category marker, not data",
    "_c_Knockback": "an inspector category marker, not data",
    "_c_Knockback_Type": "an inspector category marker, not data",
    "_c_Frame_Data": "an inspector category marker, not data",
    "_c_Loop_Data": "an inspector category marker, not data",
    "screenshake_amount": "presentation: a camera shake cue",
    "screenshake_frames": "presentation: a camera shake cue",
    "hit_particle": "presentation: a visual effect scene",
    "replace_hit_particle": "presentation: a visual effect rule",
    "camera_bump_dir": "presentation: a camera shake cue",
    "whiff_particle": "presentation: a visual effect scene",
    "bump_on_whiff": "presentation: a camera shake trigger",
    "rumble": "presentation: a controller-rumble cue",
    "whiff_sound": "presentation: an audio clip",
    "hit_sound": "presentation: an audio clip",
    "hit_bass_sound": "presentation: an audio clip",
    "whiff_sound_volume": "presentation: an audio clip's volume",
    "hit_sound_volume": "presentation: an audio clip's volume",
    "bass_sound_volume": "presentation: an audio clip's volume",
    "bass_on_whiff": "presentation: an audio clip trigger",
    "override_same_sfx": "presentation: an audio-overlap rule",
    "override_same_wav": "presentation: an audio-overlap rule",
    "sfx_pitch_variation": "presentation: an audio clip's pitch jitter",
    "can_draw": "presentation (editor gizmo visibility)",
}

THROWBOX_FIELDS: list[Field] = [("throw_state", "state_self_opt")]
THROWBOX_DROPPED = {"_c_Throw_Data": "an inspector category marker, not data"}

SWEPTHITBOX_FIELDS: list[Field] = [("to_x", "int"), ("to_y", "int")]
SWEPTHITBOX_DROPPED = {"_c_Raycast": "an inspector category marker, not data"}

FIGHTER_ROOT_FIELDS: list[Field] = [
    ("gravity", "fixed"),
    ("ground_friction", "fixed"),
    ("air_friction", "fixed"),
    ("max_ground_speed", "fixed"),
    ("max_air_speed", "fixed"),
    ("max_fall_speed", "fixed"),
    ("damages_own_team", "bool"),
    ("has_projectile_parry_window", "bool"),
    ("always_parriable", "bool"),
    ("num_air_movements", "int"),
    ("lose_one_air_option_in_neutral", "bool"),
    ("use_air_option_bar", "bool"),
    ("air_option_bar_max", "int"),
    ("air_option_bar", "int"),
    ("damage_taken_modifier", "fixed"),
    ("knockback_taken_modifier", "fixed"),
    ("di_modifier", "fixed"),
    ("num_feints", "int"),
]
FIGHTER_ROOT_DROPPED = {
    "id": "assigned at match setup, not per-character data",
    "dummy": "an editor/debug flag",
    "_c_MovementAttributes": "an inspector category marker, not data",
    "extra_state_variables": "names rollback-snapshot vars for the GDScript engine; the port's state is already explicit (decision 9), so it has no counterpart",
    "throw_positions": "always the empty dict at the Fighter root in all five characters' data (checked); see OBJECT_STATE_DROPPED for the per-state field, dropped for a different reason",
    "_c_NEW_MODDED_CHARACTERS_IMPORTANT": "an inspector category marker, not data",
    "_c_ENABLE_THIS_SETTING_IF_YOU_WANT_CONSISTENCY_WITH_THE_BASE_CAST": "an inspector category marker, not data",
    "enable_extra_aesthetic_hitstop": "presentation (the name says so: \"aesthetic\")",
    "_c_THANK_YOU": "an inspector category marker, not data",
    "air_option_bar_name": "presentation: a UI label",
    "character_portrait": "presentation: a UI image",
    "character_portrait2": "presentation: a UI image",
    "player_info_scene": "presentation: a UI scene",
    "player_extra_params_scene": "presentation: a UI scene",
    "use_extra_color_1": "presentation: a palette-swap flag",
    "extra_color_1": "presentation: a palette-swap color",
    "use_extra_color_2": "presentation: a palette-swap flag",
    "extra_color_2": "presentation: a palette-swap color",
}

PROJECTILE_ROOT_FIELDS: list[Field] = [
    ("gravity", "fixed"),
    ("ground_friction", "fixed"),
    ("air_friction", "fixed"),
    ("max_ground_speed", "fixed"),
    ("max_air_speed", "fixed"),
    ("max_fall_speed", "fixed"),
    ("damages_own_team", "bool"),
    ("has_projectile_parry_window", "bool"),
    ("always_parriable", "bool"),
    ("immunity_susceptible", "bool"),
    ("roll_immunity_susceptible", "bool"),
    ("deletes_other_projectiles", "bool"),
    ("fizzle_on_ceiling", "bool"),
    ("movable", "bool"),
    ("can_be_hit_by_melee", "bool"),
    ("hit_by_self_projectiles", "bool"),
    ("hit_cancel_on_hit", "bool"),
    ("free_cancel_on_hit", "bool"),
    ("apply_hitlag_when_hit_by_melee", "bool"),
    ("projectile_immune", "bool"),
    ("hitlag_modifier", "fixed"),
    ("hookable", "bool"),
]
PROJECTILE_ROOT_DROPPED = {
    "id": "assigned at spawn time, not per-projectile data",
    "dummy": "an editor/debug flag",
    "_c_MovementAttributes": "an inspector category marker, not data",
    "extra_state_variables": "names rollback-snapshot vars for the GDScript engine; the port's state is already explicit (decision 9), so it has no counterpart",
    "throw_positions": "projectiles are never grabbed; always the empty dict in the game's own data",
}

# The leaf-only state scripts this generator does not carry fields from,
# and the field names each one declares. Every one of these feeds only
# that single move's own `_frame_N`/`_tick` code, which decision 9 defers
# to a later slice (this slice is data only). Filled in by gen.py from a
# live scan, so the list cannot go stale; see README "Kept and dropped
# fields" for the rendered table.
LEAF_ONLY_MIXIN_NAMES = {"ObjectState", "StateInterface", "CollisionBox"}


@dataclass
class ClassInfo:
    name: str | None  # class_name, if the script declares one
    path: Path
    extends: str | None  # resolved class name of the parent, or None at the root


def build_class_registry(repo: Path) -> dict[str, Path]:
    """`class_name -> .gd file`, scanned once from SCRIPT_ROOTS, plus the
    repo root's own direct `.gd` children (not recursive: that is where
    `ObjectState.gd` lives, the base of every state script's chain, and
    a recursive root scan would also pull in `.scratch/` mod copies and
    unrelated top-level game scripts)."""
    registry: dict[str, Path] = {}
    for gd in sorted(repo.glob("*.gd")):
        info = parse_gd_script(gd)
        if info.class_name:
            registry[info.class_name] = gd
    for root in SCRIPT_ROOTS:
        root_path = repo / root
        if not root_path.is_dir():
            continue
        for gd in sorted(root_path.rglob("*.gd")):
            info = parse_gd_script(gd)
            if info.class_name:
                registry[info.class_name] = gd
    return registry


@dataclass
class Chain:
    """A script's class hierarchy, root (engine base) to leaf (this file)."""

    scripts: list  # list[godot_scene.ScriptInfo], root first

    def merged_defaults(self) -> dict:
        out: dict = {}
        for script in self.scripts:
            for name, value in script.exports.items():
                out[name] = value
            for name in script.exports_no_default:
                out.setdefault(name, _zero_for_hint(script.export_hints.get(name)))
        return out

    def class_names(self) -> set[str]:
        return {s.class_name for s in self.scripts if s.class_name}


def _zero_for_hint(hint: str | None) -> object:
    if hint is None or hint == "" or hint.startswith("String"):
        return ""
    if hint.startswith("PackedScene") or hint.startswith("Texture") or hint.startswith("AudioStream"):
        return None
    if hint.startswith("int"):
        return 0
    if hint in GODOT_ENUMS:
        # `characters/states/CharState.gd`'s `export(ActionType) var type`
        # has no `=` default. Godot's own rule for an un-defaulted
        # exported enum is its first declared member, raw value 0.
        return 0
    return None


def resolve_chain(repo: Path, gd_path: Path, registry: dict[str, Path], cache: dict) -> Chain:
    if gd_path in cache:
        return cache[gd_path]
    info = parse_gd_script(gd_path)
    scripts = []
    if info.extends:
        if info.extends.startswith("res://"):
            parent_path = repo / info.extends[len("res://"):]
        elif info.extends in registry:
            parent_path = registry[info.extends]
        else:
            parent_path = None  # an engine built-in (Node2D, Node, ...)
        if parent_path is not None:
            scripts.extend(resolve_chain(repo, parent_path, registry, cache).scripts)
    scripts.append(info)
    chain = Chain(scripts=scripts)
    cache[gd_path] = chain
    return chain
