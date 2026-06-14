-- PoB2 headless MVP bridge v3.
-- Contract: read XML file, invoke PoB2 under cwd=<PoB2>/src, write JSON file.
-- P0: uses _G.build global; requires HeadlessWrapper.lua only.
-- P1: derives selected skill from calcsTab.mainEnv.player.mainSkill.
-- v3: BuildOutput() returns nil by design; read calcsTab.mainOutput after call.

local BRIDGE_VERSION = "pob2-headless-mvp-3"

local function json_escape(value)
  value = tostring(value)
  value = value:gsub('\\', '\\\\')
  value = value:gsub('"', '\\"')
  value = value:gsub('\b', '\\b')
  value = value:gsub('\f', '\\f')
  value = value:gsub('\n', '\\n')
  value = value:gsub('\r', '\\r')
  value = value:gsub('\t', '\\t')
  value = value:gsub('[%z\1-\31]', function(c)
    return string.format('\\u%04x', string.byte(c))
  end)
  return value
end

local function json_encode(value)
  local value_type = type(value)
  if value_type == "nil" then
    return "null"
  elseif value_type == "boolean" then
    return value and "true" or "false"
  elseif value_type == "number" then
    if value ~= value or value == math.huge or value == -math.huge then
      return "null"
    end
    return tostring(value)
  elseif value_type == "string" then
    return '"' .. json_escape(value) .. '"'
  elseif value_type == "table" then
    local is_array = true
    local max_index = 0
    local count = 0
    for k, _ in pairs(value) do
      count = count + 1
      if type(k) ~= "number" or k < 1 or k % 1 ~= 0 then
        is_array = false
        break
      end
      if k > max_index then max_index = k end
    end
    local parts = {}
    if is_array and max_index == count then
      for i = 1, max_index do
        parts[#parts + 1] = json_encode(value[i])
      end
      return "[" .. table.concat(parts, ",") .. "]"
    end
    for k, v in pairs(value) do
      parts[#parts + 1] = json_encode(tostring(k)) .. ":" .. json_encode(v)
    end
    return "{" .. table.concat(parts, ",") .. "}"
  end
  return json_encode(tostring(value))
end

local function parse_args(argv)
  local opts = {}
  local i = 1
  while i <= #argv do
    local key = argv[i]
    if key == "--xml-file" or key == "--json-out" or key == "--skill-selector" then
      if i == #argv then error("missing value for " .. key) end
      opts[key:sub(3):gsub("-", "_")] = argv[i + 1]
      i = i + 2
    else
      error("unknown argument: " .. tostring(key))
    end
  end
  if not opts.xml_file then error("--xml-file is required") end
  if not opts.json_out then error("--json-out is required") end
  return opts
end

local function read_file(path)
  local file, err = io.open(path, "rb")
  if not file then error("failed to open " .. tostring(path) .. ": " .. tostring(err)) end
  local data = file:read("*a")
  file:close()
  return data
end

local function write_file(path, text)
  local file, err = io.open(path, "wb")
  if not file then error("failed to open JSON output " .. tostring(path) .. ": " .. tostring(err)) end
  file:write(text)
  file:close()
end

local function file_exists(path)
  local file = io.open(path, "rb")
  if file then file:close(); return true end
  return false
end

local function safe_tonumber(value)
  if type(value) == "number" then return value end
  if type(value) == "string" then return tonumber((value:gsub(",", ""))) end
  return nil
end

local function output_field(output, name)
  if type(output) ~= "table" then return nil end
  return safe_tonumber(output[name])
end

local function collect_available_skills(build)
  local result = {}
  local groups = build and build.skillsTab and build.skillsTab.socketGroupList
  if type(groups) ~= "table" then return result end
  for group_index, group in pairs(groups) do
    local entry = {
      socket_group = group_index,
      label = group.label or group.displayLabel or group.name,
      enabled = group.enabled,
      skills = {},
    }
    -- P1: prefer displaySkillList (the visible/usable skills list).
    local skills = group.displaySkillList or group.skillList or group.skills
    if type(skills) == "table" then
      for skill_index, skill in pairs(skills) do
        local active_effect = skill.activeEffect or {}
        local gem_data = active_effect.gemData or skill.gemData or {}
        entry.skills[#entry.skills + 1] = {
          index = skill_index,
          label = skill.label or skill.displayLabel or skill.name or gem_data.name,
          skill_id = skill.skillId or skill.skillID or gem_data.id,
          enabled = skill.enabled,
        }
      end
    end
    result[#result + 1] = entry
  end
  return result
end

local function selected_skill_metadata(build)
  if type(build) ~= "table" then return {} end
  -- P1: derive from calcsTab.mainEnv.player.mainSkill (the skill the calc engine selected).
  local main_env = build.calcsTab and build.calcsTab.mainEnv
  local player = main_env and main_env.player
  local ms = player and player.mainSkill
  if type(ms) == "table" then
    return {
      skill_id = ms.skillId or ms.id,
      skill_name = ms.skillName or ms.name,
      skill_part = ms.skillPart,
      stat_set = ms.skillStatSet,
      display_name = ms.displayName or ms.label,
    }
  end
  -- Fallback: legacy main-slot metadata from build globals.
  return {
    main_socket_group = build.mainSocketGroup,
    main_skill = build.mainSkill,
    main_skill_part = build.mainSkillPart,
    main_skill_stat_set = build.mainSkillStatSet,
  }
end

local function table_keys(t, max_keys)
  if type(t) ~= "table" then return json_encode(t) end
  max_keys = max_keys or 30
  local keys = {}
  local count = 0
  for k, _ in pairs(t) do
    count = count + 1
    if count <= max_keys then
      keys[#keys + 1] = tostring(k)
    end
  end
  if count > max_keys then keys[#keys + 1] = "... (+" .. (count - max_keys) .. " more)" end
  return "[" .. table.concat(keys, ", ") .. "]"
end

local function diagnostics_for_nil_output(active_build)
  local d = {}
  d.build_class = type(active_build)
  d.has_calcsTab = type(active_build.calcsTab)
  if type(active_build.calcsTab) == "table" then
    d.calcsTab_keys = table_keys(active_build.calcsTab)
    d.mainOutput_type = type(active_build.calcsTab.mainOutput)
    d.mainEnv_type = type(active_build.calcsTab.mainEnv)
    if type(active_build.calcsTab.mainEnv) == "table" then
      d.mainEnv_keys = table_keys(active_build.calcsTab.mainEnv)
      if type(active_build.calcsTab.mainEnv.player) == "table" then
        d.mainEnv_player_keys = table_keys(active_build.calcsTab.mainEnv.player)
        d.mainEnv_player_output_type = type(active_build.calcsTab.mainEnv.player.output)
        local ms = active_build.calcsTab.mainEnv.player.mainSkill
        if type(ms) == "table" then
          d.main_skill_name = ms.skillName or ms.name
          d.main_skill_id = ms.skillId or ms.id
        end
      end
    end
  end
  d.has_BuildOutput = type(active_build.calcsTab) == "table" and type(active_build.calcsTab.BuildOutput)
  d.buildFlag = active_build.buildFlag
  d.modFlag = active_build.modFlag
  d.mainSocketGroup = active_build.mainSocketGroup
  if type(active_build.skillsTab) == "table"
    and type(active_build.skillsTab.socketGroupList) == "table" then
    d.socket_group_count = #active_build.skillsTab.socketGroupList
  end
  d.hint = "If calcsTab exists but mainOutput is missing, the build may have no active skill socket group selected. Open the build in PoB2 GUI, select a skill, save, and re-export."
  return d
end

local function launch_pob2()
  package.path = "../runtime/lua/?.lua;../runtime/lua/?/init.lua;" .. package.path
  package.cpath = "../runtime/?.dll;../runtime/?.so;../runtime/?.dylib;" .. package.cpath

  -- P1: require HeadlessWrapper.lua exclusively. Launch.lua is for the GUI;
  -- it does not set up the headless globals this bridge relies on.
  if not file_exists("HeadlessWrapper.lua") then
    error("HeadlessWrapper.lua missing from POB2_SRC_PATH. "
      .. "Ensure POB2_SRC_PATH points to the PathOfBuilding-PoE2/src directory "
      .. "containing HeadlessWrapper.lua (shipped by PoB2, not Launch.lua).")
  end
  dofile("HeadlessWrapper.lua")
end

local function run_calculation(opts)
  if opts.skill_selector and opts.skill_selector ~= "" then
    error("skill_selector is not supported by the MVP bridge; use the build's saved selected skill")
  end

  local xml_text = read_file(opts.xml_file)
  if xml_text == "" then error("XML file is empty") end

  launch_pob2()

  if type(loadBuildFromXML) ~= "function" then error("PoB2 global loadBuildFromXML is unavailable") end

  -- P0: loadBuildFromXML returns nil in PoB2 HeadlessWrapper; the actual
  -- build object is assigned to the global _G.build.
  loadBuildFromXML(xml_text, "poe2-mcp-headless")
  local active_build = _G.build
  if type(active_build) ~= "table" then
    error("PoB2 global build is unavailable after loadBuildFromXML. "
      .. "Expected _G.build to be set by HeadlessWrapper.")
  end

  -- P0: trigger calculation. BuildOutput() returns nil by design (it mutates
  -- calcsTab.mainOutput / mainEnv / calcsEnv / calcsOutput). Call it, then
  -- read calcsTab.mainOutput.
  local output = nil
  local calc_method = "none"

  if type(active_build.calcsTab) == "table"
    and type(active_build.calcsTab.BuildOutput) == "function" then
    -- Prefer BuildOutput() — the canonical PoB2 headless calc path.
    active_build.buildFlag = true
    active_build.modFlag = true

    active_build.calcsTab:BuildOutput()  -- returns nil by design; mutates internals
    calc_method = "BuildOutput"

    output = active_build.calcsTab.mainOutput
      or (active_build.calcsTab.mainEnv
        and active_build.calcsTab.mainEnv.player
        and active_build.calcsTab.mainEnv.player.output)
  else
    -- Fallback: runCallback loop (older PoB2 / non-standard setups).
    active_build.buildFlag = true
    active_build.modFlag = true
    if type(runCallback) == "function" then
      for _ = 1, 3 do
        runCallback("OnFrame")
      end
    end
    calc_method = "runCallback"
    output = active_build.calcsTab and active_build.calcsTab.mainOutput
  end

  if type(output) ~= "table" then
    local diag = diagnostics_for_nil_output(active_build)
    diag.calc_method = calc_method
    error("PoB2 calculation produced no output table; expected calcsTab.mainOutput or calcsTab.mainEnv.player.output. "
      .. "Diagnostics: " .. json_encode(diag))
  end

  local dps = {
    TotalDPS = output_field(output, "TotalDPS"),
    CombinedDPS = output_field(output, "CombinedDPS"),
    FullDPS = output_field(output, "FullDPS"),
    FullDotDPS = output_field(output, "FullDotDPS"),
    AverageHit = output_field(output, "AverageHit"),
    CritChance = output_field(output, "CritChance"),
    CritMultiplier = output_field(output, "CritMultiplier"),
    TotalDot = output_field(output, "TotalDot") or output_field(output, "TotalDotDPS"),
  }

  local has_field = false
  for _, value in pairs(dps) do
    if value ~= nil then has_field = true; break end
  end
  if not has_field then error("missing mainOutput DPS fields") end

  local selected = selected_skill_metadata(active_build)
  local available = collect_available_skills(active_build)

  -- P1: flag when no selected skill is discoverable.
  local selected_ok = false
  for _, _ in pairs(selected) do
    selected_ok = true
    break
  end
  if not selected_ok then
    selected = nil
  end

  return {
    ok = true,
    result = {
      dps = dps,
      selected_skill = selected,
      available_skills = available,
    },
    metadata = {
      bridge_version = BRIDGE_VERSION,
      xml_file = opts.xml_file,
      calc_method = calc_method,
    },
  }
end

local opts
local ok, result = xpcall(function()
  opts = parse_args(arg or {})
  return run_calculation(opts)
end, debug.traceback)

if ok then
  write_file(opts.json_out, json_encode(result))
  os.exit(0)
else
  local payload = {
    ok = false,
    error = {
      type = "lua_error",
      message = tostring(result),
    },
    metadata = {
      bridge_version = BRIDGE_VERSION,
    },
  }
  if opts and opts.json_out then
    local write_ok, write_err = pcall(function() write_file(opts.json_out, json_encode(payload)) end)
    if not write_ok then io.stderr:write(tostring(write_err) .. "\n") end
  end
  io.stderr:write(tostring(result) .. "\n")
  os.exit(1)
end
