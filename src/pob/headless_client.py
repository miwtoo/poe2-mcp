"""Headless Path of Building 2 subprocess client.

The bridge contract is file-based: PoB XML in, JSON result out. PoB2 startup
can write arbitrary text to stdout, so callers must only trust the JSON file.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Union

try:
    from ..config import BASE_DIR
except ImportError:  # pragma: no cover - direct execution fallback
    from src.config import BASE_DIR


BRIDGE_VERSION = "pob2-headless-mvp-3"
BRIDGE_PATH = Path(__file__).with_name("headless_bridge.lua").resolve()
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 300.0
MAX_XML_CONTENT_BYTES = 10000000  # ~10 MB
MAX_SHARE_CODE_BYTES = 2000000  # ~2 MB (compressed XML)
MAX_XML_FILE_BYTES = 50000000  # ~50 MB


class PoB2HeadlessError(ValueError):
    """Runtime configuration or invocation error."""


@dataclass(frozen=True)
class RuntimeConfig:
    pob2_src_path: Path
    luajit_path: Path


def _candidate_luajit_paths() -> list[Path]:
    candidates = [
        BASE_DIR / "runtime" / "luajit" / "luajit.exe",
        BASE_DIR / "runtime" / "luajit" / "luajit",
        BASE_DIR / "runtime" / "luajit.exe",
        BASE_DIR / "runtime" / "luajit",
    ]
    found = shutil.which("luajit")
    if found:
        candidates.append(Path(found))
    return candidates


def _resolve_existing_dir(path_value: str, label: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_dir():
        raise PoB2HeadlessError(f"{label} does not point to an existing directory: {path}")
    return path


def _resolve_existing_file(path_value: str, label: str) -> Path:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise PoB2HeadlessError(f"{label} does not point to an existing file: {path}")
    return path


def resolve_runtime(
    pob2_src_path: Optional[Union[str, Path]] = None,
    luajit_path: Optional[Union[str, Path]] = None,
) -> RuntimeConfig:
    """Resolve and validate PoB2 source directory and LuaJIT executable.

    Environment variables are honored first when explicit values are not given:
    ``POB2_SRC_PATH`` and ``LUAJIT_PATH``. Local runtime defaults are used if
    present under ``BASE_DIR/runtime``.
    """

    pob2_value = str(pob2_src_path or os.environ.get("POB2_SRC_PATH") or "")
    if pob2_value:
        pob2_src = _resolve_existing_dir(pob2_value, "POB2_SRC_PATH")
    else:
        default_pob2 = BASE_DIR / "runtime" / "pob2" / "src"
        if default_pob2.is_dir():
            pob2_src = default_pob2.resolve()
        else:
            raise PoB2HeadlessError(
                "PoB2 runtime not found. Set POB2_SRC_PATH to the "
                "PathOfBuilding-PoE2/src directory or install it at "
                f"{default_pob2}. Use calculate_character_dps for manual estimates."
            )

    # P1: require HeadlessWrapper.lua exclusively. Launch.lua is the GUI
    # entry point and does not set up the headless globals this bridge needs.
    if not (pob2_src / "HeadlessWrapper.lua").is_file():
        raise PoB2HeadlessError(
            f"POB2_SRC_PATH is not a PoB2 headless src directory: {pob2_src}. "
            "Expected HeadlessWrapper.lua (shipped by PathOfBuilding-PoE2). "
            "Launch.lua alone is insufficient — it does not set up the "
            "headless build/calc globals used by this bridge."
        )

    luajit_value = str(luajit_path or os.environ.get("LUAJIT_PATH") or "")
    if luajit_value:
        luajit = _resolve_existing_file(luajit_value, "LUAJIT_PATH")
    else:
        luajit = next((p.resolve() for p in _candidate_luajit_paths() if p.is_file()), None)
        if luajit is None:
            raise PoB2HeadlessError(
                "LuaJIT runtime not found. Set LUAJIT_PATH to luajit/luajit.exe "
                f"or install it under {BASE_DIR / 'runtime' / 'luajit'}."
            )

    if not BRIDGE_PATH.is_file():
        raise PoB2HeadlessError(f"PoB2 headless bridge script missing: {BRIDGE_PATH}")

    return RuntimeConfig(pob2_src_path=pob2_src, luajit_path=luajit)


def _error_result(error_type: str, message: str, **extra: Any) -> Dict[str, Any]:
    error = {"type": error_type, "message": message}
    error.update({k: v for k, v in extra.items() if v not in (None, "")})
    return {
        "ok": False,
        "error": error,
        "metadata": {
            "bridge_version": BRIDGE_VERSION,
            "bridge_path": str(BRIDGE_PATH),
        },
    }


def _coerce_timeout(timeout_seconds: Optional[Union[float, int]]) -> float:
    if timeout_seconds is None:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError):
        raise PoB2HeadlessError("timeout_seconds must be a number")
    if timeout <= 0:
        raise PoB2HeadlessError("timeout_seconds must be greater than 0")
    if timeout > MAX_TIMEOUT_SECONDS:
        raise PoB2HeadlessError(f"timeout_seconds must be <= {MAX_TIMEOUT_SECONDS:g}")
    return timeout


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PoB2HeadlessError("PoB2 bridge did not write a JSON result file") from exc
    except json.JSONDecodeError as exc:
        raise PoB2HeadlessError(f"PoB2 bridge wrote invalid JSON: {exc}") from exc


def _read_pob2_git_ref(pob2_src: Path) -> Optional[str]:
    git_dir = pob2_src.parent / ".git"
    head = git_dir / "HEAD"
    try:
        head_text = head.read_text(encoding="utf-8").strip()
        if head_text.startswith("ref:"):
            ref_path = git_dir / head_text.split(" ", 1)[1]
            return ref_path.read_text(encoding="utf-8").strip()[:40]
        return head_text[:40]
    except OSError:
        return None


def calculate_pob2_dps(
    xml_file: Union[str, Path],
    *,
    skill_selector: Optional[dict[str, Any]] = None,
    timeout_seconds: Optional[Union[float, int]] = None,
    pob2_src_path: Optional[Union[str, Path]] = None,
    luajit_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Run PoB2 headless calculation for a PoB XML file.

    Returns a structured dict. Runtime/process/XML errors are represented as
    ``{"ok": false, "error": ...}`` rather than raised, so MCP handlers can
    return deterministic JSON to clients.
    """

    try:
        # P2: reject skill_selector early before any subprocess.
        if skill_selector is not None:
            raise PoB2HeadlessError(
                "skill_selector is not supported by the MVP PoB2 headless bridge. "
                "The bridge uses the build's saved selected skill. Explicit skill "
                "selection will be added when PoB2 skill internals are stabilized."
            )

        runtime = resolve_runtime(pob2_src_path=pob2_src_path, luajit_path=luajit_path)
        timeout = _coerce_timeout(timeout_seconds)
        xml_path = Path(xml_file).expanduser().resolve()
        if not xml_path.is_file():
            raise PoB2HeadlessError(f"build_xml_path does not point to a file: {xml_path}")
        # P2: size limit for XML file
        file_size = xml_path.stat().st_size
        if file_size > MAX_XML_FILE_BYTES:
            raise PoB2HeadlessError(
                f"build XML file exceeds size limit ({file_size:_d} > {MAX_XML_FILE_BYTES:_d} bytes). "
                f"PoB builds should be well under ~50 MB."
            )
    except PoB2HeadlessError as exc:
        return _error_result("runtime_config", str(exc))

    json_out_handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".json", prefix="pob2-headless-", delete=False
    )
    json_out = Path(json_out_handle.name)
    json_out_handle.close()
    try:
        json_out.unlink(missing_ok=True)
    except TypeError:  # Python 3.9 compatibility for tests using old runtimes
        if json_out.exists():
            json_out.unlink()

    command = [
        str(runtime.luajit_path),
        str(BRIDGE_PATH),
        "--xml-file",
        str(xml_path),
        "--json-out",
        str(json_out),
    ]
    if skill_selector is not None:
        command.extend([
            "--skill-selector",
            json.dumps(skill_selector, separators=(",", ":"), sort_keys=True),
        ])

    env = os.environ.copy()
    env["CI"] = "true"

    try:
        completed = subprocess.run(
            command,
            cwd=str(runtime.pob2_src_path),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return _error_result(
            "timeout",
            f"PoB2 headless calculation timed out after {timeout:g}s",
            stdout=exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout,
            stderr=exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr,
        )
    except OSError as exc:
        return _error_result("subprocess", f"Failed to start LuaJIT/PoB2 subprocess: {exc}")

    try:
        payload = _read_json_file(json_out)
    except PoB2HeadlessError as exc:
        return _error_result(
            "bridge_output",
            str(exc),
            returncode=completed.returncode,
            stderr=completed.stderr,
            stdout=completed.stdout,
        )
    finally:
        try:
            json_out.unlink(missing_ok=True)
        except TypeError:  # Python 3.9 compatibility
            if json_out.exists():
                json_out.unlink()

    metadata = payload.setdefault("metadata", {})
    metadata.update(
        {
            "bridge_version": BRIDGE_VERSION,
            "bridge_path": str(BRIDGE_PATH),
            "pob2_src_path": str(runtime.pob2_src_path),
            "luajit_path": str(runtime.luajit_path),
            "pob2_git_ref": _read_pob2_git_ref(runtime.pob2_src_path),
            "returncode": completed.returncode,
        }
    )
    if completed.stderr:
        metadata["stderr"] = completed.stderr[-4000:]
    if completed.stdout:
        metadata["stdout_ignored"] = completed.stdout[-4000:]

    if completed.returncode != 0:
        payload["ok"] = False
        payload.setdefault(
            "error",
            {
                "type": "lua_error",
                "message": f"PoB2 bridge exited with code {completed.returncode}",
            },
        )

    return payload
