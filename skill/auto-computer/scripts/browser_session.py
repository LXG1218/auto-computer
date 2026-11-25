#!/usr/bin/env python3
"""Safe status and authenticated reconnect for the user's existing browser profile."""
from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from _common import configure_utf8_stdio, emit_json, find_chrome_exe, get_auto_computer_home, get_tools_dir
from browser_headless import _find_edge_exe

CLI = Path(
    os.environ.get("AUTO_COMPUTER_PLAYWRIGHT_CLI", "").strip()
    or str(get_tools_dir() / "playwright" / "node_modules" / "@playwright" / "cli" / "playwright-cli.js")
)
SECRET_ROOT = Path(
    os.environ.get("AUTO_COMPUTER_SECRETS_DIR", "").strip()
    or str(get_auto_computer_home() / "secrets")
)
MIN_BACKGROUND_CHROME = (158, 0, 8127, 2)
CRYPTPROTECT_UI_FORBIDDEN = 0x1
EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9 _-]{1,64}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


def _browser_name(browser: str) -> str:
    return "msedge" if browser == "edge" else "chrome"


def _browser_exe(browser: str) -> str:
    exe = _find_edge_exe() if browser == "edge" else find_chrome_exe()
    if not exe:
        raise FileNotFoundError(f"Browser executable not found for {browser}")
    return exe


def _profile_name(profile: str) -> str:
    value = profile.strip()
    if not PROFILE_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError("profile must be a simple Chromium profile directory name")
    return value


def _profile_slug(profile: str) -> str:
    # Hex encoding is stable and avoids collisions between profile names.
    return profile.casefold().encode("utf-8").hex()


def _default_session(browser: str, profile: str) -> str:
    suffix = "default" if profile.casefold() == "default" else f"p-{_profile_slug(profile)}"
    return f"auto-computer-{browser}-{suffix}"


def _normalize_token(value: str) -> str | None:
    token = value.strip()
    prefix = "PLAYWRIGHT_MCP_EXTENSION_TOKEN="
    if token.startswith(prefix):
        token = token[len(prefix):].strip()
    return token if TOKEN_RE.fullmatch(token) else None


def _secret_path(browser: str, profile: str) -> Path:
    path = (SECRET_ROOT / f"playwright-mcp-{browser}-{_profile_slug(profile)}.dpapi").resolve()
    root = SECRET_ROOT.resolve()
    if path.parent != root:
        raise ValueError("token path escaped the protected token directory")
    return path


def _entropy(browser: str, profile: str) -> bytes:
    return f"AutoComputer|PlaywrightExtension|{browser}|{profile.casefold()}".encode("utf-8")


def _dpapi_protect(value: str, browser: str, profile: str) -> bytes:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("pywin32 is required; run this command with your Auto Computer Python venv") from exc
    protected = win32crypt.CryptProtectData(
        value.encode("utf-8"),
        "AutoComputer Playwright extension token",
        _entropy(browser, profile),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
    )
    return bytes(protected)


def _dpapi_unprotect(value: bytes, browser: str, profile: str) -> str:
    try:
        import win32crypt
    except ImportError as exc:
        raise RuntimeError("pywin32 is required; run this command with your Auto Computer Python venv") from exc
    _, clear = win32crypt.CryptUnprotectData(
        value,
        _entropy(browser, profile),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
    )
    return bytes(clear).decode("utf-8")


def _token_source(browser: str, profile: str) -> tuple[str | None, str]:
    env_token = os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN", "")
    if env_token:
        normalized = _normalize_token(env_token)
        return (normalized, "process_environment") if normalized else (None, "invalid_process_environment_token")
    path = _secret_path(browser, profile)
    if not path.is_file():
        return None, "missing"
    token = _normalize_token(_dpapi_unprotect(path.read_bytes(), browser, profile))
    if not token:
        return None, "invalid_protected_token"
    return token, "dpapi_file"


