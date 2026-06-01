"""
Lightweight text-search helpers shared across MCP handlers.

Kept in its own module (no heavy imports) so test files can pull these
helpers without triggering full mcp_server initialization.
"""

from __future__ import annotations

import difflib
from typing import Iterable, List


def did_you_mean(query: str, candidates: Iterable[str], k: int = 5) -> List[str]:
    """Return up to k 'did you mean' suggestions from a list of candidate names.

    Combines two signals:
      - Case-insensitive substring matches (prefix and contained).
      - difflib.get_close_matches fuzzy matches (catches typos).

    The merge preserves order: substring matches first (most likely intent),
    then fuzzy matches that aren't already in the substring list. Deduplicated
    case-insensitively while preserving the original casing of each name.

    Args:
        query: The user's input string. Stripped + lowercased internally.
        candidates: Iterable of candidate name strings. Falsy entries skipped.
        k: Max number of suggestions to return.

    Returns:
        List of up to k candidate names, in relevance order. Empty if no
        candidates pass either matcher.

    Notes:
        - 0.4 fuzzy cutoff was chosen empirically: low enough to catch typos
          like "Avatar of Fire" -> "Avatar of Flame", high enough to not
          surface unrelated words.
        - Used by the P2 inspect_* handlers (#115). Generic enough to apply
          to any name-list lookup that wants suggestions on misses.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    pool = [c for c in candidates if c]
    pool_lower = [c.lower() for c in pool]

    seen = set()
    out: List[str] = []

    # Substring matches: prefix first, then contained.
    prefix = [pool[i] for i, c in enumerate(pool_lower) if c.startswith(q)]
    contained = [pool[i] for i, c in enumerate(pool_lower) if q in c and not c.startswith(q)]
    for name in prefix + contained:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(name)
            if len(out) >= k:
                return out

    # Fuzzy match for typo recovery — only fill remaining slots.
    remaining = k - len(out)
    if remaining > 0:
        fuzzy = difflib.get_close_matches(q, pool_lower, n=remaining * 2, cutoff=0.4)
        for f in fuzzy:
            for i, c in enumerate(pool_lower):
                if c == f:
                    name = pool[i]
                    if name.lower() not in seen:
                        seen.add(name.lower())
                        out.append(name)
                        if len(out) >= k:
                            return out
                    break
    return out
