# PoB2 Headless Integration Plan

## Objective
Replace the current custom damage calculator in `poe2-mcp` with Path of Building 2's official calculation engine via headless LuaJIT subprocess calls.

## Background
Current calculator (`spell_dps_calculator.py`, `damage_calculator.py`) uses custom formulas with hardcoded values and heuristics. PoB2 has a proven community calculation engine. PoB2 provides `HeadlessWrapper.lua` which initializes the full calc engine without GUI.

## Architecture

```
MCP Tool Request
    ↓
poe2-mcp Python handler
    ↓
Spawn subprocess: luajit HeadlessWrapper.lua <args>
    ↓
Lua script loads build XML → triggers calc → writes JSON to temp file
    ↓
Python reads temp file → returns to MCP client
```

**Critical:** `HeadlessWrapper.lua` does `dofile("Launch.lua")` relative to cwd. The subprocess **must** run with `cwd=<PoB2>/src`. Bridge script lives inside poe2-mcp repo but is invoked from PoB2 source directory.

## Prerequisites

1. **PoB2 Runtime**: Clone `PathOfBuildingCommunity/PathOfBuilding-PoE2` to `runtime/pob2/` (shallow clone: `git clone --depth 1`).
2. **LuaJIT**: Install via `winget install DEVCOM.LuaJIT` (Windows) or download from https://luajit.org/download.html. Copy to `runtime/luajit/`.
3. **Lua Path Setup**: Before `dofile("Launch.lua")`, bridge script must configure:
   ```lua
   package.path = "../runtime/lua/?.lua;../runtime/lua/?/init.lua;" .. package.path
   package.cpath = "../runtime/?.dll;" .. package.cpath
   ```
   This resolves `require("xml")` and other PoB2 runtime modules.
4. **Build XML**: User provides build file path, share code, or raw XML. No sample builds exist in PoB2 repo — must create test fixtures or use real builds.

## Phase 0: Manual Spike ✅ COMPLETE

**Status:** Phase 0 spike completed successfully. PoB2 headless engine loads and calculates.

**What was proven:**

| Step | Result |
|---|---|
| Clone PoB2 | ✅ Shallow clone, 1884 files to `runtime/pob2/` |
| LuaJIT install | ✅ v2.1.1720049189 via winget, copied to `runtime/luajit/` |
| Path setup | ✅ Fixed `module 'xml' not found` by adding `../runtime/lua/?.lua` and `../runtime/?.dll` |
| Full engine load | ✅ All modules load: Common, Data, Build, Calcs, all tabs |
| Build load | ✅ `loadBuildFromXML()` and `newBuild()` both work |
| DPS extraction | ✅ `build.calcsTab.mainOutput.TotalDPS` returns 0.37 (baseline unarmed) |

**Key finding:** `xml.lua` is pure Lua (no C dependencies). The path fix is simple — prepend PoB2's runtime directories to `package.path` and `package.cpath` before `dofile("Launch.lua")`.

**Files created in Phase 0:**
- `runtime/pob2/src/run_headless.lua` — working entry point with path setup
- `runtime/pob2/src/test_paths.lua` — path verification reference

## Implementation Steps

### Phase 1: Bridge Script (Lua)

Create `src/pob/headless_bridge.lua` inside poe2-mcp repo.

Responsibilities:
- Parse CLI arguments: `--xml-file <path>`, `--json-out <path>`, optional `--skill-selector <json>`.
- Set Lua paths **before** `dofile("Launch.lua")`:
  ```lua
  package.path = "../runtime/lua/?.lua;../runtime/lua/?/init.lua;" .. package.path
  package.cpath = "../runtime/?.dll;" .. package.cpath
  ```
- Load build via `loadBuildFromXML(xmlText, name)`.
- Use build's **saved selected skill** by default. If `--skill-selector` provided, attempt exact selection (socket group, active skill index, stat set, skill part).
- Trigger calculation: `runCallback("OnFrame")` after `build.modFlag = true`.
- Extract scalar results from `build.calcsTab.mainOutput`:
  - `TotalDPS` (hit DPS)
  - `CombinedDPS`
  - `FullDPS`
  - `AverageHit`
  - `CritChance`
  - `CritMultiplier`
  - `TotalDot` / `TotalDotDPS` (if available)
