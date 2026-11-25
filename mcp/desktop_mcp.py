#!/usr/bin/env python3
"""Small stdio MCP bridge to the existing background Windows UIA script."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _default_home() -> Path:
    env_home = os.environ.get("AUTO_COMPUTER_HOME", "").strip()
    if env_home:
        return Path(env_home)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    return (Path(local_app_data) if local_app_data else Path.home()) / "AutoComputer"


def _resolve_uia_script() -> Path:
    for env_key in ("ANTIGRAVITY_UIA_SCRIPT", "AUTO_COMPUTER_UIA_SCRIPT"):
        val = os.environ.get(env_key, "").strip()
        if val:
            return Path(val)
    repo_candidate = Path(__file__).resolve().parent.parent / "skill" / "auto-computer" / "scripts" / "win_uia.ps1"
    if repo_candidate.is_file():
        return repo_candidate
    return Path.home() / ".codex" / "skills" / "auto-computer" / "scripts" / "win_uia.ps1"


def _resolve_winapp() -> Path | None:
    env_winapp = os.environ.get("AUTO_COMPUTER_WINAPP", "").strip()
    if env_winapp:
        return Path(env_winapp)
    tools_dir = os.environ.get("AUTO_COMPUTER_TOOLS_DIR", "").strip()
    base = Path(tools_dir) if tools_dir else _default_home() / "tools"
    candidate = base / "winapp" / "winapp.exe"
    if candidate.is_file():
        return candidate
    which = shutil.which("winapp") or shutil.which("winapp.exe")
    return Path(which) if which else None


UIA_SCRIPT = _resolve_uia_script()
POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
MAX_ACTIONS = 20
MAX_WAIT_MS = 10_000
PROCESS_TIMEOUT_S = 30
WINAPP = _resolve_winapp()
FOREGROUND_TIMEOUT_S = 45

TOOLS = [
    {
        "name": "list_windows",
        "description": "List current top-level windows through background UI Automation. Optionally filter by exact process name (with or without .exe). Returns HWND, process, class, and bounds; titles are omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {"process": {"type": "string", "description": "Optional exact process name, e.g. Antigravity"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "inspect_window",
        "description": "Inspect bounded UIA controls under an explicit HWND. Selectors may filter by name regex, AutomationId, or control type. For privacy, returned control value/text contents are always omitted.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": ["string", "integer"], "description": "Explicit window handle, decimal or 0x-prefixed hexadecimal"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                "name_regex": {"type": "string"},
                "automation_id": {"type": "string"},
                "control_type": {"type": "string", "description": "UIA control type such as Button, Edit, or Text"},
            },
            "required": ["hwnd"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_actions",
        "description": "Run up to 20 background UIA actions under an explicit HWND. Each action needs a selector (automationId, name, nameRegex, controlType, className, optional index), except wait with ms. Supported ops: set_value(value), invoke, toggle(state on/off), select, expand, collapse, read(key), assert(field name|toggleState|isSelected|automationId|controlType|className|isEnabled|isOffscreen; equals/contains), wait(ms or selector+timeout_ms). Values and text content are always removed from results; assert on value/text is rejected to prevent password disclosure. Legacy __fg_* actions are denied unless allow_foreground=true is explicitly supplied; use foreground_desktop for new foreground input.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": ["string", "integer"]},
                "allow_foreground": {"type": "boolean", "default": False},
                "actions": {"type": "array", "minItems": 1, "maxItems": MAX_ACTIONS, "items": {"type": "object"}},
            },
            "required": ["hwnd", "actions"],
            "additionalProperties": False,
        },
    },
    {
        "name": "foreground_desktop",
        "description": "Foreground-only WinApp control. Every call requires foreground=true plus expected_pid and expected_bounds (the physicalBounds [left,top,right,bottom] from a recent list_windows/screenshot result); stale PID or geometry is rejected. Screenshot uses the target HWND's screen rectangle, so returned coordinates are absolute physical screen pixels. For input actions, expected_foreground must equal the target HWND and the target must already be foreground before input; WinApp may request foreground during handoff. Actions report injection only; take a new screenshot or inspect/read the app to verify the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": ["string", "integer"], "description": "Explicit decimal or 0x-prefixed HWND"},
                "action": {"type": "string", "enum": ["screenshot", "click", "drag", "type", "keys", "scroll"]},
                "foreground": {"type": "boolean", "description": "Must be true to opt into visible foreground screenshot/input"},
                "expected_pid": {"type": "integer", "minimum": 1},
                "expected_bounds": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                "expected_foreground": {"type": ["string", "integer"], "description": "For input only: previously observed foreground HWND; must equal hwnd"},
                "x": {"type": "integer", "minimum": -2000000, "maximum": 2000000},
                "y": {"type": "integer", "minimum": -2000000, "maximum": 2000000},
                "to_x": {"type": "integer", "minimum": -2000000, "maximum": 2000000},
                "to_y": {"type": "integer", "minimum": -2000000, "maximum": 2000000},
                "button": {"type": "string", "enum": ["left", "right", "double"]},
                "text": {"type": "string", "maxLength": 4000},
                "keys": {"type": "string", "maxLength": 128},
                "selector": {"type": "string", "maxLength": 256},
                "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                "wheel": {"type": "integer", "minimum": -20, "maximum": 20},
            },
            "required": ["hwnd", "action", "foreground", "expected_pid", "expected_bounds"],
            "additionalProperties": False,
        },
    },
]


class BridgeError(Exception):
    pass


def _write_message(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _hwnd(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise BridgeError("hwnd must be a decimal integer or 0x-prefixed hexadecimal string")
    raw = str(value).strip()
    try:
        n = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
    except ValueError as exc:
        raise BridgeError("invalid hwnd") from exc
    if n <= 0 or n > 0x7FFFFFFFFFFFFFFF:
        raise BridgeError("hwnd is outside the supported positive handle range")
    return str(n)


def _int_arg(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    value = args.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeError(f"{key} must be an integer from {low} to {high}")
    return value


def _clean_sensitive(obj: Any) -> Any:
    """The source UIA script does not expose IsPassword; never pass contents through."""
    if isinstance(obj, dict):
        return {k: _clean_sensitive(v) for k, v in obj.items() if k.lower() not in {"value", "text"}}
    if isinstance(obj, list):
        return [_clean_sensitive(v) for v in obj]
    return obj


def _selector_present(action: dict[str, Any]) -> bool:
    return any(action.get(k) not in (None, "") for k in ("automationId", "name", "nameRegex", "controlType", "className"))


def _validate_actions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ACTIONS:
        raise BridgeError(f"actions must be an array containing 1 to {MAX_ACTIONS} actions")
    allowed = {"set_value", "invoke", "toggle", "select", "expand", "collapse", "read", "assert", "wait"}
    selectors = {"automationId", "name", "nameRegex", "controlType", "className", "index"}
    clean: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise BridgeError(f"actions[{i}] must be an object")
        action = dict(item)
        op = action.get("op", action.get("action"))
        if not isinstance(op, str) or op.lower() not in allowed:
            raise BridgeError(f"actions[{i}].op must be one of: {', '.join(sorted(allowed))}")
        op = op.lower()
        action["op"] = op
        if op == "wait":
            if "ms" in action:
                _int_arg(action, "ms", 0, 0, MAX_WAIT_MS)
            else:
                if not _selector_present(action):
                    raise BridgeError(f"actions[{i}] wait needs ms or a selector")
                _int_arg(action, "timeout_ms", 5000, 1, MAX_WAIT_MS)
        elif not _selector_present(action):
            raise BridgeError(f"actions[{i}] requires an explicit UIA selector")
        if op == "assert" and action.get("field", "name") in {"value", "text"}:
            raise BridgeError("assert on value/text is disabled because the underlying UIA script cannot identify password controls")
        if op == "set_value" and not isinstance(action.get("value"), str):
            raise BridgeError(f"actions[{i}] set_value requires a string value")
        if op == "toggle" and action.get("state") is not None and str(action["state"]).lower() not in {"on", "off", "true", "false", "1", "0", "checked", "unchecked"}:
            raise BridgeError(f"actions[{i}] toggle state must be on or off")
        if op == "assert" and action.get("field", "name") not in {"name", "toggleState", "isSelected", "automationId", "controlType", "className", "isEnabled", "isOffscreen"}:
            raise BridgeError(f"actions[{i}] assert field is not supported by the privacy-safe bridge")
        # The underlying PowerShell script has an eight-level selector search cap.
        if "index" in action:
            _int_arg(action, "index", 0, 0, 100)
        unknown_selector_keys = set(action) - selectors - {"op", "action", "value", "state", "key", "field", "equals", "contains", "ms", "timeout_ms"}
        if unknown_selector_keys:
            raise BridgeError(f"actions[{i}] has unsupported fields: {', '.join(sorted(unknown_selector_keys))}")
        clean.append(action)
    return clean


def _run_uia(mode: str, args: list[str], actions: list[dict[str, Any]] | None = None, allow_foreground: bool = False) -> dict[str, Any]:
    if not UIA_SCRIPT.is_file():
        raise BridgeError(f"UIA script unavailable: {UIA_SCRIPT}")
    if not POWERSHELL.is_file():
        raise BridgeError(f"Windows PowerShell unavailable: {POWERSHELL}")
    tmp_path: Path | None = None
    try:
        if actions is not None:
            fd, name = tempfile.mkstemp(prefix="codex-uia-", suffix=".json")
            tmp_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"actions": actions}, f, ensure_ascii=False, separators=(",", ":"))
            args.extend(["-ActionsFile", str(tmp_path)])
        command = [str(POWERSHELL), "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", str(UIA_SCRIPT), "-Mode", mode, *args]
        if allow_foreground:
            command.append("-AllowForeground")
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        try:
            proc = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=PROCESS_TIMEOUT_S,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                startupinfo=startupinfo,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise BridgeError(f"UIA operation timed out after {PROCESS_TIMEOUT_S}s") from exc
        stdout = proc.stdout.strip()
        if not stdout:
            detail = proc.stderr.strip() or f"PowerShell exited with code {proc.returncode} without JSON output"
            raise BridgeError(detail)
        try:
            result = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise BridgeError(f"invalid UIA JSON output (exit {proc.returncode}): {stdout[-1200:]}") from exc
        if not isinstance(result, dict):
            raise BridgeError("UIA returned an unexpected result shape")
        result = _clean_sensitive(result)
        if proc.returncode != 0 and result.get("ok") is not False:
            raise BridgeError(result.get("error") or f"PowerShell exited with code {proc.returncode}")
        return result
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _call(name: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise BridgeError("tool arguments must be an object")
    if name == "list_windows":
        process = args.get("process", "")
        if not isinstance(process, str):
            raise BridgeError("process must be a string")
        ps = ["-IncludeUntitled"]
        if process.strip():
            if len(process) > 128 or re.search(r"[\r\n\x00]", process):
                raise BridgeError("invalid process name")
            ps.extend(["-Process", process.strip()])
        result = _run_uia("windows", ps)
        # Titles may include account or document data and aren't needed to identify a window.
        result.pop("windows", None) if result.get("ok") is not True else None
        if result.get("ok"):
            safe_windows = []
            for window in result.get("windows", []):
                item = {k: window.get(k) for k in ("hwnd", "pid", "process", "className", "bounds")}
                bounds = item.get("bounds") or {}
                if all(isinstance(bounds.get(k), int) and not isinstance(bounds.get(k), bool) for k in ("x", "y", "width", "height")):
                    item["physicalBounds"] = [bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]]
                safe_windows.append(item)
            result["windows"] = safe_windows
        return result
    if name == "inspect_window":
        hwnd = _hwnd(args.get("hwnd"))
        depth = _int_arg(args, "max_depth", 5, 1, 8)
        limit = _int_arg(args, "limit", 50, 1, 100)
        ps = ["-Hwnd", hwnd, "-MaxDepth", str(depth), "-Limit", str(limit)]
        for key, flag in (("name_regex", "-Name"), ("automation_id", "-AutomationId"), ("control_type", "-ControlType")):
            val = args.get(key)
            if val is not None:
                if not isinstance(val, str) or len(val) > 256 or re.search(r"[\r\n\x00]", val):
                    raise BridgeError(f"{key} must be a string of at most 256 characters")
                if val:
                    ps.extend([flag, val])
        result = _run_uia("inspect", ps)
        if isinstance(result.get("window"), dict):
            result["window"].pop("title", None)
        return result
    if name == "run_actions":
        hwnd = _hwnd(args.get("hwnd"))
        actions = _validate_actions(args.get("actions"))
        has_legacy_foreground = any(str(a.get("automationId", "")).startswith("__fg_") for a in actions)
        allow_foreground = args.get("allow_foreground", False)
        if not isinstance(allow_foreground, bool):
            raise BridgeError("allow_foreground must be a boolean")
        if has_legacy_foreground and not allow_foreground:
            raise BridgeError("legacy __fg_* actions require explicit allow_foreground=true; prefer foreground_desktop")
        return _run_uia("batch", ["-Hwnd", hwnd], actions, allow_foreground=allow_foreground)
    if name == "foreground_desktop":
        return _foreground_desktop(args)
    raise BridgeError(f"unknown tool: {name}")


def _window_state(hwnd: int) -> dict[str, Any]:
    """Read the exact target's PID, visibility, foreground state, and physical bounds."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    handle = wintypes.HWND(hwnd)
    if not user32.IsWindow(handle):
        raise BridgeError("target HWND is no longer a window")
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    rect = wintypes.RECT()
    if not user32.GetWindowRect(handle, ctypes.byref(rect)):
        raise BridgeError("could not read target window bounds")
    bounds = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise BridgeError("target window has empty bounds")
    return {
        "hwnd": hwnd,
        "pid": int(pid.value),
        "visible": bool(user32.IsWindowVisible(handle)),
        "minimized": bool(user32.IsIconic(handle)),
        "foregroundHwnd": int(user32.GetForegroundWindow() or 0),
        "bounds": bounds,
    }


