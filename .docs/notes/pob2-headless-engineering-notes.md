# PoB2 Headless Integration — Engineering Notes

## Overview

`calculate_pob2_dps` is an MCP tool that spawns a LuaJIT subprocess running Path of Building 2's `HeadlessWrapper.lua` to calculate DPS from a PoB XML build. The subprocess writes results to a temp JSON file (stdout is ignored — PoB2 contaminants it with startup logs).

## Related docs

- [PoB2 Headless Integration Plan](../plans/PoB2-headless-integration.md) — overall plan, goals, and architecture decisions.

## File Layout

```
.mcp-servers/poe2-mcp/
├── src/
│   ├── mcp_server.py                       # Tool registration + _handle_calculate_pob2_dps handler
│   └── pob/
│       ├── headless_bridge.lua             # Lua bridge — parses args, loads PoB2, runs calc, writes JSON
│       └── headless_client.py              # Python subprocess wrapper — resolve runtime, spawn, read result
├── tests/
│   └── test_pob2_headless.py              # 29 tests — runtime validation, subprocess mock, MCP handler
├── pyproject.toml                          # package-data includes pob/*.lua
├── .env                                    # POB2_SRC_PATH + LUAJIT_PATH (local, no secrets)
└── README.md                               # Tool description in tools table
```

External dependency (not in this repo):
- `PathOfBuilding-PoE2/src/` — cloned from `PathOfBuildingCommunity/PathOfBuilding-PoE2`
- `luajit.exe` — installed via `winget install DEVCOM.LuaJIT` or manual download

## Setup

**Prerequisite understanding:** `runtime/luajit/` and `runtime/pob2/` are **not tracked in git**. They are optional local-only locations for development convenience (fallback paths). You may clone/install them anywhere; point to them via `.env`.

### 1. Clone PoB2

Clone outside the repo (recommended) or into the ignored `runtime/pob2/`:

```powershell
git clone --depth 1 https://github.com/PathOfBuildingCommunity/PathOfBuilding-PoE2.git
```

Example paths:
- `C:\repos\PathOfBuilding-PoE2\src`
- `runtime\pob2\src` (local, git-ignored)

### 2. Install LuaJIT

```powershell
winget install DEVCOM.LuaJIT
# or download from https://luajit.org/download.html
# Installed exe: C:\Program Files\LuaJIT\luajit.exe (or similar)
```

### 3. Configure `.env`

Create or edit `.mcp-servers/poe2-mcp/.env`:

```env
POB2_SRC_PATH=C:\absolute\path\to\PathOfBuilding-PoE2\src
LUAJIT_PATH=C:\absolute\path\to\luajit.exe
```

**Critical — `.env` contains local machine paths and must NOT be committed.** It is already in `.gitignore`. Do not force-add it.

**Important:** `.env` is read at MCP process start. You must **restart the MCP server** (or the AI assistant hosting it) after changing `.env`.

If these are not set, the client falls back to `BASE_DIR/runtime/pob2/src` and `BASE_DIR/runtime/luajit/` (local-only, git-ignored paths).

### 4. Verify paths resolve

```powershell
python -c "from src.pob.headless_client import resolve_runtime; r=resolve_runtime(); print(r)"
```

If `HeadlessWrapper.lua` is missing at the expected path, you'll get a clear error.

## Flow

```
User provides exactly one of:
  build_xml_path | build_xml_content | build_share_code | poe_ninja_url
                          │
                          ▼
  MCP handler resolves source → writes XML to temp file
                          │
                          ▼
  Python client resolves PoB2/LuaJIT paths → builds command:
    luajit headless_bridge.lua --xml-file <temp.xml> --json-out <temp.json>
                          │
                          ▼
  Subprocess runs with:
    - cwd = POB2_SRC_PATH
    - env CI=true (skips ModCache, enables headless mode)
    - stdout captured but ignored
    - stderr captured for diagnostics
    - timeout (default 30s, configurable)
                          │
                          ▼
  Lua bridge:
    1. Sets package.path/cpath for PoB2 runtime modules
    2. dofile("HeadlessWrapper.lua")
    3. loadBuildFromXML(xml, name) — returns nil; _G.build is the active build
    4. calcsTab:BuildOutput() — returns nil by design; reads calcsTab.mainOutput
    5. Writes JSON to --json-out file
    6. Exits 0 on success, 1 on error
                          │
                          ▼
  Python reads JSON file → enriches metadata → returns to MCP client
```