def _cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["NO_UPDATE_NOTIFIER"] = "1"
    return env


def _cli_list() -> dict[str, Any]:
    if not CLI.is_file():
        raise FileNotFoundError(f"Playwright CLI not found: {CLI}")
    node = os.environ.get("AUTO_COMPUTER_NODE") or "node"
    proc = subprocess.run(
        [node, str(CLI), "--json", "list", "--all"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=12,
        env=_cli_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError("Playwright CLI session listing failed")
    try:
        value = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Playwright CLI returned invalid session data") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Playwright CLI returned an unexpected session result")
    return value


def _browser_version(exe: str) -> tuple[int, ...] | None:
    try:
        import win32api

        info = win32api.GetFileVersionInfo(exe, "\\")
        ms, ls = info["FileVersionMS"], info["FileVersionLS"]
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None


def _window_snapshot(browser_exe: str) -> dict[str, Any] | None:
    """Read foreground HWND and active target window caption without exposing either."""
    if os.name != "nt":
        return None
    try:
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        target_basename = os.path.basename(browser_exe).casefold()
        process_query_limited_information = 0x1000
        foreground_value = user32.GetForegroundWindow()
        foreground_hwnd = int(foreground_value or 0)

        def window_info(hwnd: int) -> tuple[bool, str]:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
            if not pid.value:
                return False, ""
            handle = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
            if not handle:
                return False, ""
            try:
                capacity = wintypes.DWORD(32768)
                buffer = ctypes.create_unicode_buffer(capacity.value)
                if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(capacity)):
                    return False, ""
                if os.path.basename(buffer.value).casefold() != target_basename:
                    return False, ""
            finally:
                kernel32.CloseHandle(handle)
            length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
            title = ctypes.create_unicode_buffer(max(1, int(length) + 1))
            user32.GetWindowTextW(wintypes.HWND(hwnd), title, len(title))
            return True, title.value

        foreground_is_browser = False
        if foreground_hwnd:
            foreground_is_browser, _ = window_info(foreground_hwnd)

        candidates: list[tuple[int, str]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd: int, _lparam: int) -> bool:
            if user32.IsWindowVisible(wintypes.HWND(hwnd)):
                matches, caption = window_info(int(hwnd))
                if matches:
                    candidates.append((int(hwnd), caption))
            return True

        callback = callback_type(visit)
        if not user32.EnumWindows(callback, 0):
            return None
        target = next((item for item in candidates if item[0] == foreground_hwnd), None)
        if target is None and candidates:
            target = candidates[0]
        return {
            "foreground_hwnd": foreground_hwnd,
            "foreground_is_browser": foreground_is_browser,
            "target_hwnd": target[0] if target else None,
            "target_caption": target[1] if target else None,
        }
    except Exception:
        return None


def _foreground_result(before: dict[str, Any] | None, after: dict[str, Any] | None) -> tuple[str, bool | None]:
    if before is None or after is None:
        return "unavailable", None
    preserved = before.get("foreground_hwnd") == after.get("foreground_hwnd")
    before_caption = before.get("target_caption")
    after_caption = after.get("target_caption")
    caption_preserved = None
    if before_caption is not None and after_caption is not None:
        caption_preserved = before_caption == after_caption
    return ("preserved" if preserved else "changed"), caption_preserved


def _profile_root(browser: str, profile: str) -> Path:
    local_value = os.environ.get("LOCALAPPDATA", "")
    if not local_value:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    local = Path(local_value)
    product = Path("Google") / "Chrome" if browser == "chrome" else Path("Microsoft") / "Edge"
    return local / product / "User Data" / profile


def _extension_version(browser: str, profile: str) -> str | None:
    base = _profile_root(browser, profile) / "Extensions" / EXTENSION_ID
    try:
        versions = sorted((p for p in base.iterdir() if p.is_dir()), reverse=True)
    except OSError:
        return None
    for directory in versions:
        try:
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if manifest.get("name") == "Playwright Extension" and isinstance(manifest.get("version"), str):
            return manifest["version"]
    return None


