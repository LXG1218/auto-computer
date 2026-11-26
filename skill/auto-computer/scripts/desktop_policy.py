"""Shared desktop safety policy for protected app owners and qualified launches.

This module deliberately does not launch an executable unless an operator has
registered an exact executable/version/route in the machine policy directory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
from typing import Iterable, Mapping, Any

PROTECTED_PROCESS_NAMES = frozenset({
    "weixin", "wechat", "wechatapp", "wechatappex", "wechatutility",
    "wechatbrowser", "wechatapphost", "wechatplayer", "weixinapp",
})


def _base_name(name: str) -> str:
    return Path(str(name).replace("\\", "/")).name.casefold().removesuffix(".exe")


def is_protected_name(name: str) -> bool:
    base = _base_name(name)
    return base in PROTECTED_PROCESS_NAMES or base.startswith(("wechat", "weixin"))


def protected_pids(records: Iterable[Mapping[str, Any]]) -> set[int]:
    """Return WeChat/Weixin roots and all descendants from synthetic process rows."""
    parents: dict[int, int] = {}
    protected: set[int] = set()
    for row in records:
        try:
            pid = int(row["pid"])
            ppid = int(row.get("parent_pid", row.get("ppid", 0)))
        except (KeyError, TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        parents[pid] = ppid
        if is_protected_name(str(row.get("name", ""))):
            protected.add(pid)
    # Parent chains are bounded and cycle-safe; descendants can be arbitrarily deep.
    for pid in parents:
        seen: set[int] = set()
        current = pid
        for _ in range(128):
            if current in protected:
                protected.add(pid)
                break
            if current in seen:
                break
            seen.add(current)
            current = parents.get(current, 0)
            if current <= 0:
                break
    return protected


def _windows_process_rows() -> list[dict[str, int | str]]:
    if os.name != "nt":
        raise OSError("process ownership checks are available only on Windows")
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snapshot or snapshot == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    rows: list[dict[str, int | str]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            rows.append({"pid": int(entry.th32ProcessID), "parent_pid": int(entry.th32ParentProcessID),
                         "name": str(entry.szExeFile)})
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return rows


def is_protected_pid(pid: int, records: Iterable[Mapping[str, Any]] | None = None) -> bool:
    if isinstance(pid, bool) or int(pid) <= 0:
        return False
    rows = _windows_process_rows() if records is None else records
    return int(pid) in protected_pids(rows)


def ensure_unprotected_pid(pid: int) -> None:
    if is_protected_pid(pid):
        raise PermissionError("WeChat/Weixin windows and their child processes are excluded")


def machine_policy_dir() -> Path:
    home = os.environ.get("AUTO_COMPUTER_HOME", "").strip()
    if home:
        base = Path(home)
    else:
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) / "AutoComputer" if local else Path.home() / "AutoComputer"
    return base / "adapters" / "launch"


def _version_tuple(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ValueError("version must be an exact dotted numeric file version")
    return tuple(int(part) for part in value.split("."))


def validate_launch_target(entry: Mapping[str, Any], *, executable: str | None = None,
                           version: str | None = None) -> dict[str, Any]:
    """Validate registry rows; registration requires an explicitly audited route."""
    app_id = entry.get("id")
    path = Path(str(entry.get("executable", ""))).resolve(strict=True)
    actual_version = str(entry.get("version", ""))
    route = entry.get("route")
    args = entry.get("arguments", [])
    if not isinstance(app_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
        raise ValueError("id must contain 1 to 64 ASCII letters, digits, dot, underscore, or hyphen")
    if not path.is_file() or path.suffix.casefold() != ".exe":
        raise ValueError("registered executable must be an existing .exe file")
    if is_protected_name(path.name):
        raise PermissionError("WeChat/Weixin launch targets are forbidden")
    _version_tuple(actual_version)
    if executable is not None and str(path).casefold() != str(Path(executable).resolve()).casefold():
        raise ValueError("registered executable path does not match")
    if version is not None and actual_version != version:
        raise ValueError("registered executable version does not match")
    if route not in {"verified_background_switch", "audited_min_no_active"}:
        raise ValueError("route must be verified_background_switch or audited_min_no_active")
    if entry.get("verified") is not True:
        raise ValueError("target must be explicitly verified by an operator")
    if route == "verified_background_switch" and not isinstance(entry.get("switch"), str):
        raise ValueError("verified_background_switch requires an exact application switch")
    if route == "audited_min_no_active" and entry.get("entrypoint_audited") is not True:
        raise ValueError("audited_min_no_active requires entrypoint_audited=true")
    if not isinstance(args, list) or any(not isinstance(arg, str) or "\x00" in arg for arg in args):
        raise ValueError("arguments must be a list of strings")
    return {"id": app_id, "executable": str(path), "version": actual_version,
            "route": route, "switch": entry.get("switch"), "arguments": args,
            "verified": True, "entrypoint_audited": entry.get("entrypoint_audited") is True}


def register_launch_target(entry: Mapping[str, Any], directory: Path | None = None) -> Path:
    target = validate_launch_target(entry)
    directory = directory or machine_policy_dir()
    directory.mkdir(parents=True, exist_ok=True)
    # One immutable file per ID prevents clobbering another registered route.
    destination = directory / f"{target['id']}.json"
    if destination.exists():
        raise FileExistsError(f"launch target already registered: {target['id']}")
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(target, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return destination


def load_launch_target(app_id: str, directory: Path | None = None) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", app_id):
        raise ValueError("invalid target id")
    path = (directory or machine_policy_dir()) / f"{app_id}.json"
    with path.open("r", encoding="utf-8") as stream:
        entry = json.load(stream)
    if not isinstance(entry, dict) or entry.get("id") != app_id:
        raise ValueError("launch registry entry ID mismatch")
    return validate_launch_target(entry)


def launch_registered_target(app_id: str, *, directory: Path | None = None) -> dict[str, Any]:
    """Start only an exact registered route with a no-activate initial show hint.

    Application-level verified switch or audited cooperative entrypoint is
    mandatory. Startup show state is defense in depth, not a generic guarantee.
    """
    entry = load_launch_target(app_id, directory)
    args = [entry["executable"], *entry["arguments"]]
    if entry["route"] == "verified_background_switch":
        args.append(entry["switch"])
    if entry["route"] != "verified_background_switch" and entry["route"] != "audited_min_no_active":
        raise ValueError("launch route is not qualified")
    if os.name != "nt":
        raise OSError("registered desktop launch is available only on Windows")
    import ctypes
    from ctypes import wintypes
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 7  # SW_SHOWMINNOACTIVE
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True, startupinfo=startup,
                            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    return {"ok": True, "id": app_id, "pid": proc.pid,
            "route": entry["route"], "foreground_guarantee": "qualified-route-only"}