- Write compact JSON to `--json-out` path. Do **not** print JSON to stdout (stdout is contaminated by PoB2 startup messages).
- Exit 0 on success, non-zero on error with stderr message.

Key Lua API surface:
```lua
build.calcsTab.mainOutput.TotalDPS      -- hit DPS only
build.calcsTab.mainOutput.CombinedDPS   -- combined hit + dot
build.calcsTab.mainOutput.FullDPS       -- full DPS including minions
build.calcsTab.mainOutput.AverageHit
build.calcsTab.mainOutput.CritChance
build.calcsTab.mainOutput.CritMultiplier
```

### Phase 2: Python Subprocess Handler

Create `src/pob/headless_client.py`.

Responsibilities:
- Validate `POB2_SRC_PATH` (PoB2 runtime `src/` directory) and `LUAJIT_PATH`.
- Construct subprocess call with `cwd=POB2_SRC_PATH`:
  ```
  luajit <path_to_headless_bridge.lua> --xml-file <temp_xml> --json-out <temp_json>
  ```
- Set env var `CI=true` to skip ModCache loading.
- Capture **stderr** for diagnostics (stdout is ignored due to PoB2 contamination).
- Read JSON from `--json-out` temp file after subprocess exits.
- Handle timeouts (default 30s, configurable). Kill subprocess on timeout.
- Return structured dict to MCP handler.
- Include PoB2 version/commit hash, bridge version, selected skill metadata in response.

Error handling:
- Runtime not found → clear error message.
- Lua error / non-zero exit → capture stderr, return error.
- Timeout → kill subprocess, return error.
- Missing active skill → return error with available skill list.

### Phase 3: Golden Validation

Before exposing to MCP, validate bridge accuracy against PoB2 GUI.

1. Create 3 fixture builds covering different archetypes (spell, attack, minion). No sample builds exist in PoB2 repo — must create from scratch or export from PoB2 GUI.
2. For each build, record GUI-displayed values: selected skill name, TotalDPS, CombinedDPS, AverageHit, CritChance.
3. Run `headless_client.py` against same XML.
4. Compare outputs. Expected: **exact match** except GUI rounding differences.
5. Test error cases:
   - Missing runtime
   - Invalid XML
   - No active skill in build
   - Calculation timeout

Success criteria:
- Selected skill name/index matches GUI.
- Displayed fields match GUI rounded values for all 3 fixture builds.
- No JSON parse corruption under startup warnings.
- Cold run completes in <20s.
- Clear, deterministic errors for all failure modes.

### Phase 4: MCP Tool Registration

Only after Phase 3 proves deterministic bridge.

Modify `mcp_server.py`:

1. Add new tool schema: `calculate_pob2_dps`.
2. Input fields:
   - `build_xml_path` (string, optional)
   - `build_share_code` (string, optional)
   - `build_xml_content` (string, optional)
   - `skill_selector` (object, optional) — exact selector, not fuzzy name
3. Handler `_handle_calculate_pob2_dps`:
   - Resolve build source (path > share code decode > raw XML).
   - Write resolved XML to temp file.
   - Call `headless_client.py`.
   - Read JSON result.
   - Clean up temp files.
   - Return structured result with DPS fields, selected skill metadata, PoB2 version.

### Phase 5: Advanced Features (Post-MVP)

After stable MCP tool:
- Config overrides (boss, ailments, charges, exposure, weapon set, flasks)
- Full `actor.breakdown` tree
- Cache unchanged builds
- Batch skill comparison
- Auto-import from PoE2 API via OAuth (reuse existing `poe_api.py`)

## File Changes