def _winapp_run(argv: list[str]) -> str:
    winapp = WINAPP or _resolve_winapp()
    if not winapp or not winapp.is_file():
        raise BridgeError("WinApp CLI unavailable; set AUTO_COMPUTER_WINAPP or install winapp.exe into %LOCALAPPDATA%\\AutoComputer\\tools\\winapp")
    try:
        proc = subprocess.run(
            [str(winapp), *argv], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=FOREGROUND_TIMEOUT_S, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise BridgeError(f"WinApp timed out after {FOREGROUND_TIMEOUT_S}s") from exc
    detail = (proc.stdout + "\n" + proc.stderr).strip()
    if proc.returncode:
        raise BridgeError(f"WinApp exited {proc.returncode}: {detail[-1200:]}")
    return detail[-2000:]


def _foreground_desktop(args: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(args.get("hwnd"), (str, int)) or isinstance(args.get("hwnd"), bool):
        raise BridgeError("hwnd must be an explicit decimal or hexadecimal window handle")
    hwnd = int(_hwnd(args["hwnd"]))
    action = args.get("action")
    if args.get("foreground") is not True:
        raise BridgeError("foreground must be the explicit boolean true")
    expected_pid = _int_arg(args, "expected_pid", 0, 1, 0x7FFFFFFF)
    expected_bounds = args.get("expected_bounds")
    if not isinstance(expected_bounds, list) or len(expected_bounds) != 4 or any(isinstance(v, bool) or not isinstance(v, int) for v in expected_bounds):
        raise BridgeError("expected_bounds must be the four integer physical bounds from a recent observation")
    if action not in {"screenshot", "click", "drag", "type", "keys", "scroll"}:
        raise BridgeError("unsupported foreground action")
    before = _window_state(hwnd)
    if before["pid"] != expected_pid or before["bounds"] != expected_bounds:
        raise BridgeError(f"stale target observation; expected pid/bounds {expected_pid}/{expected_bounds}, current {before['pid']}/{before['bounds']}")
    if not before["visible"] or before["minimized"]:
        raise BridgeError("target must be visible and not minimized; inspect or restore it first")
    expected_foreground = args.get("expected_foreground")
    if action != "screenshot":
        if expected_foreground is None or _hwnd(expected_foreground) != str(hwnd):
            raise BridgeError("input requires expected_foreground equal to hwnd from the latest observation")
        if before["foregroundHwnd"] != hwnd:
            raise BridgeError("foreground changed since observation; re-observe before input")
    left, top, right, bottom = before["bounds"]

    def point(xkey: str, ykey: str) -> str:
        x = _int_arg(args, xkey, -2_000_001, -2_000_000, 2_000_000)
        y = _int_arg(args, ykey, -2_000_001, -2_000_000, 2_000_000)
        if not (left <= x < right and top <= y < bottom):
            raise BridgeError(f"{xkey}/{ykey} must lie within target bounds {before['bounds']}")
        return f"{x},{y}"

    root = ["ui"]
    target = ["-w", str(hwnd)]
    image_data = None
    if action == "screenshot":
        fd, out_path = tempfile.mkstemp(prefix="codex-winapp-", suffix=".png")
        os.close(fd)
        try:
            os.unlink(out_path)
            cmd = root + ["screenshot", *target, "--capture-screen", "--json", "-o", out_path]
            output = _winapp_run(cmd)
            png_path = Path(out_path)
            if not png_path.is_file():
                raise BridgeError(f"WinApp reported success but screenshot file is missing: {output}")
            payload = png_path.read_bytes()
            if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
                raise BridgeError("WinApp screenshot is not a valid PNG")
            width, height = struct.unpack(">II", payload[16:24])
            image_data = payload
        finally:
            try:
                os.unlink(out_path)
            except OSError:
                pass
        after = _window_state(hwnd)
        expected_size = [right - left, bottom - top]
        if after["pid"] != before["pid"] or after["bounds"] != before["bounds"] or after["foregroundHwnd"] != hwnd:
            raise BridgeError("target PID, geometry, or foreground changed during screenshot; discard this capture and re-observe")
        if [width, height] != expected_size:
            raise BridgeError(f"screen crop dimensions {width}x{height} do not match target bounds {expected_size[0]}x{expected_size[1]}; coordinates are unavailable")
        return {"ok": True, "action": action, "target": after, "screenBounds": after["bounds"], "png": image_data, "width": width, "height": height, "effectVerified": False}

    if action == "click":
        button = args.get("button", "left")
        if button not in {"left", "right", "double"}:
            raise BridgeError("button must be left, right, or double")
        cmd = root + ["click", "--at", point("x", "y"), *target]
        if button == "right":
            cmd.append("--right")
        elif button == "double":
            cmd.append("--double")
    elif action == "drag":
        cmd = root + ["drag", point("x", "y"), point("to_x", "to_y"), *target]
    elif action == "type":
        value = args.get("text")
        if not isinstance(value, str) or not value or len(value) > 4000:
            raise BridgeError("type requires non-empty text of at most 4000 characters")
        cmd = root + ["send-keys", value, *target, "--verbatim", "--via", "send-input"]
    elif action == "keys":
        value = args.get("keys")
        if not isinstance(value, str) or not value or len(value) > 128 or re.search(r"[\r\n\x00]", value):
            raise BridgeError("keys requires a shortcut of at most 128 characters")
        cmd = root + ["send-keys", value, *target, "--via", "send-input"]
    else:
        selector = args.get("selector")
        if not isinstance(selector, str) or not selector or len(selector) > 256 or re.search(r"[\r\n\x00]", selector):
            raise BridgeError("scroll requires an explicit UIA selector inside the target window")
        cmd = root + ["scroll", selector, *target]
        if "wheel" in args:
            amount = _int_arg(args, "wheel", 0, -20, 20)
            if amount == 0:
                raise BridgeError("wheel must be non-zero")
            cmd += ["--wheel", str(amount)]
        else:
            direction = args.get("direction")
            if direction not in {"up", "down", "left", "right"}:
                raise BridgeError("scroll needs direction up/down/left/right or a non-zero wheel value")
            cmd += ["--direction", direction]

    output = _winapp_run(cmd)
    after = _window_state(hwnd)
    if after["pid"] != before["pid"]:
        raise BridgeError("target HWND changed process during action")
    return {"ok": True, "action": action, "targetBefore": before, "targetAfter": after, "winappOutput": output, "injectionAccepted": True, "effectVerified": False}


def _tool_result(value: Any, is_error: bool = False) -> dict[str, Any]:
    if isinstance(value, dict) and isinstance(value.get("png"), bytes):
        image = value.pop("png")
        metadata = {k: v for k, v in value.items() if k != "png"}
        return {"content": [{"type": "text", "text": json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))}, {"type": "image", "data": __import__("base64").b64encode(image).decode("ascii"), "mimeType": "image/png"}], "structuredContent": metadata, "isError": is_error}
    content = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return {"content": [{"type": "text", "text": content}], "structuredContent": value, "isError": is_error}


