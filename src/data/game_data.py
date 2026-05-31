"""
Canonical paths for extracted PoE2 game data tracked in this repo.

Use these constants instead of hardcoding strings — the layout can move and
callers won't notice:

    from src.data.game_data import MODS_JSON, PASSIVE_TREE_JSON, load_mods
    mods = load_mods()

See `data/game/README.md` for the layout, lifecycle, and data policy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_BASE_DIR = Path(__file__).resolve().parent.parent.parent
GAME_DATA_DIR = _BASE_DIR / "data" / "game"

# Global version manifest
VERSION_JSON = GAME_DATA_DIR / "version.json"

# Datasets — each is its own folder with `<name>.json` + `metadata.json`
MODS_DIR = GAME_DATA_DIR / "mods"
MODS_JSON = MODS_DIR / "mods.json"
MODS_META = MODS_DIR / "metadata.json"

PASSIVE_TREE_DIR = GAME_DATA_DIR / "passive_tree"
PASSIVE_TREE_JSON = PASSIVE_TREE_DIR / "tree.json"
PASSIVE_TREE_META = PASSIVE_TREE_DIR / "metadata.json"

SUPPORT_GEMS_DIR = GAME_DATA_DIR / "support_gems"
SUPPORT_GEMS_JSON = SUPPORT_GEMS_DIR / "support_gems.json"
SUPPORT_GEMS_META = SUPPORT_GEMS_DIR / "metadata.json"

ASCENDANCIES_DIR = GAME_DATA_DIR / "ascendancies"
ASCENDANCIES_JSON = ASCENDANCIES_DIR / "ascendancies.json"
ASCENDANCIES_META = ASCENDANCIES_DIR / "metadata.json"

STATS_DIR = GAME_DATA_DIR / "stats"
STATS_JSON = STATS_DIR / "stats.json"
STATS_META = STATS_DIR / "metadata.json"

# Datasets pending 0.5 re-extract (paths reserved; folders may not yet exist).
SKILL_GEMS_DIR = GAME_DATA_DIR / "skill_gems"


def get_version() -> Optional[Dict[str, Any]]:
    """Return the parsed contents of data/game/version.json, or None if missing."""
    if not VERSION_JSON.exists():
        return None
    try:
        return json.loads(VERSION_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def load_mods() -> Optional[Dict[str, Any]]:
    """Load and return the canonical mods dataset, or None if missing."""
    if not MODS_JSON.exists():
        return None
    return json.loads(MODS_JSON.read_text(encoding="utf-8"))


def load_passive_tree() -> Optional[Dict[str, Any]]:
    """Load and return the canonical passive tree dataset, or None if missing."""
    if not PASSIVE_TREE_JSON.exists():
        return None
    return json.loads(PASSIVE_TREE_JSON.read_text(encoding="utf-8"))


def load_support_gems() -> Optional[Dict[str, Any]]:
    """Load and return the canonical support gems dataset, or None if missing."""
    if not SUPPORT_GEMS_JSON.exists():
        return None
    return json.loads(SUPPORT_GEMS_JSON.read_text(encoding="utf-8"))


def load_ascendancies() -> Optional[Dict[str, Any]]:
    """Load and return the canonical ascendancies dataset, or None if missing."""
    if not ASCENDANCIES_JSON.exists():
        return None
    return json.loads(ASCENDANCIES_JSON.read_text(encoding="utf-8"))


def load_stats() -> Optional[Dict[int, str]]:
    """Load and return the canonical stats table as a {row_index: stat_id} dict.

    Returns the dict form (not the raw JSON envelope) because that's the shape
    every caller wants — for resolving stat_key references from mods, passives,
    and gems. Returns None if stats.json is missing.
    """
    if not STATS_JSON.exists():
        return None
    payload = json.loads(STATS_JSON.read_text(encoding="utf-8"))
    return {entry["row_index"]: entry["stat_id"] for entry in payload.get("stats", [])}


# ============================================================================
# Convenience lookup helpers — saved scrolls so callers (especially AIs) don't
# have to know the internal JSON shape to do common filters.
#
# These are pure functions over the loaded data — no caching, no IO beyond the
# one load_*() call. Callers that need many lookups should load once and use
# the structures directly; these helpers are for one-shot use from MCP handlers
# and ad-hoc scripts.
# ============================================================================


def find_ascendancies_by_base_class(
    base_class: str,
    include_unused: bool = False,
) -> list:
    """Return all ascendancy entries whose base_class matches.

    Example:
        >>> find_ascendancies_by_base_class("Witch")
        [{"id": "Witch1", "display_name": "Infernalist", ...},
         {"id": "Witch2", "display_name": "Blood Mage", ...},
         {"id": "Witch3", "display_name": "Lich", ...},
         {"id": "Witch3b", "display_name": "Abyssal Lich", ...}]

    Args:
        base_class: Case-insensitive class name ("Witch", "monk", etc.).
        include_unused: If True, include [DNT-UNUSED] placeholder rows.
                        Default False — only PoE2-active ascendancies.

    Returns empty list if dataset isn't loaded yet or no matches.
    Relies on PR #78's base_class field; older revisions without that
    field will return empty.
    """
    payload = load_ascendancies()
    if not payload:
        return []
    target = base_class.lower()
    out = []
    for a in payload.get("ascendancies", []):
        if (a.get("base_class") or "").lower() != target:
            continue
        if not include_unused and a.get("is_unused"):
            continue
        out.append(a)
    return out


def find_mods_by_stat_id(
    stat_id: str,
    generation_type: Optional[str] = None,
    limit: int = 50,
) -> list:
    """Return mods whose stats include the given stat_id.

    Relies on PR #75's inline stat_id enrichment. Falls back to empty if the
    mods dataset doesn't have that field yet (older data revisions).

    Args:
        stat_id: Canonical stat identifier (e.g. 'additional_strength',
                 'base_life_regeneration_rate_per_second').
        generation_type: Optional filter — 'PREFIX', 'SUFFIX', 'IMPLICIT',
                         'CORRUPTED'. None = all types.
        limit: Cap on returned mods (sorted by level_requirement asc).
    """
    payload = load_mods()
    if not payload:
        return []
    target = stat_id.lower()
    out = []
    for mod in payload.get("mods", []):
        if generation_type and mod.get("generation_type_name") != generation_type:
            continue
        for s in mod.get("stats", []):
            if s.get("is_empty"):
                continue
            sid = s.get("stat_id")
            if sid and sid.lower() == target:
                out.append(mod)
                break
    out.sort(key=lambda m: m.get("level_requirement", 0))
    return out[:limit]


def find_mods_by_display_name(display_name_fragment: str, limit: int = 50) -> list:
    """Substring search across mod display_name (case-insensitive).

    Useful for "find the mod that grants X" queries when the user knows the
    suffix/prefix name (e.g. 'of the Newt') but not the stat_id.
    """
    payload = load_mods()
    if not payload:
        return []
    needle = display_name_fragment.lower()
    out = [m for m in payload.get("mods", []) if needle in (m.get("display_name") or "").lower()]
    out.sort(key=lambda m: m.get("level_requirement", 0))
    return out[:limit]


def get_keystones() -> list:
    """Return all keystone nodes from the passive tree.

    Each entry includes id, name, stats, and other passive-tree-node fields.
    Empty list if passive_tree dataset isn't loaded.
    """
    payload = load_passive_tree()
    if not payload:
        return []
    return [n for n in payload.get("nodes", {}).values() if n.get("is_keystone")]


def get_notables() -> list:
    """Return all notable nodes from the passive tree (excluding keystones)."""
    payload = load_passive_tree()
    if not payload:
        return []
    return [n for n in payload.get("nodes", {}).values() if n.get("is_notable") and not n.get("is_keystone")]


def find_keystone_by_name(name: str) -> Optional[Dict[str, Any]]:
    """Look up a keystone by display name (case-insensitive exact match).

    Example:
        >>> find_keystone_by_name("Resolute Technique")
        {"id": "passive_keystone_resolute_technique", "name": "Resolute Technique", ...}
    """
    target = name.lower().strip()
    for k in get_keystones():
        if (k.get("name") or "").lower().strip() == target:
            return k
    return None


def load_metadata(dataset_dir: Path) -> Optional[Dict[str, Any]]:
    """Load `metadata.json` from a dataset directory."""
    meta = dataset_dir / "metadata.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def describe() -> str:
    """Human-readable summary of what data is currently installed. Useful for
    health_check / diagnostic output."""
    v = get_version()
    if not v:
        return "data/game/ not populated yet — see README.md"
    lines = [
        f"PoE2 Patch {v.get('patch_version', '?')} "
        f"({v.get('patch_name', '?')}, released {v.get('patch_released', '?')})",
        f"Released as: {v.get('released_as', '?')}; data revision {v.get('data_revision', '?')}",
        f"Extracted: {v.get('extracted_at', '?')}",
    ]
    for ds, info in (v.get("datasets") or {}).items():
        lines.append(f"  - {ds}: {info.get('record_count', '?')} records at {info.get('path', '?')}")
    pending = v.get("datasets_pending_0_5_reextract") or {}
    if pending:
        lines.append(f"  pending re-extract: {', '.join(pending.keys())}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe())