def _version_at_least(value: str | None, minimum: tuple[int, ...]) -> bool:
    if not value:
        return False
    try:
        parts = tuple(int(part) for part in value.split("."))
    except ValueError:
        return False
    parts = (parts + (0,) * len(minimum))[:len(minimum)]
    return parts >= minimum


def _version_text(version: tuple[int, ...] | None) -> str | None:
    return ".".join(map(str, version)) if version else None


def _is_running(exe_name: str) -> bool:
    if os.name != "nt":
        return False
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return exe_name.casefold() in (proc.stdout or "").casefold()
    except (OSError, subprocess.SubprocessError):
        return False


def _find_session(data: dict[str, Any], name: str, browser: str) -> dict[str, Any] | None:
    expected = _browser_name(browser)
    sessions = data.get("browsers", [])
    if not isinstance(sessions, list):
        return None
    for item in sessions:
        if not isinstance(item, dict) or item.get("name") != name:
            continue
        browser_type = str(item.get("browserType", "")).casefold()
        if (expected == "chrome" and browser_type in {"chrome", "chromium"}) or (
            expected == "msedge" and browser_type in {"msedge", "edge"}
        ):
            return item if item.get("status") == "open" else {"name": name, "conflict": True}
        return {"name": name, "conflict": True}
    return None


def _status(browser: str, profile: str, session: str) -> dict[str, Any]:
    exe = _browser_exe(browser)
    data = _cli_list()
    version = _browser_version(exe)
    running = _is_running("msedge.exe" if browser == "edge" else "chrome.exe")
    token, token_source = _token_source(browser, profile)
    extension_version = _extension_version(browser, profile)
    existing = _find_session(data, session, browser)
    conflict = bool(existing and existing.get("conflict"))
    attached = bool(existing and existing.get("attached") and not conflict)
    version_supported = browser == "chrome" and version is not None and version >= MIN_BACKGROUND_CHROME
    extension_installed = extension_version is not None
    extension_background_supported = _version_at_least(extension_version, (0, 5, 0))
    already_reusable = attached and bool(existing.get("compatible"))
    can_reconnect = bool(token and running and extension_background_supported and version_supported and not conflict)
    blockers: list[str] = []
    if conflict:
        blockers.append("session_name_in_use_by_another_browser")
    if not running and not already_reusable:
        blockers.append("browser_not_running")
    if not extension_installed:
        blockers.append("playwright_extension_not_detected")
    elif not extension_background_supported:
        blockers.append("playwright_extension_0_5_0_or_later_required_for_background_mode")
    if browser == "edge":
        blockers.append("background_reconnect_behavior_not_verified_for_edge")
    elif not version_supported:
        blockers.append("background_connect_requires_chrome_158_0_8127_2_or_later")
    if not token and not already_reusable:
        blockers.append("no_authorized_extension_token")
    return {
        "ok": True,
        "browser": browser,
        "profile": profile,
        "session": session,
        "browser_running": running,
        "browser_version": _version_text(version),
        "extension_installed": extension_installed,
        "extension_version": extension_version,
        "token_source": token_source,
        "session_reusable": already_reusable,
        "can_reconnect_in_background": can_reconnect,
        "blockers": blockers,
    }


def _save_token(browser: str, profile: str) -> dict[str, Any]:
    if not sys.stdin.isatty():
        raise RuntimeError("save-token requires an interactive terminal; no token was read or saved")
    print(
        "Open the Playwright Extension Manage clients page in the selected browser profile, copy its profile token, then paste it here.",
        file=sys.stderr,
    )
    token = _normalize_token(getpass.getpass("Paste the copied token assignment (input hidden): "))
    if not token:
        raise ValueError("token was empty or invalid; nothing was saved")
    protected = _dpapi_protect(token, browser, profile)
    path = _secret_path(browser, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, protected)
    return {"ok": True, "browser": browser, "profile": profile, "saved": True, "storage": "Windows DPAPI CurrentUser"}