## Tool Input Examples

### p Local XML file

```
build_xml_path: "C:/Users/Me/builds/mydeadeye.xml"
```

### Raw XML content

```
build_xml_content: "<PathOfBuilding><Build><Player>...</Player></Build></PathOfBuilding>"
```

### PoB share code

```
build_share_code: "AAAABAcAAAAAAAEAAABAz8vK..."  (base64 + zlib compressed XML)
```

### poe.ninja profile URL

```
poe_ninja_url: "https://poe.ninja/poe2/profile/Miwtoo-3415/runesofaldur/character/Miwtoo_ROTA"
```

Flow: poe.ninja URL → `get_pob_import` API → PoB share code decode → XML → headless calc

### skill_selector (MVP rejects)

```
skill_selector: {"socket_group": 1, "skill_index": 1}
```

This is **rejected** with `unsupported_feature`. The bridge uses the build's saved selected skill. Future work needed.

## Output Format

Success (`ok: true`):

```json
{
  "ok": true,
  "result": {
    "dps": {
      "TotalDPS": 3362.68,
      "CombinedDPS": 3370.88,
      "FullDPS": 0,
      "FullDotDPS": 0,
      "AverageHit": 2208.69,
      "CritChance": 8.73,
      "CritMultiplier": 2.10,
      "TotalDot": null
    },
    "selected_skill": {
      "skill_id": "Snipe",
      "skill_name": "Snipe",
      "skill_part": 1,
      "stat_set": 1,
      "display_name": "Snipe"
    },
    "available_skills": [...]
  },
  "metadata": {
    "bridge_version": "pob2-headless-mvp-3",
    "calc_method": "BuildOutput",
    "pob2_git_ref": "a82a33b4f",
    "returncode": 0,
    "build_source": "poe_ninja_url",
    "account": "Miwtoo-3415",
    "character": "Miwtoo_ROTA"
  }
}
```

Error (`ok: false`):

```json
{
  "ok": false,
  "error": {
    "type": "runtime_config",
    "message": "POB2_SRC_PATH does not point..."
  },
  "metadata": {
    "bridge_version": "pob2-headless-mvp-3"
  }
}
```

Error types: `runtime_config`, `input_validation`, `unsupported_feature`, `timeout`, `lua_error`, `subprocess`, `bridge_output`, `handler_error`, `pob_export_unavailable`, `invalid_pob_export`.

## Critical Debug Learnings

### 1. `HeadlessWrapper.lua` is required; `Launch.lua` is NOT sufficient

`HeadlessWrapper.lua` sets up headless globals (`_G.build`, `loadBuildFromXML`, `calcsTab`, etc.). `Launch.lua` is for GUI mode only and lacks these. The Python client checks for `HeadlessWrapper.lua` explicitly.

### 2. `loadBuildFromXML(xml, name)` returns nil

This is expected. PoB2's headless wrapper assigns the build to `_G.build`. Always read from `_G.build` after calling `loadBuildFromXML`.

### 3. `calcsTab:BuildOutput()` returns nil by design

The method mutates internal state (`calcsTab.mainOutput`, `calcsTab.mainEnv.player.output`, `calcsTab.calcsOutput`). Never use its return value. Read outputs after calling it.

### 4. Output source priority

```
First:  active_build.calcsTab.mainOutput
Then:   active_build.calcsTab.mainEnv.player.output
```

### 5. `cwd` must be `POB2_SRC_PATH`

