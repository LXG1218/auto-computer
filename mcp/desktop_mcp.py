#!/usr/bin/env python3
"""Lightweight stdio MCP bridge for background Windows UIA and guarded foreground handoffs."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
import atexit
import json
import os
import queue
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator


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
UIA_WORKER = UIA_SCRIPT.with_name("win_uia_worker.py")
GUARD_SCRIPT = Path(__file__).resolve().with_name("foreground_guard.py")
if str(UIA_SCRIPT.resolve().parent) not in sys.path:
    sys.path.insert(0, str(UIA_SCRIPT.resolve().parent))
from desktop_policy import launch_registered_target

POWERSHELL = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
MAX_ACTIONS = 20
MAX_WAIT_MS = 10_000
PROCESS_TIMEOUT_S = 30
DAEMON_BOOT_TIMEOUT_S = 15
WINAPP = _resolve_winapp()
FOREGROUND_TIMEOUT_S = 45
DEFAULT_MAX_BYTES = 12_000
NOISE_WINDOW_CLASSES = {"UAC_InputIndicatorOverlayWnd"}
_TREE_CACHE: OrderedDict[str, tuple[str, list[dict[str, Any]]]] = OrderedDict()
_TREE_SEQ = 0

TOOLS = [
    {
        "name": "launch_registered_app",
        "description": "Launch an operator-qualified app entry from %AUTO_COMPUTER_HOME%\\adapters\\launch with SW_SHOWMINNOACTIVE.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string", "minLength": 1, "maxLength": 64}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_windows",
        "description": "List top-level windows via background UIA (HWND, pid, process, className, physicalBounds).",
        "inputSchema": {
            "type": "object",
            "properties": {"process": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "inspect_window",
        "description": "Inspect UIA controls under hwnd with optional byte budget (max_bytes) and incremental delta (delta_base).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": ["string", "integer"]},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                "name_regex": {"type": "string"},
                "automation_id": {"type": "string"},
                "control_type": {"type": "string"},
                "max_bytes": {"type": "integer", "minimum": 2000, "maximum": 65536, "default": DEFAULT_MAX_BYTES},
                "delta_base": {"type": "string", "maxLength": 64},
            },
            "required": ["hwnd"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_actions",
        "description": "Run up to 20 background UIA actions (set_value, invoke, toggle, select, expand, collapse, read, assert, wait) under hwnd.",
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
        "description": "Guarded foreground WinApp screenshot/input (checks POINT_COVERED, shows edge glow + Esc abort on input).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "hwnd": {"type": ["string", "integer"]},
                "action": {"type": "string", "enum": ["screenshot", "click", "drag", "type", "keys", "scroll"]},
                "foreground": {"type": "boolean"},
                "expected_pid": {"type": "integer", "minimum": 1},
                "expected_bounds": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                "expected_foreground": {"type": ["string", "integer"]},
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


def _compact_control(rec: Any) -> dict[str, Any]:
    if not isinstance(rec, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        if k == "isEnabled" and v is True:
            continue
        if k == "isOffscreen" and v is False:
            continue
        if (
            k == "bounds"
            and isinstance(v, dict)
            and all(isinstance(v.get(p), int) and not isinstance(v.get(p), bool) for p in ("x", "y", "width", "height"))
        ):
            out["bounds"] = [v["x"], v["y"], v["width"], v["height"]]
            continue
        out[k] = v
    return out


def _control_sig(ctrl: dict[str, Any]) -> tuple[Any, ...]:
    return (
        ctrl.get("controlType", ""),
        ctrl.get("automationId", ""),
        ctrl.get("className", ""),
        ctrl.get("name", ""),
        ctrl.get("depth"),
    )


def _control_key(ctrl: dict[str, Any], idx: int) -> tuple[Any, ...]:
    return (
        ctrl.get("controlType", ""),
        ctrl.get("automationId", ""),
        ctrl.get("className", ""),
        ctrl.get("depth"),
        idx,
    )


def _apply_inspect_budget(
    result: dict[str, Any],
    hwnd: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    delta_base: str | None = None,
) -> dict[str, Any]:
    global _TREE_SEQ
    if not result.get("ok") or not isinstance(result.get("controls"), list):
        return result

    compact = [_compact_control(c) for c in result["controls"]]
    _TREE_SEQ += 1
    new_id = f"t{_TREE_SEQ}"
    _TREE_CACHE[new_id] = (hwnd, compact)
    while len(_TREE_CACHE) > 16:
        _TREE_CACHE.popitem(last=False)

    out = dict(result)
    out["tree_id"] = new_id
    out["controls"] = compact

    if delta_base:
        cached = _TREE_CACHE.get(delta_base)
        if not cached:
            out["delta_fallback"] = "baseline_not_found"
        elif cached[0] != hwnd:
            out["delta_fallback"] = "hwnd_mismatch"
        else:
            prev_controls = cached[1]
            prev_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
            seen_prev: dict[tuple[Any, ...], int] = {}
            for c in prev_controls:
                base_k = (c.get("controlType", ""), c.get("automationId", ""), c.get("className", ""), c.get("depth"))
                idx = seen_prev.get(base_k, 0)
                seen_prev[base_k] = idx + 1
                prev_by_key[_control_key(c, idx)] = c

            curr_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
            seen_curr: dict[tuple[Any, ...], int] = {}
            for c in compact:
                base_k = (c.get("controlType", ""), c.get("automationId", ""), c.get("className", ""), c.get("depth"))
                idx = seen_curr.get(base_k, 0)
                seen_curr[base_k] = idx + 1
                curr_by_key[_control_key(c, idx)] = c

            added = [c for k, c in curr_by_key.items() if k not in prev_by_key]
            removed = [c for k, c in prev_by_key.items() if k not in curr_by_key]
            changed = []
            for k, curr_c in curr_by_key.items():
                prev_c = prev_by_key.get(k)
                if prev_c is not None and curr_c != prev_c:
                    changed.append(curr_c)

            denom = max(1, len(prev_controls) + len(compact))
            if (len(added) + len(removed) + len(changed)) / denom <= 0.5:
                delta_out: dict[str, Any] = {
                    "ok": True,
                    "delta": True,
                    "tree_id": new_id,
                    "delta_base": delta_base,
                    "added": added,
                    "removed": removed,
                    "changed": changed,
                }
                if len(json.dumps(delta_out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= max_bytes:
                    return delta_out
                out["delta_fallback"] = "delta_exceeds_max_bytes"
            else:
                out["delta_fallback"] = "change_ratio_exceeded"

    encoded = json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        trimmed = list(compact)
        while trimmed and len(json.dumps({**out, "controls": trimmed, "truncated": True, "truncatedByBytes": True}, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > max_bytes:
            trimmed.pop()
        out["controls"] = trimmed
        out["count"] = len(trimmed)
        out["truncated"] = True
        out["truncatedByBytes"] = True
    return out


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
        if "index" in action:
            _int_arg(action, "index", 0, 0, 100)
        unknown_selector_keys = set(action) - selectors - {"op", "action", "value", "state", "key", "field", "equals", "contains", "ms", "timeout_ms"}
        if unknown_selector_keys:
            raise BridgeError(f"actions[{i}] has unsupported fields: {', '.join(sorted(unknown_selector_keys))}")
        clean.append(action)
    return clean


@contextmanager
def _foreground_guard(enabled: bool) -> Iterator[Path | None]:
    if not enabled or os.name != "nt" or not GUARD_SCRIPT.is_file():
        yield None
        return
    fd, name = tempfile.mkstemp(prefix="ac-abort-", suffix=".flag")
    os.close(fd)
    abort_path = Path(name)
    abort_path.unlink(missing_ok=True)
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(GUARD_SCRIPT), "--abort-file", str(abort_path), "--parent-pid", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        proc = None
    try:
        yield abort_path
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            abort_path.unlink(missing_ok=True)
        except OSError:
            pass


def _run_watched(
    command: list[str],
    *,
    timeout_s: float,
    abort_file: Path | None,
    label: str,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    proc = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        startupinfo=startupinfo,
        encoding="utf-8",
        errors="replace",
    )
    out_chunks: list[str] = []
    err_chunks: list[str] = []

    def _drain(stream: Any, bucket: list[str]) -> None:
        if stream is None:
            return
        try:
            bucket.append(stream.read() or "")
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    t_out = threading.Thread(target=_drain, args=(proc.stdout, out_chunks), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err_chunks), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout_s
    while True:
        if abort_file is not None and abort_file.is_file():
            try:
                proc.kill()
            except Exception:
                pass
            t_out.join(timeout=0.5)
            t_err.join(timeout=0.5)
            raise BridgeError("Aborted by user (Esc)")
        ret = proc.poll()
        if ret is not None:
            t_out.join(timeout=2.0)
            t_err.join(timeout=2.0)
            return ret, "".join(out_chunks), "".join(err_chunks)
        if time.monotonic() >= deadline:
            try:
                proc.kill()
            except Exception:
                pass
            t_out.join(timeout=0.5)
            t_err.join(timeout=0.5)
            raise BridgeError(f"{label} timed out after {timeout_s}s")
        time.sleep(0.03)


class _UiaDaemonClient:
    """Keeps a single STA Python UIA worker alive across background MCP calls."""

    def __init__(self, worker_path: Path) -> None:
        self.worker_path = worker_path
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._stderr_lines: list[str] = []
        self._seq = 0
        self._warming = False

    def _reader_loop(self, stdout: Any, target_queue: queue.Queue[str | None]) -> None:
        try:
            for line in stdout:
                target_queue.put(line)
        except Exception:
            pass
        finally:
            target_queue.put(None)

    def _stderr_loop(self, stderr: Any, bucket: list[str]) -> None:
        try:
            for line in stderr:
                bucket.append(line)
                if len(bucket) > 50:
                    del bucket[:-50]
        except Exception:
            pass

    def _drain_stderr(self) -> str:
        return "".join(self._stderr_lines).strip()

    def _ensure_started_locked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._close_locked()
        if not self.worker_path.is_file():
            raise BridgeError(f"UIA worker unavailable: {self.worker_path}")
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        env = os.environ.copy()
        env["WIN_UIA_SCRIPT_DIR"] = str(self.worker_path.resolve().parent)
        self._queue = queue.Queue()
        self._stderr_lines = []
        self._proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", str(self.worker_path), "--daemon"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            startupinfo=startupinfo,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert self._proc.stdout is not None
        threading.Thread(
            target=self._reader_loop,
            args=(self._proc.stdout, self._queue),
            daemon=True,
        ).start()
        if self._proc.stderr is not None:
            threading.Thread(
                target=self._stderr_loop,
                args=(self._proc.stderr, self._stderr_lines),
                daemon=True,
            ).start()
        deadline = time.monotonic() + DAEMON_BOOT_TIMEOUT_S
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                self._close_locked()
                raise BridgeError("UIA worker timed out during COM/UIA startup")
            try:
                line = self._queue.get(timeout=rem)
            except queue.Empty:
                self._close_locked()
                raise BridgeError("UIA worker timed out during COM/UIA startup")
            if line is None:
                err = self._drain_stderr()
                self._close_locked()
                raise BridgeError(err or "UIA worker exited before reporting ready")
            text = line.strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError as exc:
                self._close_locked()
                raise BridgeError(f"Invalid UIA worker startup line: {text[:400]}") from exc
            if msg.get("ready") is True:
                return
            self._close_locked()
            raise BridgeError(msg.get("error") or "UIA worker failed initialization")

    def _close_locked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.wait(timeout=1.5)
            except Exception:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except Exception:
                    pass

    def warm_async(self) -> None:
        if not self.worker_path.is_file() or self._warming:
            return
        self._warming = True

        def _warm() -> None:
            try:
                with self._lock:
                    self._ensure_started_locked()
            except Exception:
                pass
            finally:
                self._warming = False

        threading.Thread(target=_warm, daemon=True).start()

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def request(self, payload: dict[str, Any], timeout_s: float = PROCESS_TIMEOUT_S) -> dict[str, Any]:
        with self._lock:
            self._ensure_started_locked()
            assert self._proc is not None and self._proc.stdin is not None
            self._seq += 1
            req_id = self._seq
            line = json.dumps({"id": req_id, "args": payload}, ensure_ascii=False, separators=(",", ":")) + "\n"
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except OSError as exc:
                self._close_locked()
                raise BridgeError(f"Failed writing request to UIA worker: {exc}") from exc

            deadline = time.monotonic() + timeout_s
            while True:
                rem = deadline - time.monotonic()
                if rem <= 0:
                    self._close_locked()
                    raise BridgeError(f"UIA operation timed out after {timeout_s}s")
                try:
                    raw_line = self._queue.get(timeout=rem)
                except queue.Empty:
                    self._close_locked()
                    raise BridgeError(f"UIA operation timed out after {timeout_s}s")
                if raw_line is None:
                    err = self._drain_stderr()
                    self._close_locked()
                    raise BridgeError(err or "UIA worker terminated unexpectedly")
                text = raw_line.strip()
                if not text:
                    continue
                try:
                    resp = json.loads(text)
                except json.JSONDecodeError as exc:
                    self._close_locked()
                    raise BridgeError(f"Invalid UIA worker JSON line: {text[:400]}") from exc
                if resp.get("id") != req_id:
                    continue
                res = resp.get("result")
                if not isinstance(res, dict):
                    raise BridgeError("UIA worker returned an unexpected result shape")
                return res


_UIA_DAEMON = _UiaDaemonClient(UIA_WORKER)
atexit.register(_UIA_DAEMON.close)


def _payload_to_ps_args(payload: dict[str, Any], actions_file: Path | None = None) -> tuple[str, list[str], bool]:
    mode = str(payload.get("mode", ""))
    ps: list[str] = []
    if mode == "windows":
        if payload.get("includeUntitled"):
            ps.append("-IncludeUntitled")
        if payload.get("process"):
            ps.extend(["-Process", str(payload["process"])])
    elif mode == "inspect":
        ps.extend(["-Hwnd", str(payload["hwnd"]), "-MaxDepth", str(payload["maxDepth"]), "-Limit", str(payload["limit"])])
        for key, flag in (("name", "-Name"), ("automationId", "-AutomationId"), ("controlType", "-ControlType")):
            if payload.get(key):
                ps.extend([flag, str(payload[key])])
    elif mode == "batch":
        ps.extend(["-Hwnd", str(payload["hwnd"])])
        if actions_file is not None:
            ps.extend(["-ActionsFile", str(actions_file)])
    return mode, ps, bool(payload.get("allowForeground"))


def _run_uia_oneshot(payload: dict[str, Any], allow_foreground: bool = False) -> dict[str, Any]:
    tmp_path: Path | None = None
    try:
        actions = payload.get("actions")
        args_payload = dict(payload)
        if isinstance(actions, list):
            fd, name = tempfile.mkstemp(prefix="codex-uia-", suffix=".json")
            tmp_path = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump({"actions": actions}, f, ensure_ascii=False, separators=(",", ":"))
            args_payload.pop("actions", None)
            args_payload["actionsFile"] = str(tmp_path)

        if UIA_WORKER.is_file():
            env = os.environ.copy()
            env["WIN_UIA_SCRIPT_DIR"] = str(UIA_WORKER.resolve().parent)
            env["WIN_UIA_ARGS"] = json.dumps(args_payload, ensure_ascii=False, separators=(",", ":"))
            command = [sys.executable, "-X", "utf8", str(UIA_WORKER)]
            label = "UIA worker"
        else:
            if not UIA_SCRIPT.is_file():
                raise BridgeError(f"UIA script unavailable: {UIA_SCRIPT}")
            if not POWERSHELL.is_file():
                raise BridgeError(f"Windows PowerShell unavailable: {POWERSHELL}")
            mode, ps, fg = _payload_to_ps_args(args_payload, tmp_path)
            command = [str(POWERSHELL), "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass", "-File", str(UIA_SCRIPT), "-Mode", mode, *ps]
            if fg:
                command.append("-AllowForeground")
            env = None
            label = "UIA operation"

        with _foreground_guard(allow_foreground) as abort_file:
            returncode, raw_stdout, raw_stderr = _run_watched(command, timeout_s=PROCESS_TIMEOUT_S, abort_file=abort_file, label=label, env=env)
        stdout = raw_stdout.strip()
        if not stdout:
            detail = raw_stderr.strip() or f"{label} exited with code {returncode} without JSON output"
            raise BridgeError(detail)
        try:
            result = json.loads(stdout.splitlines()[-1])
        except json.JSONDecodeError as exc:
            raise BridgeError(f"invalid UIA JSON output (exit {returncode}): {stdout[-1200:]}") from exc
        if not isinstance(result, dict):
            raise BridgeError("UIA returned an unexpected result shape")
        result = _clean_sensitive(result)
        if returncode != 0 and result.get("ok") is not False:
            raise BridgeError(result.get("error") or f"{label} exited with code {returncode}")
        return result
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _run_uia(payload: dict[str, Any], allow_foreground: bool = False) -> dict[str, Any]:
    if not allow_foreground and UIA_WORKER.is_file():
        result = _UIA_DAEMON.request(payload, timeout_s=PROCESS_TIMEOUT_S)
        return _clean_sensitive(result)
    return _run_uia_oneshot(payload, allow_foreground=allow_foreground)


def _call(name: str, args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise BridgeError("tool arguments must be an object")
    if name == "list_windows":
        process = args.get("process", "")
        if not isinstance(process, str):
            raise BridgeError("process must be a string")
        payload: dict[str, Any] = {"mode": "windows", "includeUntitled": True}
        if process.strip():
            if len(process) > 128 or re.search(r"[\r\n\x00]", process):
                raise BridgeError("invalid process name")
            payload["process"] = process.strip()
        result = _run_uia(payload)
        if result.get("ok") is not True:
            result.pop("windows", None)
            return result
        safe_windows = []
        for window in result.get("windows", []):
            cls = window.get("className")
            if cls in NOISE_WINDOW_CLASSES:
                continue
            item = {k: window.get(k) for k in ("hwnd", "pid", "process", "className")}
            bounds = window.get("bounds") or {}
            if all(isinstance(bounds.get(k), int) and not isinstance(bounds.get(k), bool) for k in ("x", "y", "width", "height")):
                item["physicalBounds"] = [bounds["x"], bounds["y"], bounds["x"] + bounds["width"], bounds["y"] + bounds["height"]]
            safe_windows.append(item)
        return {"ok": True, "windows": safe_windows}
    if name == "launch_registered_app":
        app_id = args.get("id")
        if not isinstance(app_id, str):
            raise BridgeError("id must be a string")
        try:
            return launch_registered_target(app_id)
        except (OSError, ValueError) as exc:
            raise BridgeError(str(exc)) from exc
    if name == "inspect_window":
        hwnd = _hwnd(args.get("hwnd"))
        depth = _int_arg(args, "max_depth", 5, 1, 8)
        limit = _int_arg(args, "limit", 50, 1, 100)
        max_bytes = _int_arg(args, "max_bytes", DEFAULT_MAX_BYTES, 2000, 65536)
        delta_base = args.get("delta_base")
        if delta_base is not None and (not isinstance(delta_base, str) or len(delta_base) > 64):
            raise BridgeError("delta_base must be a string of at most 64 characters")
        payload = {"mode": "inspect", "hwnd": hwnd, "maxDepth": depth, "limit": limit}
        for key, dst_key in (("name_regex", "name"), ("automation_id", "automationId"), ("control_type", "controlType")):
            val = args.get(key)
            if val is not None:
                if not isinstance(val, str) or len(val) > 256 or re.search(r"[\r\n\x00]", val):
                    raise BridgeError(f"{key} must be a string of at most 256 characters")
                if val:
                    payload[dst_key] = val
        result = _run_uia(payload)
        result.pop("autoSetFocus", None)
        result.pop("totalScanned", None)
        result.pop("scanned", None)
        if result.get("deepQueryUsed") is False:
            result.pop("deepQueryUsed", None)
        win_info = result.get("window")
        if isinstance(win_info, dict):
            win_info.pop("title", None)
            if win_info.get("visible") is True:
                win_info.pop("visible", None)
            if win_info.get("minimized") is False:
                win_info.pop("minimized", None)
            b = win_info.pop("bounds", None)
            if isinstance(b, dict) and all(isinstance(b.get(k), int) and not isinstance(b.get(k), bool) for k in ("x", "y", "width", "height")):
                win_info["physicalBounds"] = [b["x"], b["y"], b["x"] + b["width"], b["y"] + b["height"]]
        return _apply_inspect_budget(result, hwnd, max_bytes=max_bytes, delta_base=delta_base)
    if name == "run_actions":
        hwnd = _hwnd(args.get("hwnd"))
        actions = _validate_actions(args.get("actions"))
        has_legacy_foreground = any(str(a.get("automationId", "")).startswith("__fg_") for a in actions)
        allow_foreground = args.get("allow_foreground", False)
        if not isinstance(allow_foreground, bool):
            raise BridgeError("allow_foreground must be a boolean")
        if has_legacy_foreground and not allow_foreground:
            raise BridgeError("legacy __fg_* actions require explicit allow_foreground=true; prefer foreground_desktop")
        result = _run_uia(
            {"mode": "batch", "hwnd": hwnd, "actions": actions, "allowForeground": allow_foreground},
            allow_foreground=allow_foreground,
        )
        result.pop("autoSetFocus", None)
        if isinstance(result.get("results"), list):
            compact_steps = []
            for step in result["results"]:
                if not isinstance(step, dict):
                    continue
                s = dict(step)
                if "error" not in s:
                    s.pop("target", None)
                if isinstance(s.get("info"), dict):
                    s["info"] = _compact_control(s["info"])
                compact_steps.append(s)
            result["results"] = compact_steps
        if result.get("ok") and not allow_foreground and result.get("foregroundBefore") == result.get("foregroundAfter"):
            result.pop("foregroundBefore", None)
            result.pop("foregroundAfter", None)
            result.pop("pointerBefore", None)
            result.pop("pointerAfter", None)
            result["foregroundPreserved"] = True
        return result
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


def _check_point_not_covered(hwnd: int, expected_pid: int, x: int, y: int) -> None:
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    hit = user32.WindowFromPoint(wintypes.POINT(x, y))
    if not hit:
        return
    root = int(user32.GetAncestor(hit, 2) or hit)  # GA_ROOT = 2
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(root), ctypes.byref(pid))
    hit_pid = int(pid.value)
    if root != hwnd and hit_pid != expected_pid:
        raise BridgeError(f"POINT_COVERED: target point ({x},{y}) is obscured by window 0x{root:X} (pid={hit_pid})")


def _winapp_run(argv: list[str], *, abort_file: Path | None = None) -> str:
    winapp = WINAPP or _resolve_winapp()
    if not winapp or not winapp.is_file():
        raise BridgeError("WinApp CLI unavailable; set AUTO_COMPUTER_WINAPP or install winapp.exe into %LOCALAPPDATA%\\AutoComputer\\tools\\winapp")
    returncode, out, err = _run_watched([str(winapp), *argv], timeout_s=FOREGROUND_TIMEOUT_S, abort_file=abort_file, label="WinApp")
    detail = (out + "\n" + err).strip()
    if returncode:
        raise BridgeError(f"WinApp exited {returncode}: {detail[-1200:]}")
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

    def point(xkey: str, ykey: str) -> tuple[int, int]:
        x = _int_arg(args, xkey, -2_000_001, -2_000_000, 2_000_000)
        y = _int_arg(args, ykey, -2_000_001, -2_000_000, 2_000_000)
        if not (left <= x < right and top <= y < bottom):
            raise BridgeError(f"{xkey}/{ykey} must lie within target bounds {before['bounds']}")
        return x, y

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
        px, py = point("x", "y")
        _check_point_not_covered(hwnd, expected_pid, px, py)
        button = args.get("button", "left")
        if button not in {"left", "right", "double"}:
            raise BridgeError("button must be left, right, or double")
        cmd = root + ["click", "--at", f"{px},{py}", *target]
        if button == "right":
            cmd.append("--right")
        elif button == "double":
            cmd.append("--double")
    elif action == "drag":
        px, py = point("x", "y")
        tx, ty = point("to_x", "to_y")
        _check_point_not_covered(hwnd, expected_pid, px, py)
        _check_point_not_covered(hwnd, expected_pid, tx, ty)
        cmd = root + ["drag", f"{px},{py}", f"{tx},{ty}", *target]
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

    with _foreground_guard(True) as abort_file:
        output = _winapp_run(cmd, abort_file=abort_file)
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
        _UIA_DAEMON.warm_async()
        return {"jsonrpc": "2.0", "id": ident, "result": {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "antigravity-desktop-bridge", "version": "0.2.0"}}}
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
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    try:
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
    finally:
        _UIA_DAEMON.close()


if __name__ == "__main__":
    raise SystemExit(main())