def _atomic_write(path: Path, content: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _reconnect(browser: str, profile: str, session: str) -> dict[str, Any]:
    status = _status(browser, profile, session)
    if status["session_reusable"]:
        return {**status, "ok": True, "reused_existing_session": True}
    if not status["can_reconnect_in_background"]:
        return {**status, "ok": False, "reused_existing_session": False}

    token, _ = _token_source(browser, profile)
    assert token
    browser_exe = _browser_exe(browser)
    before = _window_snapshot(browser_exe)
    env = _cli_env()
    env["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = token
    env["PLAYWRIGHT_MCP_BACKGROUND"] = "1"
    env["PLAYWRIGHT_MCP_PROFILE_DIR_NAME"] = profile
    if profile.casefold() == "default":
        target = f"--extension={_browser_name(browser)}"
    else:
        target = f"--cdp=extension://{_browser_name(browser)}/{profile}"
    node = os.environ.get("AUTO_COMPUTER_NODE") or "node"
    try:
        proc = subprocess.run(
            [node, str(CLI), "attach", target, f"--session={session}", "--no-tab-group"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=40,
            env=env,
        )
    except subprocess.TimeoutExpired:
        after = _window_snapshot(browser_exe)
        foreground_check, caption_preserved = _foreground_result(before, after)
        return {
            **status,
            "ok": False,
            "error": "Playwright CLI attach timed out; output was suppressed",
            "foreground_check": foreground_check,
            "browser_active_caption_preserved": caption_preserved,
        }
    after = _window_snapshot(browser_exe)
    foreground_check, caption_preserved = _foreground_result(before, after)
    try:
        data = _cli_list()
        attached = _find_session(data, session, browser)
        session_verified = bool(
            proc.returncode == 0
            and attached
            and not attached.get("conflict")
            and attached.get("status") == "open"
            and attached.get("attached")
            and attached.get("compatible")
        )
    except Exception:
        session_verified = False
    error = None
    if proc.returncode != 0:
        error = "Playwright CLI attach failed; output was suppressed"
    elif not session_verified:
        error = "Attach was not verified in the session registry; no retry was attempted"
    elif foreground_check == "changed":
        error = "Foreground window changed during attach"
    elif caption_preserved is False:
        error = "Browser active window caption changed during attach"
    elif foreground_check == "unavailable":
        error = "Foreground preservation could not be verified"
    return {
        "ok": session_verified and foreground_check == "preserved" and caption_preserved is not False,
        "reused_existing_session": False,
        "attach_return_code": proc.returncode,
        "session_verified": session_verified,
        "background_mode": True,
        "foreground_check": foreground_check,
        "browser_active_caption_preserved": caption_preserved,
        "browser": browser,
        "profile": profile,
        "session": session,
        "output_suppressed": True,
        **({"error": error} if error else {}),
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "save-token", "reconnect"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--browser", choices=("chrome", "edge"), default="chrome")
        cmd.add_argument("--profile", default="Default")
        if name != "save-token":
            cmd.add_argument("--session", default=None)

    args = parser.parse_args()
    started = time.perf_counter()
    try:
        args.profile = _profile_name(args.profile)
        if args.command == "save-token":
            result = _save_token(args.browser, args.profile)
        else:
            args.session = args.session or _default_session(args.browser, args.profile)
            if not SESSION_RE.fullmatch(args.session):
                raise ValueError("session must use letters, numbers, dot, underscore, or hyphen")
            result = (
                _status(args.browser, args.profile, args.session)
                if args.command == "status"
                else _reconnect(args.browser, args.profile, args.session)
            )
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
        emit_json(result, 0 if result.get("ok") else 1)
    except Exception as exc:
        # Never echo command-line secret material or tool output.
        emit_json({"ok": False, "error": str(exc), "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}, 1)


if __name__ == "__main__":
    main()