The bridge sets `package.path` with **relative** paths (`../runtime/lua/?.lua`). The subprocess must run from `POB2_SRC_PATH` for these to resolve correctly against the PoB2 repo tree.

### 6. `CI=true` env

Required. Skips ModCache loading (which would fail in headless mode). Set in the Python subprocess env.

### 7. Stdout is contaminated; use temp JSON file

PoB2 writes startup messages, dependency counts, etc. to stdout. Never parse stdout. The bridge writes structured JSON to the `--json-out` temp file. Python reads that file after the subprocess exits.

### 8. Missing passive node warnings are non-fatal

You may see during tree load:

```
Warning: Could not find passive node in tree for nodeId <id> in tree version ...
```

These are connector/node warnings from `Classes/PassiveTree.lua`. They indicate PoB2 data may lag the game but they **do not** block calculation.

### 9. `skill_selector` is intentionally rejected for MVP

The bridge always uses the build's saved selected skill. The `selected_skill` metadata is derived from `calcsTab.mainEnv.player.mainSkill`. This can be missing or ambiguous (e.g., when multiple skills are socketed, PoB2 picks the "active" one which may not be what the user expects).

### 10. LuaJIT error `-1073741515` / `0xC0000135`

This is a Windows DLL-not-found error. LuaJIT needs certain VC++ runtime DLLs in its PATH or next to `luajit.exe`. Check:
- Is `luajit.exe` a valid executable? Try running directly.
- Are Visual C++ redistributables installed?
- Is something blocking the process (antivirus, Windows Defender)?

### 11. Restart MCP after env changes

`.env` is loaded once at process start. Changes require a full restart of the MCP server process (or the AI assistant running it).

### 12. `AverageHit` is nested under `output.MainHand.AverageHit` for attack skills

**Issue:** `AverageHit` was always `null` in headless DPS output for attack builds (bows, melee, wands).

**Root cause:** PoB2's `CalcOffence.lua` stores attack average hit inside per-weapon sub-tables of the output, not at the flat `output.AverageHit` key. For attack skills, the value lives at `output.MainHand.AverageHit` (main hand) and optionally `output.OffHand.AverageHit` (dual wield). The top-level `output.AverageHit` key does not exist for attack skills — spells use the flat key.

Confirmed in PoB2 source (`CalcOffence.lua` line 4416, `CalcSections.lua` lines 248/320, spec test `build.calcsTab.mainOutput.MainHand.AverageHit`).

**Fix strategy** (`headless_bridge.lua`):
- Added `nested_field(output, path)` helper that traverses dot-separated sub-table paths (e.g., `"MainHand.AverageHit"`).
- `AverageHit` extraction now cascades: flat `output.AverageHit` → `output.MainHand.AverageHit` → `output.OffHand.AverageHit` → across candidate tables (`mainOutput`, `mainEnv.player.output`, `calcsOutput`) → `output.AverageDamage` → `output.MainHand.AverageDamage`.
- Only `AverageHit` uses the nested fallback. Other DPS fields (`TotalDPS`, `CombinedDPS`, etc.) continue reading from the flat output table. No broad table switch. No formula derivation.

**Real build validation** (Miwtoo_ROTA Deadeye Ice Shot, poe.ninja):
- Before: `AverageHit = null`, all other fields unchanged.
- After: `AverageHit = 2208.69`. `TotalDPS = 3362.68`, `CombinedDPS = 3370.88`, `CritChance = 8.73%`, `CritMultiplier = 2.10` — unchanged.
- Debug diagnostics (`POB2_HEADLESS_DEBUG_OUTPUT=1`) confirmed: `mainOutput_has_AverageHit = false`, `mainOutput_MainHand.AverageHit = 2208.69` in all three output tables. `OffHand` sub-table existed but was empty (bows are two-handed).