def _handle(req: dict[str, Any]) -> dict[str, Any] | None:
    method = req.get("method")
    ident = req.get("id")
    if method == "notifications/initialized" or (isinstance(method, str) and method.startswith("notifications/")):
        return None
    if method == "initialize":
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        version = params.get("protocolVersion", "2024-11-05")
        return {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "antigravity-desktop-bridge", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": ident, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": ident, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = req.get("params") if isinstance(req.get("params"), dict) else {}
        try:
            result = _call(params.get("name", ""), params.get("arguments", {}))
            return {"jsonrpc": "2.0", "id": ident, "result": _tool_result(result, is_error=result.get("ok") is False)}
        except BridgeError as exc:
            return {"jsonrpc": "2.0", "id": ident, "result": _tool_result({"ok": False, "error": str(exc)}, is_error=True)}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": ident, "result": _tool_result({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, is_error=True)}
    if ident is None:
        return None
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main() -> int:
    # The protocol stream is stdout only. A malformed line gets a JSON-RPC parse error.
    # MCP stdio uses UTF-8 JSON-RPC regardless of the Windows console code page.
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            if not isinstance(req, dict) or req.get("jsonrpc") != "2.0":
                raise ValueError("request must be a JSON-RPC 2.0 object")
            response = _handle(req)
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
        if response is not None:
            _write_message(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
