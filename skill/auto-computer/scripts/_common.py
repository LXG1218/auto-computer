from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def get_auto_computer_home() -> Path:
    """Return the writable Auto Computer data/runtime root directory."""
    env_home = os.environ.get("AUTO_COMPUTER_HOME", "").strip()
    if env_home:
        return Path(env_home)
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "AutoComputer"
    return Path.home() / "AutoComputer"


def get_tools_dir() -> Path:
    """Return the directory where optional Auto Computer binaries/packages are staged."""
    env_tools = os.environ.get("AUTO_COMPUTER_TOOLS_DIR", "").strip()
    if env_tools:
        return Path(env_tools)
    runtime_file = get_auto_computer_home() / "runtime.json"
    try:
        if runtime_file.is_file():
            runtime_data = json.loads(runtime_file.read_text(encoding="utf-8"))
            rt_tools = str(runtime_data.get("tools", "")).strip()
            if rt_tools and Path(rt_tools).is_dir():
                return Path(rt_tools)
    except Exception:
        pass
    return get_auto_computer_home() / "tools"


DEFAULT_OVERLAY_DIR = str(get_tools_dir() / "python-overlays")


def ensure_comtypes_overlay() -> str | None:
    """Ensure staged pure-Python comtypes overlay is on sys.path when not installed in venv."""
    import importlib.util

    if importlib.util.find_spec("comtypes") is not None:
        return None
    candidates = [
        os.environ.get("AUTO_COMPUTER_OVERLAY", "").strip(),
        str(get_tools_dir() / "python-overlays"),
        DEFAULT_OVERLAY_DIR,
    ]
    for raw in candidates:
        if not raw:
            continue
        p = Path(raw)
        if p.is_dir():
            s = str(p)
            if s not in sys.path:
                sys.path.append(s)
            return s
    return None


def configure_utf8_stdio() -> None:
    """Ensure stdout and stderr emit UTF-8 on Windows consoles."""
    for stream in (sys.stdout, sys.stderr):
        reconf = getattr(stream, "reconfigure", None)
        if callable(reconf):
            try:
                reconf(encoding="utf-8", errors="replace")
            except Exception:
                pass


def enable_dpi_v2() -> bool:
    """Enable Per-Monitor V2 DPI awareness before querying Windows coordinates."""
    if os.name != "nt":
        return False
    import ctypes

    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def emit_json(obj: Any, exit_code: int = 0) -> None:
    """Print compact UTF-8 JSON and terminate with exit_code."""
    configure_utf8_stdio()
    print(json.dumps(obj, ensure_ascii=False))
    raise SystemExit(exit_code)


def parse_json_arg(value: str) -> Any:
    """Load JSON from a file path (UTF-8/UTF-8-SIG) if it exists, else parse inline JSON."""
    p = Path(value)
    if p.exists() and p.is_file():
        return json.loads(p.read_text(encoding="utf-8-sig"))
    return json.loads(value)


def parse_hwnd(value: str | int) -> int:
    """Parse hex ('0x1234') or decimal window handle into an integer."""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s, 10)


def find_chrome_exe() -> str | None:
    """Locate Google Chrome executable on Windows."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    import shutil

    return shutil.which("chrome") or shutil.which("chrome.exe")


def find_edge_exe() -> str | None:
    """Locate Microsoft Edge executable on Windows."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    import shutil

    return shutil.which("msedge") or shutil.which("msedge.exe")


_find_edge_exe = find_edge_exe


_CTYPES_TYPE_NAMES = (
    "HWND",
    "HDC",
    "HBITMAP",
    "HGDIOBJ",
    "WPARAM",
    "LPARAM",
    "LRESULT",
    "UINT",
    "DWORD",
    "BOOL",
)


def __getattr__(name: str) -> Any:
    """Lazily expose strict 64-bit Win32 ctypes aliases without slowing non-Win32 CLI startup."""
    if name in _CTYPES_TYPE_NAMES:
        import ctypes

        mapping = {
            "HWND": ctypes.c_void_p,
            "HDC": ctypes.c_void_p,
            "HBITMAP": ctypes.c_void_p,
            "HGDIOBJ": ctypes.c_void_p,
            "WPARAM": ctypes.c_size_t,
            "LPARAM": ctypes.c_ssize_t,
            "LRESULT": ctypes.c_ssize_t,
            "UINT": ctypes.c_uint,
            "DWORD": ctypes.c_uint32,
            "BOOL": ctypes.c_bool,
        }
        globals().update(mapping)
        return mapping[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
