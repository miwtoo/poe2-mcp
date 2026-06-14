from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pob.headless_client import PoB2HeadlessError, calculate_pob2_dps, resolve_runtime


def _make_runtime(tmp_path: Path) -> tuple[Path, Path]:
    """Create a valid PoB2 runtime dir with HeadlessWrapper.lua (P1 requirement)."""
    pob2_src = tmp_path / "pob2" / "src"
    pob2_src.mkdir(parents=True)
    (pob2_src / "HeadlessWrapper.lua").write_text("-- PoB2 headless\n", encoding="utf-8")
    luajit = tmp_path / ("luajit.exe" if sys.platform == "win32" else "luajit")
    luajit.write_text("test", encoding="utf-8")
    return pob2_src, luajit


def _make_runtime_legacy(tmp_path: Path) -> tuple[Path, Path]:
    """Create a runtime dir with only Launch.lua (GUImode, not headless)."""
    pob2_src = tmp_path / "pob2" / "src"
    pob2_src.mkdir(parents=True)
    (pob2_src / "Launch.lua").write_text("-- PoB2 GUI\n", encoding="utf-8")
    luajit = tmp_path / ("luajit.exe" if sys.platform == "win32" else "luajit")
    luajit.write_text("test", encoding="utf-8")
    return pob2_src, luajit


# ---- Runtime validation ----

def test_resolve_runtime_missing_paths(tmp_path):
    with pytest.raises(PoB2HeadlessError, match="POB2_SRC_PATH"):
        resolve_runtime(pob2_src_path=tmp_path / "missing", luajit_path=tmp_path / "nope")


def test_resolve_runtime_explicit_paths(tmp_path):
    pob2_src, luajit = _make_runtime(tmp_path)
    runtime = resolve_runtime(pob2_src_path=pob2_src, luajit_path=luajit)
    assert runtime.pob2_src_path == pob2_src.resolve()
    assert runtime.luajit_path == luajit.resolve()


def test_resolve_runtime_requires_headless_wrapper(tmp_path):
    """P1: Launch.lua alone is not sufficient; HeadlessWrapper.lua is required."""
    pob2_src, luajit = _make_runtime_legacy(tmp_path)
    with pytest.raises(PoB2HeadlessError, match="HeadlessWrapper.lua"):
        resolve_runtime(pob2_src_path=pob2_src, luajit_path=luajit)


# ---- calculate_pob2_dps existing tests (updated for HeadlessWrapper) ----

def test_calculate_pob2_dps_success_reads_json_file(monkeypatch, tmp_path):
    pob2_src, luajit = _make_runtime(tmp_path)
    xml_file = tmp_path / "build.xml"
    xml_file.write_text("<PathOfBuilding/>", encoding="utf-8")

    def fake_run(command, **kwargs):
        json_out = Path(command[command.index("--json-out") + 1])
        json_out.write_text(
            json.dumps({"ok": True, "result": {"dps": {"TotalDPS": 123.4}}}),
            encoding="utf-8",
        )
        assert kwargs["cwd"] == str(pob2_src.resolve())
        assert kwargs["env"]["CI"] == "true"
        assert kwargs["shell"] is False
        return subprocess.CompletedProcess(command, 0, stdout="PoB startup noise", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = calculate_pob2_dps(
        xml_file,
        pob2_src_path=pob2_src,
        luajit_path=luajit,
        timeout_seconds=5,
    )

    assert result["ok"] is True
    assert result["result"]["dps"]["TotalDPS"] == 123.4
    assert result["metadata"]["returncode"] == 0
    assert "stdout_ignored" in result["metadata"]


def test_calculate_pob2_dps_nonzero_keeps_bridge_error(monkeypatch, tmp_path):
    pob2_src, luajit = _make_runtime(tmp_path)
    xml_file = tmp_path / "build.xml"
    xml_file.write_text("<PathOfBuilding/>", encoding="utf-8")

    def fake_run(command, **kwargs):
        json_out = Path(command[command.index("--json-out") + 1])
        json_out.write_text(
            json.dumps({"ok": False, "error": {"type": "lua_error", "message": "bad xml"}}),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="traceback")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = calculate_pob2_dps(xml_file, pob2_src_path=pob2_src, luajit_path=luajit)

    assert result["ok"] is False
    assert result["error"]["message"] == "bad xml"
    assert result["metadata"]["returncode"] == 1
    assert result["metadata"]["stderr"] == "traceback"


def test_calculate_pob2_dps_timeout(monkeypatch, tmp_path):
    pob2_src, luajit = _make_runtime(tmp_path)
    xml_file = tmp_path / "build.xml"
    xml_file.write_text("<PathOfBuilding/>", encoding="utf-8")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output="noise", stderr="slow")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = calculate_pob2_dps(
        xml_file,
        pob2_src_path=pob2_src,
        luajit_path=luajit,
        timeout_seconds=1,
    )

    assert result["ok"] is False
    assert result["error"]["type"] == "timeout"
    assert "1s" in result["error"]["message"]


# ---- Skill selector / unsupported feature ----

def test_python_client_rejects_skill_selector(monkeypatch, tmp_path):
    """P2: skill_selector rejected before any subprocess/resolution."""
    pob2_src, luajit = _make_runtime(tmp_path)
    xml_file = tmp_path / "build.xml"
    xml_file.write_text("<PathOfBuilding/>", encoding="utf-8")

    # subprocess.run should never be called
    called = []

    def fake_run(command, **kwargs):
        called.append(1)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = calculate_pob2_dps(
        xml_file,
        skill_selector={"socket_group": 1, "skill_index": 1},
        pob2_src_path=pob2_src,
        luajit_path=luajit,
    )

    assert result["ok"] is False
    assert result["error"]["type"] == "runtime_config"
    assert "skill_selector" in result["error"]["message"]
    assert not called  # subprocess never started


@pytest.mark.asyncio
async def test_mcp_handler_rejects_skill_selector():
    """P2: MCP handler rejects skill_selector before any source resolution."""
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({
        "build_xml_path": "a.xml",
        "skill_selector": {"socket_group": 1},
    })

    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "unsupported_feature"
    assert "skill_selector" in payload["error"]["message"]


# ---- MCP handler input validation ----

@pytest.mark.asyncio
async def test_mcp_handler_rejects_multiple_build_sources():
    """Exactly-one-source enforcement includes poe_ninja_url."""
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({
        "build_xml_path": "a.xml",
        "build_xml_content": "<PathOfBuilding/>",
    })

    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "input_validation"


@pytest.mark.asyncio
async def test_mcp_handler_rejects_no_source():
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({})
    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert payload["error"]["type"] == "input_validation"


@pytest.mark.asyncio
async def test_mcp_handler_rejects_xml_path_url():
    """P1: URL passed as build_xml_path is caught before path resolution."""
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({
        "build_xml_path": "https://example.com/build.xml",
    })

    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert "URL" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_handler_poe_ninja_url_not_a_url():
    """poe_ninja_url must be http/https."""
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({
        "poe_ninja_url": "not-a-url",
    })

    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert "scheme" in payload["error"]["message"] or "URL" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_mcp_handler_poe_ninja_url_unparseable():
    """Malformed URL that doesn't match known poe.ninja patterns."""
    from src.mcp_server import PoE2BuildOptimizerMCP

    server = PoE2BuildOptimizerMCP()

    response = await server._handle_calculate_pob2_dps({
        "poe_ninja_url": "https://poe.ninja/not-a-character-page",
    })

    payload = json.loads(response[0].text)
    assert payload["ok"] is False
    assert "account" in payload["error"]["message"]