**Diagnostics:** Set `POB2_HEADLESS_DEBUG_OUTPUT=1` in the subprocess env to get `metadata.debug_output` in the result. This reports, for each candidate output table:
- Top-level type and keys (capped at 30)
- Presence of `AverageHit`, `MainHand`, `OffHand`, `AverageDamage` at the top level
- If `MainHand` is a table: `MainHand.AverageHit` value, `MainHand.AverageDamage` value, and `MainHand` keys (capped)

**Guardrails:**
- JSON contract: `result.dps.AverageHit` is unchanged. The fix is invisible to callers who never received a value.
- No formula-derived AverageHit — only reads PoB2-computed fields.
- `skill_selector` is a separate concern. Not affected by this change.
- If PoB2 changes the output table structure, update `field_fallback` / `nested_field` candidate tables and paths.

## Validated Outputs

### Ice Shot / Crude Bow (minimal smoke test)

```
TotalDPS=0.37
CombinedDPS=0.37
CritChance=1.25
calc_method=BuildOutput
bridge_version=pob2-headless-mvp-3
```

### Real build (Miwtoo_ROTA Deadeye Ice Shot)

```
TotalDPS=3362.68
CombinedDPS=3370.88
FullDPS=0
FullDotDPS=0
TotalDot=null
AverageHit=2208.69
CritChance=8.73
CritMultiplier=2.10
selected_skill=inferred (Snipe from mainSkill)
bridge_version=pob2-headless-mvp-3
calc_method=BuildOutput
PoB2 git ref=a82a33b4f
fix=average_hit_mainhand_nested (2026-06-15)
```

## Commands

### Run tests

```powershell
python -m pytest tests/test_pob2_headless.py -q
# 29 passed (headless-specific tests)

# All PoB-related tests (headless + import + export):
python -m pytest tests/test_pob2_headless.py tests/test_pob_import_robustness.py tests/test_pob_export_primary_parse.py -q
# 36 passed
```

### Run all PoB-related tests

```powershell
python -m pytest tests/test_pob2_headless.py tests/test_pob_import_robustness.py -q
# 36 passed (headless 29 + import 7)

python -m pytest tests/test_pob2_headless.py tests/test_pob_import_robustness.py tests/test_pob_export_primary_parse.py -q
# 36 passed
```

### Py compile check

```powershell
python -m py_compile src/pob/headless_client.py src/mcp_server.py
```

### Manual bridge smoke test (with real PoB2)

```powershell
$env:POB2_SRC_PATH="C:\path\to\PathOfBuilding-PoE2\src"
$env:LUAJIT_PATH="C:\path\to\luajit.exe"
python -c "
from src.pob.headless_client import calculate_pob2_dps
r = calculate_pob2_dps('path/to/build.xml')
import json; print(json.dumps(r, indent=2))
"
```

### Check PoB2 git ref

```powershell
git -C C:\path\to\PathOfBuilding-PoE2 rev-parse HEAD
```

## Known Limitations / Future Work

| Issue | Description |
|-------|-------------|
| `selected_skill` missing in real results | Bridge derives skill from `mainEnv.player.mainSkill` but this can be nil for complex builds. Need improved extraction (e.g., from `calcsTab.calcsOutput`). |
| ~~`AverageHit` always null~~ | **Resolved (2026-06-15).** Root cause: attack skills store AverageHit under `output.MainHand.AverageHit`, not flat. Fix: `nested_field` fallback in `headless_bridge.lua`. See Critical Debug Learning #12. |
| `skill_selector` unsupported | Cannot choose between Snipe vs Ice Shot. Future: implement exact socket group / skill selection. |
| No golden fixtures | No sample builds with known GUI outputs to validate against. Future: export builds from PoB2 GUI and commit as test fixtures. |
| PoB2 private API drift | `HeadlessWrapper.lua` internals may change. Pin PoB2 commit or add diagnostic checks. |
| Missing passive node warnings | May indicate PoB2 data is stale vs current game patch. Non-fatal but worth monitoring. |
| Windows-only DLL path issues | LuaJIT error `-1073741515` = missing VC++ runtime. Document fix. |