| File | Action |
|------|--------|
| `runtime/pob2/src/run_headless.lua` | Created in Phase 0 |
| `src/pob/headless_bridge.lua` | Create (Phase 1) |
| `src/pob/headless_client.py` | Create (Phase 2) |
| `mcp_server.py` | Add tool schema + handler (Phase 4 only) |
| `tests/test_pob2_headless.py` | Create (Phase 3 validation) |
| `README.md` | Update docs |

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| LuaJIT + C module setup hell on Windows | Use winget `DEVCOM.LuaJIT`; pin version; test on clean Windows install |
| PoB2 runtime cwd requirement | Enforce `cwd=POB2_SRC_PATH` in subprocess; validate path exists |
| stdout contamination from PoB2 startup | Write JSON to temp file; capture stderr only |
| PoB2 private API drift | Pin PoB2 commit/version; update bridge on breaking changes |
| Lua module mismatch (xml, base64, sha1, etc.) | `xml.lua` is pure Lua; bundle other required modules with runtime; test in CI |
| Build XML format changes | Validate XML against PoB2 before loading; version check |
| No sample builds in PoB2 repo | Create test fixtures from PoB2 GUI exports; document procedure |
| Config override complexity | Defer to Phase 5; MVP uses saved build config only |
| Concurrency (multiple LuaJIT instances) | Limit max concurrent subprocesses; queue excess requests |
| Temp file hygiene | Use `tempfile` module; always clean up in `finally` block |
| Security (file path validation) | Validate paths are within allowed directories; no shell injection |
| Calculation slow (>30s) | Make timeout configurable; warn on slow builds |
| Windows-only PoB2 binary | LuaJIT headless works cross-platform; Linux needs Wine for GUI only |
| Existing exporter is incomplete | Use importer only; do not rely on `exporter.py` for real builds |

## Success Criteria

- `calculate_pob2_dps` returns DPS values that match PoB2 GUI rounded values for 3 fixture builds.
- Selected skill name/index matches GUI.
- No JSON parse corruption under startup warnings.
- Cold run completes in <20s for typical builds.
- Clear, deterministic error messages for: missing runtime, bad XML, no active skill, timeout.
- Custom calculator (`calculate_character_dps`) remains as separate **estimate tool**, not transparent fallback.

## Fallback Strategy

The existing `calculate_character_dps` tool takes aggregated spell inputs (spell stats, modifiers, enemy stats). PoB2 takes build XML. These are **different contracts** — transparent fallback is not practical.

If PoB2 runtime is unavailable:
- Return clear error: "PoB2 runtime not found. Install from ..."
- Suggest: "Use `calculate_character_dps` with manual inputs for an estimate."

Do not attempt to lossily extract modifiers from XML to feed the custom calculator.

## Future Enhancements

- Cache `actor.output` to avoid re-calculating unchanged builds.
- Support batch skill comparison (multiple skills in one call).
- Expose full `actor.breakdown` tree for detailed analysis.
- Auto-import from PoE2 API via OAuth (reuse existing `poe_api.py`).
- Dockerized PoB2 runtime for consistent deployment.

---

## Implementation Status (MVP Complete)

**Status:** `calculate_pob2_dps` tool is registered and functional. All Phase 1–4 implementation steps are complete. Phase 5 (advanced features) remains future work.

### Built Files

| File | Role |
|------|------|
| `src/pob/headless_bridge.lua` | Lua entry point — parses CLI args, loads PoB2 via HeadlessWrapper.lua, runs `BuildOutput()`, writes structured JSON to temp file |
| `src/pob/headless_client.py` | Python subprocess wrapper — resolves runtime paths, spawns LuaJIT with `cwd=POB2_SRC_PATH`, reads JSON result, enriches metadata |
| `src/mcp_server.py` (`_handle_calculate_pob2_dps`) | MCP tool handler — accepts exactly one of `build_xml_path`, `build_xml_content`, `build_share_code`, `poe_ninja_url`; resolves build source; delegates to headless_client |
| `tests/test_pob2_headless.py` | 29 tests covering runtime validation, subprocess mock, MCP handler input validation, skill_selector rejection |
| `pyproject.toml` | `[tool.setuptools.package-data]` includes `pob/*.lua` for bridge distribution |

### Final Critical Gotchas

1. **`loadBuildFromXML()` returns nil** — active build is `_G.build`, not the return value.
2. **`calcsTab:BuildOutput()` returns nil by design** — it mutates `calcsTab.mainOutput`. Read outputs after calling it.
3. **cwd must be `POB2_SRC_PATH`** — the bridge sets relative `package.path` entries.
4. **Stdout is contaminated** — PoB2 writes startup logs to stdout. JSON goes to temp file.
5. **`CI=true` env** — required to skip ModCache loading in headless mode.
6. **Restart MCP after `.env` changes** — env loads at process start only.

### Operational Runbook

See `.docs/notes/pob2-headless-engineering-notes.md` for setup, flow, tool input examples, known errors, debug lessons, and validated outputs.
