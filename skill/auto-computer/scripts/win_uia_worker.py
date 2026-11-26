import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes

SCRIPT_DIR = os.environ.get("WIN_UIA_SCRIPT_DIR") or os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
from _common import ensure_comtypes_overlay
import msaa_background as mb
import win_background as wb

ensure_comtypes_overlay()
import comtypes
import comtypes.client

comtypes.client.GetModule(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "UIAutomationCore.dll"))
from comtypes.gen import UIAutomationClient as uia

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetProcessWindowStation.argtypes = []
user32.GetProcessWindowStation.restype = wintypes.HANDLE
user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
user32.GetThreadDesktop.restype = wintypes.HANDLE
user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
user32.GetUserObjectInformationW.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcessId.argtypes = []
kernel32.GetCurrentProcessId.restype = wintypes.DWORD
kernel32.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

CONTROL_TYPES = {
    50000: "Button",
    50001: "Calendar",
    50002: "CheckBox",
    50003: "ComboBox",
    50004: "Edit",
    50005: "Hyperlink",
    50006: "Image",
    50007: "ListItem",
    50008: "List",
    50009: "Menu",
    50010: "MenuBar",
    50011: "MenuItem",
    50012: "ProgressBar",
    50013: "RadioButton",
    50014: "ScrollBar",
    50015: "Slider",
    50016: "Spinner",
    50017: "StatusBar",
    50018: "Tab",
    50019: "TabItem",
    50020: "Text",
    50021: "ToolBar",
    50022: "ToolTip",
    50023: "Tree",
    50024: "TreeItem",
    50025: "Custom",
    50026: "Group",
    50027: "Thumb",
    50028: "DataGrid",
    50029: "DataItem",
    50030: "Document",
    50031: "SplitButton",
    50032: "Window",
    50033: "Pane",
    50034: "Header",
    50035: "HeaderItem",
    50036: "Table",
    50037: "TitleBar",
    50038: "Separator",
    50039: "SemanticZoom",
    50040: "AppBar",
}
CONTROL_TYPES_BY_NAME = {v.lower(): k for k, v in CONTROL_TYPES.items()}

PATTERN_CHECKS = [
    ("Value", "UIA_IsValuePatternAvailablePropertyId"),
    ("Invoke", "UIA_IsInvokePatternAvailablePropertyId"),
    ("SelectionItem", "UIA_IsSelectionItemPatternAvailablePropertyId"),
    ("Toggle", "UIA_IsTogglePatternAvailablePropertyId"),
    ("ExpandCollapse", "UIA_IsExpandCollapsePatternAvailablePropertyId"),
    ("ScrollItem", "UIA_IsScrollItemPatternAvailablePropertyId"),
    ("LegacyIAccessible", "UIA_IsLegacyIAccessiblePatternAvailablePropertyId"),
]


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _uoi_name(handle):
    if not handle:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    needed = wintypes.DWORD()
    if user32.GetUserObjectInformationW(handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed)):
        return buf.value
    return ""


def _desktop_info():
    winsta = _uoi_name(user32.GetProcessWindowStation())
    desk = _uoi_name(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()))
    sid = wintypes.DWORD()
    kernel32.ProcessIdToSessionId(kernel32.GetCurrentProcessId(), ctypes.byref(sid))
    fg = user32.GetForegroundWindow()
    return {
        "desktop": f"{winsta}\\{desk}" if winsta or desk else "",
        "sessionId": int(sid.value),
        "foregroundHwnd": f"0x{fg:X}" if fg else None,
    }


def _cursor_pos():
    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return [int(pt.x), int(pt.y)]
    return None


def _proc_name(pid, cache):
    if pid in cache:
        return cache[pid]
    name = ""
    h = kernel32.OpenProcess(0x1000, False, pid)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            sz = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
                base = os.path.basename(buf.value)
                name = base[:-4] if base.lower().endswith(".exe") else base
        finally:
            kernel32.CloseHandle(h)
    cache[pid] = name
    return name


def _win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _win_bounds(hwnd):
    r = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return {"x": int(r.left), "y": int(r.top), "width": int(r.right - r.left), "height": int(r.bottom - r.top)}
    return {"x": 0, "y": 0, "width": 0, "height": 0}


def _safe_str(val):
    return "" if val is None else str(val)


def _ctype_name(ct_id):
    return CONTROL_TYPES.get(int(ct_id), f"Type({int(ct_id)})")


def _norm_ctype(val):
    s = _safe_str(val).strip()
    if s.lower().startswith("controltype."):
        s = s[12:]
    return s.lower()


def _el_bounds(el):
    try:
        r = el.CurrentBoundingRectangle
        return {
            "x": int(r.left),
            "y": int(r.top),
            "width": int(r.right - r.left),
            "height": int(r.bottom - r.top),
        }
    except Exception:
        return None


def _el_patterns(el):
    found = []
    for pname, prop_attr in PATTERN_CHECKS:
        prop_id = getattr(uia, prop_attr, None)
        if prop_id is None:
            continue
        try:
            if bool(el.GetCurrentPropertyValue(prop_id)):
                found.append(pname)
        except Exception:
            pass
    return found


def _el_toggle_state(el):
    try:
        if not bool(el.GetCurrentPropertyValue(uia.UIA_IsTogglePatternAvailablePropertyId)):
            return None
        pat = el.GetCurrentPattern(uia.UIA_TogglePatternId).QueryInterface(uia.IUIAutomationTogglePattern)
        st = int(pat.CurrentToggleState)
        return {0: "Off", 1: "On", 2: "Indeterminate"}.get(st, str(st))
    except Exception:
        return None


def _el_is_selected(el):
    try:
        if not bool(el.GetCurrentPropertyValue(uia.UIA_IsSelectionItemPatternAvailablePropertyId)):
            return None
        pat = el.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
        return bool(pat.CurrentIsSelected)
    except Exception:
        return None


def _el_record(el, depth=None, include_patterns=True):
    is_pw = False
    try:
        is_pw = bool(el.CurrentIsPassword)
    except Exception:
        pass
    try:
        name = "<redacted>" if is_pw else _safe_str(el.CurrentName)
    except Exception:
        name = ""
    try:
        auto_id = _safe_str(el.CurrentAutomationId)
    except Exception:
        auto_id = ""
    try:
        ct = _ctype_name(el.CurrentControlType)
    except Exception:
        ct = ""
    try:
        cls = _safe_str(el.CurrentClassName)
    except Exception:
        cls = ""
    try:
        enabled = bool(el.CurrentIsEnabled)
    except Exception:
        enabled = None
    try:
        offscreen = bool(el.CurrentIsOffscreen)
    except Exception:
        offscreen = None
    rec = {
        "name": name,
        "automationId": auto_id,
        "controlType": ct,
        "className": cls,
        "isEnabled": enabled,
        "isOffscreen": offscreen,
        "bounds": _el_bounds(el),
    }
    if depth is not None:
        rec["depth"] = depth
    if include_patterns:
        rec["patterns"] = _el_patterns(el)
    return rec


def _walk_controls(walker, root, max_depth, callback):
    stack = [(root, 0)]
    scanned = 0
    while stack:
        node, depth = stack.pop()
        scanned += 1
        if depth > 0:
            if callback(node, depth, scanned) is False:
                return scanned, True
        if depth < max_depth:
            children = []
            try:
                child = walker.GetFirstChildElement(node)
                while child:
                    children.append(child)
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                pass
            for c in reversed(children):
                stack.append((c, depth + 1))
    return scanned, False


def _build_condition(raw, control_type="", automation_id="", exact_name=None):
    conds = []
    ct_norm = _norm_ctype(control_type)
    if ct_norm and ct_norm in CONTROL_TYPES_BY_NAME:
        conds.append(raw.CreatePropertyCondition(uia.UIA_ControlTypePropertyId, CONTROL_TYPES_BY_NAME[ct_norm]))
    if automation_id not in (None, ""):
        conds.append(raw.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId, str(automation_id)))
    if exact_name not in (None, ""):
        conds.append(raw.CreatePropertyCondition(uia.UIA_NamePropertyId, str(exact_name)))
    if not conds:
        return raw.CreateTrueCondition()
    cond = conds[0]
    for extra in conds[1:]:
        cond = raw.CreateAndCondition(cond, extra)
    return cond


def _init_uia():
    comtypes.CoInitialize()
    raw = comtypes.client.CreateObject(
        "{e22ad333-b25f-460c-83d0-0581107395c9}",
        interface=uia.IUIAutomation,
    )
    safe = raw.QueryInterface(uia.IUIAutomation2)
    prop_name = "AutoSetFocus" if hasattr(type(safe), "AutoSetFocus") else "autoSetFocus"
    setattr(safe, prop_name, False)
    if bool(getattr(safe, prop_name)):
        raise RuntimeError("IUIAutomation2.AutoSetFocus readback was not False")
    return raw, safe


def mode_windows(args):
    _set_dpi_aware()
    info = _desktop_info()
    proc_filter = args.get("process", "").strip().lower()
    if proc_filter.endswith(".exe"):
        proc_filter = proc_filter[:-4]
    include_untitled = bool(args.get("includeUntitled", False))
    cache = {}
    windows = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @WNDENUMPROC
    def enum_cb(hwnd, _lparam):
        visible = bool(user32.IsWindowVisible(hwnd))
        if not proc_filter and not visible:
            return True
        if not include_untitled and not proc_filter and user32.GetWindowTextLengthW(hwnd) == 0:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pname = _proc_name(int(pid.value), cache)
        if proc_filter and pname.lower() != proc_filter:
            return True
        windows.append({
            "hwnd": f"0x{int(hwnd):X}",
            "pid": int(pid.value),
            "process": pname,
            "className": _win_class(hwnd),
            "visible": visible,
            "minimized": bool(user32.IsIconic(hwnd)),
            "bounds": _win_bounds(hwnd),
        })
        return True

    user32.EnumWindows(enum_cb, 0)
    return {
        "ok": True,
        "autoSetFocus": False,
        **info,
        "windows": windows,
    }


def _validate_hwnd(raw_hwnd):
    if not raw_hwnd:
        raise ValueError("Hwnd is required")
    hwnd = int(str(raw_hwnd), 0)
    if hwnd <= 0 or not user32.IsWindow(hwnd):
        raise ValueError(f"Invalid window handle: {raw_hwnd}")
    return hwnd


def mode_inspect(args, raw):
    hwnd = _validate_hwnd(args.get("hwnd"))
    max_depth = max(1, min(8, int(args.get("maxDepth", 5))))
    limit = max(1, min(100, int(args.get("limit", 50))))
    name_rx = re.compile(args["name"], re.IGNORECASE) if args.get("name") else None
    auto_id_filter = args.get("automationId") or ""
    ctype_filter = _norm_ctype(args.get("controlType", ""))

    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    pname = _proc_name(int(pid.value), {})
    cls = _win_class(hwnd)
    visible = bool(user32.IsWindowVisible(hwnd))
    minimized = bool(user32.IsIconic(hwnd))

    root = raw.ElementFromHandle(hwnd)
    if not root:
        raise RuntimeError(f"ElementFromHandle returned null for 0x{hwnd:X}")
    walker = raw.ControlViewWalker
    controls = []
    truncated_by_limit = False

    def visit(node, depth, _scanned):
        nonlocal truncated_by_limit
        rec = _el_record(node, depth=depth, include_patterns=True)
        if name_rx and not name_rx.search(rec["name"]):
            return True
        if auto_id_filter and rec["automationId"] != auto_id_filter:
            return True
        if ctype_filter and rec["controlType"].lower() != ctype_filter:
            return True
        controls.append(rec)
        if len(controls) >= limit:
            truncated_by_limit = True
            return False
        return True

    scanned, stopped = _walk_controls(walker, root, max_depth, visit)
    deep_fallback = False

    has_filter = bool(name_rx or auto_id_filter or ctype_filter)
    if has_filter or (max_depth == 8 and scanned <= 1):
        try:
            cond = _build_condition(raw, ctype_filter, auto_id_filter)
            arr = root.FindAll(uia.TreeScope_Descendants, cond)
            count = int(arr.Length)
            if count > 0 and (len(controls) == 0 or has_filter):
                deep_fallback = True
                controls = []
                truncated_by_limit = False
                scanned = max(scanned, count + 1)
                for i in range(count):
                    node = arr.GetElement(i)
                    rec = _el_record(node, depth=None, include_patterns=True)
                    if name_rx and not name_rx.search(rec["name"]):
                        continue
                    if auto_id_filter and rec["automationId"] != auto_id_filter:
                        continue
                    if ctype_filter and rec["controlType"].lower() != ctype_filter:
                        continue
                    controls.append(rec)
                    if len(controls) >= limit:
                        truncated_by_limit = True
                        break
        except Exception:
            pass

    extra_diag = {}
    if len(controls) == 0 and scanned <= 1:
        root_pid = int(pid.value)
        try:
            child_hwnds = mb._same_pid_child_hwnds(user32, hwnd, root_pid, 10)
            extra_diag["childHwnds"] = [
                {"hwnd": f"0x{ch:X}", "className": _win_class(ch)} for ch in child_hwnds
            ]
        except Exception as exc:
            extra_diag["childHwndsError"] = str(exc)

        msaa_available = False
        msaa_child_count = None
        msaa_sample = []
        try:
            oleacc = ctypes.WinDLL("oleacc", use_last_error=True)
            comtypes.client.GetModule("oleacc.dll")
            from comtypes.gen.Accessibility import IAccessible

            acc = mb._get_client_accessible(oleacc, hwnd, IAccessible)
            if acc is not None:
                msaa_available = True
                raw_cnt = mb._property(acc, "accChildCount", 0)
                try:
                    msaa_child_count = int(raw_cnt) if raw_cnt is not None else 0
                except (TypeError, ValueError):
                    msaa_child_count = None
                if msaa_child_count and msaa_child_count > 0:
                    children, _ = mb._children(oleacc, acc, min(20, msaa_child_count), IAccessible)
                    for child_acc, cid in children[:20]:
                        cname = mb._property(child_acc, "accName", cid)
                        crole_id = mb._property(child_acc, "accRole", cid)
                        crole = mb._role_text(oleacc, crole_id)
                        msaa_sample.append({"name": cname, "role": crole, "childId": cid})
        except Exception as exc:
            extra_diag["msaaError"] = str(exc)

        extra_diag["msaaAvailable"] = msaa_available
        extra_diag["msaaChildCount"] = msaa_child_count
        if msaa_sample:
            extra_diag["msaaSample"] = msaa_sample

        if msaa_available:
            time.sleep(0.15)
            try:
                root = raw.ElementFromHandle(hwnd)
                if root:
                    cond = _build_condition(raw, ctype_filter, auto_id_filter)
                    arr = root.FindAll(uia.TreeScope_Descendants, cond)
                    count = int(arr.Length)
                    if count > 0:
                        deep_fallback = True
                        scanned = max(scanned, count + 1)
                        extra_diag["uiaWokeAfterMsaa"] = True
                        for i in range(count):
                            node = arr.GetElement(i)
                            rec = _el_record(node, depth=None, include_patterns=True)
                            if name_rx and not name_rx.search(rec["name"]):
                                continue
                            if auto_id_filter and rec["automationId"] != auto_id_filter:
                                continue
                            if ctype_filter and rec["controlType"].lower() != ctype_filter:
                                continue
                            controls.append(rec)
                            if len(controls) >= limit:
                                truncated_by_limit = True
                                break
            except Exception:
                pass

        if len(controls) == 0:
            try:
                ocr_script = os.path.join(os.environ.get("WIN_UIA_SCRIPT_DIR") or os.getcwd(), "window_ocr.py")
                cmd = [
                    sys.executable,
                    ocr_script,
                    "--hwnd",
                    f"0x{hwnd:X}",
                    "--pid",
                    str(root_pid),
                    "--language-tag",
                    "zh-Hans-CN",
                ]
                for kw in ("搜索", "确定", "取消", "登录", "发送"):
                    cmd.extend(["--keyword", kw])
                proc = subprocess.run(
                    cmd,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=15,
                    check=False,
                )
                stdout_lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
                if stdout_lines:
                    ocr_res = json.loads(stdout_lines[-1])
                    extra_diag["ocrStatus"] = ocr_res.get("status")
                    extra_diag["ocrHits"] = ocr_res.get("hits", [])
                    if ocr_res.get("error"):
                        extra_diag["ocrError"] = ocr_res.get("error")
                else:
                    extra_diag["ocrStatus"] = "no-output"
                    extra_diag["ocrError"] = proc.stderr.strip()[:400]
            except Exception as exc:
                extra_diag["ocrStatus"] = "error"
                extra_diag["ocrError"] = str(exc)

    win_info = {
        "hwnd": f"0x{hwnd:X}",
        "pid": int(pid.value),
        "process": pname,
        "className": cls,
        "visible": visible,
        "minimized": minimized,
        **extra_diag,
    }

    return {
        "ok": True,
        "autoSetFocus": False,
        "window": win_info,
        "controls": controls,
        "totalScanned": scanned,
        "deepQueryUsed": deep_fallback,
        "truncated": bool(truncated_by_limit or stopped),
    }


def _match_selector(el, sel, name_rx):
    try:
        if sel.get("automationId") not in (None, ""):
            if _safe_str(el.CurrentAutomationId) != str(sel["automationId"]):
                return False
        if sel.get("name") not in (None, ""):
            if bool(el.CurrentIsPassword) or _safe_str(el.CurrentName) != str(sel["name"]):
                return False
        if name_rx is not None:
            if bool(el.CurrentIsPassword) or not name_rx.search(_safe_str(el.CurrentName)):
                return False
        if sel.get("controlType") not in (None, ""):
            if _ctype_name(el.CurrentControlType).lower() != _norm_ctype(sel["controlType"]):
                return False
        if sel.get("className") not in (None, ""):
            if _safe_str(el.CurrentClassName) != str(sel["className"]):
                return False
        return True
    except Exception:
        return False


def _find_element(raw, walker, root, sel):
    index = int(sel.get("index", 0) or 0)
    name_rx = re.compile(sel["nameRegex"], re.IGNORECASE) if sel.get("nameRegex") else None
    found = []

    try:
        cond = _build_condition(
            raw,
            sel.get("controlType", ""),
            sel.get("automationId", ""),
            sel.get("name"),
        )
        arr = root.FindAll(uia.TreeScope_Descendants, cond)
        count = int(arr.Length)
        for i in range(count):
            node = arr.GetElement(i)
            if _match_selector(node, sel, name_rx):
                found.append(node)
                if len(found) > index:
                    return found[index], len(found)
    except Exception:
        found = []

    if not found:
        def visit(node, _depth, _scanned):
            if _match_selector(node, sel, name_rx):
                found.append(node)
                if len(found) > index:
                    return False
            return True

        _walk_controls(walker, root, 8, visit)

    if index >= len(found):
        return None, len(found)
    return found[index], len(found)


def _is_root_window_selector(act, root_class):
    if _norm_ctype(act.get("controlType", "")) != "window":
        return False
    if act.get("automationId") == "__root__":
        return True
    cls = act.get("className")
    if cls and cls == root_class:
        return True
    return False


# --- Authorized brief-foreground helpers (DPI-aware BitBlt/PrintWindow + WinRT OCR + SendInput) ---
class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _INPUT_UNION),
    ]


def _set_dpi_aware():
    fn = getattr(user32, "SetProcessDpiAwarenessContext", None)
    if fn is not None:
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = wintypes.BOOL
        fn(ctypes.c_void_p(-4))


def _bring_foreground(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
        time.sleep(0.08)
    fg = user32.GetForegroundWindow()
    if fg == hwnd:
        return
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    cur_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.BringWindowToTop.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    attached_fg = False
    attached_tgt = False
    try:
        if fg_tid and fg_tid != cur_tid:
            attached_fg = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
        if tgt_tid and tgt_tid != cur_tid:
            attached_tgt = bool(user32.AttachThreadInput(cur_tid, tgt_tid, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        if attached_tgt:
            user32.AttachThreadInput(cur_tid, tgt_tid, False)
        if attached_fg:
            user32.AttachThreadInput(cur_tid, fg_tid, False)
    for _ in range(20):
        if user32.GetForegroundWindow() == hwnd:
            return
        time.sleep(0.02)
    raise RuntimeError(f"Failed to bring HWND 0x{hwnd:X} to foreground")


def _restore_foreground(orig_fg, orig_cursor):
    try:
        if orig_cursor is not None:
            user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            user32.SetCursorPos.restype = wintypes.BOOL
            user32.SetCursorPos(int(orig_cursor[0]), int(orig_cursor[1]))
    except Exception:
        pass
    try:
        if orig_fg and user32.IsWindow(orig_fg) and user32.GetForegroundWindow() != orig_fg:
            _bring_foreground(orig_fg)
    except Exception:
        pass


def _join_ocr_words(words):
    parts = []
    for i, w in enumerate(words):
        t = str(w.text)
        if i > 0 and parts:
            prev = parts[-1]
            if prev and prev[-1].isascii() and prev[-1].isalnum() and t and t[0].isascii() and t[0].isalnum():
                parts.append(" ")
        parts.append(t)
    return "".join(parts).strip()


def _norm_ocr_label(s):
    return _safe_str(s).replace(" ", "").replace("阝东", "陈")


def _matches_contact(label, query):
    norm_l = _norm_ocr_label(label)
    norm_q = _norm_ocr_label(query)
    if not norm_q:
        return False
    if norm_q in norm_l:
        return True
    if len(norm_q) >= 3 and norm_q[1:] in norm_l:
        return True
    return False


def _capture_and_ocr(hwnd, prefer_bitblt=True):
    import asyncio
    import win32con
    import win32gui
    import win32ui
    from PIL import Image, ImageStat
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError(f"GetWindowRect failed for 0x{hwnd:X}")
    left, top = int(rect.left), int(rect.top)
    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"Invalid window dimensions: {width}x{height}")

    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    user32.PrintWindow.restype = wintypes.BOOL

    raw_bytes = None
    method = None

    def _try_bitblt():
        ddc = user32.GetDC(0)
        if not ddc:
            return None, None
        src = win32ui.CreateDCFromHandle(ddc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, width, height)
        prev = mem.SelectObject(bmp)
        try:
            mem.BitBlt((0, 0), (width, height), src, (left, top), win32con.SRCCOPY)
            px = bmp.GetBitmapBits(True)
            img = Image.frombytes("RGB", (width, height), px, "raw", "BGRX", 0, 1)
            if max(ImageStat.Stat(img).stddev) > 2.0:
                return px, "bitblt"
            return None, None
        finally:
            if prev is not None:
                mem.SelectObject(prev)
            mem.DeleteDC()
            win32gui.DeleteObject(bmp.GetHandle())
            user32.ReleaseDC(0, ddc)

    def _try_printwindow():
        wdc = user32.GetWindowDC(hwnd)
        if not wdc:
            return None, None
        src = win32ui.CreateDCFromHandle(wdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, width, height)
        prev = mem.SelectObject(bmp)
        try:
            for pw_flag in (3, 2, 0):
                if user32.PrintWindow(hwnd, mem.GetSafeHdc(), pw_flag):
                    px = bmp.GetBitmapBits(True)
                    img = Image.frombytes("RGB", (width, height), px, "raw", "BGRX", 0, 1)
                    if max(ImageStat.Stat(img).stddev) > 2.0:
                        return px, f"printwindow:{pw_flag}"
            return None, None
        finally:
            if prev is not None:
                mem.SelectObject(prev)
            mem.DeleteDC()
            win32gui.DeleteObject(bmp.GetHandle())
            user32.ReleaseDC(hwnd, wdc)

    if prefer_bitblt:
        raw_bytes, method = _try_bitblt()
        if raw_bytes is None:
            raw_bytes, method = _try_printwindow()
    else:
        raw_bytes, method = _try_printwindow()
        if raw_bytes is None:
            raw_bytes, method = _try_bitblt()

    if raw_bytes is None:
        raise RuntimeError("Failed to capture non-blank window pixels")

    tags = [str(lang.language_tag) for lang in OcrEngine.available_recognizer_languages]
    selected_tag = next((t for t in tags if t.lower() == "zh-hans-cn" or t.lower().startswith("zh-hans")), None)
    if selected_tag is None:
        raise RuntimeError(f"zh-Hans OCR language unavailable; available={tags}")
    engine = OcrEngine.try_create_from_language(Language(selected_tag))
    if engine is None:
        raise RuntimeError(f"Failed to create OcrEngine for {selected_tag}")

    async def _run_ocr():
        writer = DataWriter()
        writer.write_bytes(raw_bytes)
        buf = writer.detach_buffer()
        bmp = SoftwareBitmap(BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.IGNORE)
        bmp.copy_from_buffer(buf)
        try:
            return await engine.recognize_async(bmp)
        finally:
            bmp.close()

    ocr_res = asyncio.run(_run_ocr())
    lines = []
    for line in ocr_res.lines:
        words = list(line.words)
        if not words:
            continue
        label = _join_ocr_words(words)
        if not label:
            continue
        x1 = min(float(w.bounding_rect.x) for w in words)
        y1 = min(float(w.bounding_rect.y) for w in words)
        x2 = max(float(w.bounding_rect.x) + float(w.bounding_rect.width) for w in words)
        y2 = max(float(w.bounding_rect.y) + float(w.bounding_rect.height) for w in words)
        lines.append({
            "label": label,
            "x": round(x1, 1),
            "y": round(y1, 1),
            "width": round(x2 - x1, 1),
            "height": round(y2 - y1, 1),
            "cx": int(round((x1 + x2) / 2.0)),
            "cy": int(round((y1 + y2) / 2.0)),
        })
    lines.sort(key=lambda it: (it["y"], it["x"]))
    return {"left": left, "top": top, "width": width, "height": height, "method": method, "lines": lines}


def _send_inputs(items):
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    arr = (_INPUT * len(items))(*items)
    sent = user32.SendInput(len(items), arr, ctypes.sizeof(_INPUT))
    if sent != len(items):
        raise RuntimeError(f"SendInput sent {sent}/{len(items)} events")


def _click_phys(x, y):
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.SetCursorPos.restype = wintypes.BOOL
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.04)
    down = _INPUT(type=0, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=0x0002, time=0, dwExtraInfo=0)))
    up = _INPUT(type=0, union=_INPUT_UNION(mi=_MOUSEINPUT(dx=0, dy=0, mouseData=0, dwFlags=0x0004, time=0, dwExtraInfo=0)))
    _send_inputs([down, up])


def _send_vk(vk):
    down = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)))
    up = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0x0002, time=0, dwExtraInfo=0)))
    _send_inputs([down, up])


def _send_hotkey(vk_mod, vk_key):
    m_down = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_mod, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)))
    k_down = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_key, wScan=0, dwFlags=0, time=0, dwExtraInfo=0)))
    k_up = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_key, wScan=0, dwFlags=0x0002, time=0, dwExtraInfo=0)))
    m_up = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=vk_mod, wScan=0, dwFlags=0x0002, time=0, dwExtraInfo=0)))
    _send_inputs([m_down, k_down, k_up, m_up])


def _send_unicode_text(s):
    raw_u16 = s.encode("utf-16-le")
    for i in range(0, len(raw_u16), 2):
        code = int.from_bytes(raw_u16[i:i + 2], "little")
        down = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=code, dwFlags=0x0004, time=0, dwExtraInfo=0)))
        up = _INPUT(type=1, union=_INPUT_UNION(ki=_KEYBDINPUT(wVk=0, wScan=code, dwFlags=0x0006, time=0, dwExtraInfo=0)))
        _send_inputs([down, up])
        time.sleep(0.03)


def _fg_search_open(hwnd, query):
    _set_dpi_aware()
    orig_fg = user32.GetForegroundWindow()
    orig_cursor = _cursor_pos()
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    orig_clip = int(user32.GetClipboardSequenceNumber())
    noise = ("搜索", "搜一搜", "包含", "聊天记录", "网络结果", "公众号", "小程序", "文章", "视频号", "服务号", "功能", "查找微信号", "添加", "群聊")
    try:
        _bring_foreground(hwnd)
        time.sleep(0.30)
        init_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        w, h = init_ocr["width"], init_ocr["height"]
        left, top = init_ocr["left"], init_ocr["top"]
        search_hits = [ln for ln in init_ocr["lines"] if "搜索" in ln["label"] and ln["x"] < w * 0.35 and ln["y"] < h * 0.14]
        clicked_search = None
        if search_hits:
            sh = search_hits[0]
            tx = left + sh["cx"] + 30
            ty = top + sh["cy"]
            clicked_search = [tx, ty]
            _click_phys(tx, ty)
            time.sleep(0.20)
        else:
            _send_hotkey(0x11, 0x46)  # Ctrl+F
            time.sleep(0.20)
        _send_hotkey(0x11, 0x41)  # Ctrl+A
        time.sleep(0.05)
        _send_vk(0x08)  # Backspace
        time.sleep(0.10)
        fg_before_type = user32.GetForegroundWindow()
        _send_unicode_text(query)
        time.sleep(1.00)

        search_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        w2, h2 = search_ocr["width"], search_ocr["height"]
        left2, top2 = search_ocr["left"], search_ocr["top"]
        left_panel = [ln for ln in search_ocr["lines"] if ln["x"] < w2 * 0.45 and ln["y"] < h2 * 0.78]
        candidates = [
            ln for ln in left_panel
            if ln["y"] > max(105, h2 * 0.11)
            and _matches_contact(ln["label"], query)
            and not any(bad in ln["label"] for bad in noise)
        ]
        candidates.sort(key=lambda it: it["y"])
        if not candidates:
            _send_vk(0x1B)  # Esc
            time.sleep(0.15)
            return {
                "ok": False,
                "error": f"No user candidate matching {query!r} found in search results",
                "captureMethod": search_ocr["method"],
                "windowRect": [left, top, w, h],
                "clickedSearch": clicked_search,
                "fgBeforeType": f"0x{fg_before_type:X}" if fg_before_type else None,
                "leftPanelLines": [{"label": ln["label"], "x": ln["x"], "y": ln["y"]} for ln in left_panel[:20]],
                "clipboardUnchanged": int(user32.GetClipboardSequenceNumber()) == orig_clip,
            }

        first = candidates[0]
        _click_phys(left2 + first["cx"], top2 + first["cy"])
        time.sleep(0.65)

        after_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        w3, h3 = after_ocr["width"], after_ocr["height"]
        header_lines = [ln for ln in after_ocr["lines"] if ln["x"] > w3 * 0.25 and ln["y"] < h3 * 0.14]
        send_hits = [ln for ln in after_ocr["lines"] if "发送" in ln["label"] and ln["x"] > w3 * 0.65 and ln["y"] > h3 * 0.70]
        header_matched = any(_matches_contact(ln["label"], query) for ln in header_lines)
        return {
            "ok": True,
            "captureMethod": after_ocr["method"],
            "windowSize": [w3, h3],
            "clickedCandidate": first,
            "candidateCount": len(candidates),
            "headerLines": header_lines,
            "headerMatched": header_matched,
            "sendButton": send_hits[0] if send_hits else None,
            "clipboardUnchanged": int(user32.GetClipboardSequenceNumber()) == orig_clip,
        }
    finally:
        _restore_foreground(orig_fg, orig_cursor)


def _fg_send_message(hwnd, msg, expected_header):
    _set_dpi_aware()
    orig_fg = user32.GetForegroundWindow()
    orig_cursor = _cursor_pos()
    user32.GetClipboardSequenceNumber.argtypes = []
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    orig_clip = int(user32.GetClipboardSequenceNumber())
    try:
        _bring_foreground(hwnd)
        time.sleep(0.25)
        before_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        w, h = before_ocr["width"], before_ocr["height"]
        left, top = before_ocr["left"], before_ocr["top"]
        header_lines = [ln for ln in before_ocr["lines"] if ln["x"] > w * 0.25 and ln["y"] < h * 0.14]
        if expected_header and not any(_matches_contact(ln["label"], expected_header) for ln in header_lines):
            raise RuntimeError(f"Active chat header mismatch; expected {expected_header!r}, got {[l['label'] for l in header_lines]}")
        send_hits = [ln for ln in before_ocr["lines"] if "发送" in ln["label"] and ln["x"] > w * 0.65 and ln["y"] > h * 0.70]
        if not send_hits:
            raise RuntimeError("Send button ('发送') not found in bottom-right of chat window")
        send_btn = send_hits[0]

        def _count_msg(lines_all):
            area = [ln for ln in lines_all if ln["x"] > w * 0.25 and h * 0.12 < ln["y"] < send_btn["y"] - 110]
            cnt = sum(1 for ln in area if ln["label"].strip() == msg)
            return cnt, area

        before_cnt, _ = _count_msg(before_ocr["lines"])
        input_x = left + int(w * 0.55)
        input_y = top + int(send_btn["y"] - 55)
        _click_phys(input_x, input_y)
        time.sleep(0.15)
        _send_unicode_text(msg)
        time.sleep(0.30)

        input_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        input_lines = [
            ln for ln in input_ocr["lines"]
            if ln["x"] > w * 0.25 and send_btn["y"] - 110 <= ln["y"] <= send_btn["y"]
        ]
        typed_observed = any(msg in _norm_ocr_label(ln["label"]) for ln in input_lines)
        if not typed_observed:
            _send_hotkey(0x11, 0x41)
            time.sleep(0.05)
            _send_vk(0x08)
            raise RuntimeError(f"Typed message {msg!r} was not observed by OCR in input box; inputLines={input_lines}")

        _click_phys(left + send_btn["cx"], top + send_btn["cy"])
        time.sleep(0.65)

        after_ocr = _capture_and_ocr(hwnd, prefer_bitblt=True)
        after_input_lines = [
            ln for ln in after_ocr["lines"]
            if ln["x"] > w * 0.25 and send_btn["y"] - 110 <= ln["y"] <= send_btn["y"] - 5
            and "发送" not in ln["label"]
        ]
        input_cleared = not any(msg in _norm_ocr_label(ln["label"]) for ln in after_input_lines)
        after_cnt, after_area = _count_msg(after_ocr["lines"])
        bottom_lines = [{"label": ln["label"], "x": ln["x"], "y": ln["y"]} for ln in after_area[-4:]]
        return {
            "ok": True,
            "headerVerified": True,
            "headerLines": header_lines,
            "sendButton": send_btn,
            "typedObserved": typed_observed,
            "inputCleared": input_cleared,
            "beforeMsgCount": before_cnt,
            "afterMsgCount": after_cnt,
            "afterBottomLines": bottom_lines,
            "clipboardUnchanged": int(user32.GetClipboardSequenceNumber()) == orig_clip,
        }
    finally:
        _restore_foreground(orig_fg, orig_cursor)


def mode_batch(args, raw):
    hwnd = _validate_hwnd(args.get("hwnd"))
    if isinstance(args.get("actions"), list):
        actions = args["actions"]
    else:
        actions_file = args.get("actionsFile") or ""
        if not actions_file or not os.path.isfile(actions_file):
            raise ValueError(f"ActionsFile not found: {actions_file}")
        with open(actions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        actions = data.get("actions", [])
    has_legacy_foreground = any(str(a.get("automationId", "")).startswith("__fg_") for a in actions)
    if has_legacy_foreground and not bool(args.get("allowForeground")):
        raise RuntimeError("Legacy __fg_* actions require the explicit -AllowForeground switch")
    if has_legacy_foreground:
        _set_dpi_aware()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    root_pid = int(pid.value)
    root_class = _win_class(hwnd)
    root = raw.ElementFromHandle(hwnd)
    if not root:
        raise RuntimeError(f"ElementFromHandle returned null for 0x{hwnd:X}")
    walker = raw.ControlViewWalker

    fg_before = user32.GetForegroundWindow()
    ptr_before = _cursor_pos()
    results = []

    for idx, act in enumerate(actions):
        op = str(act.get("op", "")).lower()
        step_res = {"index": idx, "op": op}
        try:
            if op == "wait":
                if "ms" in act:
                    time.sleep(int(act["ms"]) / 1000.0)
                    step_res["waitedMs"] = int(act["ms"])
                    results.append(step_res)
                    continue
                timeout_ms = int(act.get("timeout_ms", 5000))
                deadline = time.monotonic() + timeout_ms / 1000.0
                el = None
                while True:
                    el, _ = _find_element(raw, walker, root, act)
                    if el is not None or time.monotonic() >= deadline:
                        break
                    time.sleep(0.05)
                if el is None:
                    raise RuntimeError(f"Timed out after {timeout_ms}ms waiting for selector")
                step_res["target"] = _el_record(el, include_patterns=False)
                results.append(step_res)
                continue

            if op in ("expand", "collapse") and _is_root_window_selector(act, root_class):
                if op == "expand":
                    res = wb.show_noactivate(hwnd, root_pid)
                else:
                    res = wb.set_noactivate_state(hwnd, root_pid, "hidden")
                step_res["showResult"] = res
                if not res.get("ok") and res.get("effect") != "state-reached":
                    raise RuntimeError(res.get("action") or res.get("effect") or "root window state change failed")
                results.append(step_res)
                continue

            if op == "set_value" and act.get("automationId") == "__fg_search_open__":
                res = _fg_search_open(hwnd, str(act["value"]))
                if not res.get("ok"):
                    step_res["diag"] = res
                    raise RuntimeError(res.get("error", "foreground search failed"))
                step_res["fgSearchResult"] = res
                results.append(step_res)
                continue

            if op == "set_value" and act.get("automationId") == "__fg_send_message__":
                res = _fg_send_message(hwnd, str(act["value"]), str(act.get("name", "")))
                step_res["fgSendResult"] = res
                results.append(step_res)
                continue

            el, match_count = _find_element(raw, walker, root, act)
            if el is None:
                raise RuntimeError(f"Selector matched {match_count} element(s); index {act.get('index', 0)} not found")
            step_res["target"] = _el_record(el, include_patterns=True)

            if op == "set_value":
                if bool(el.CurrentIsPassword):
                    raise RuntimeError("Refusing set_value on password control")
                if not bool(el.GetCurrentPropertyValue(uia.UIA_IsValuePatternAvailablePropertyId)):
                    raise RuntimeError("Target control does not support ValuePattern")
                pat = el.GetCurrentPattern(uia.UIA_ValuePatternId).QueryInterface(uia.IUIAutomationValuePattern)
                val = str(act["value"])
                pat.SetValue(val)
                matched = False
                for _ in range(10):
                    if _safe_str(pat.CurrentValue) == val:
                        matched = True
                        break
                    time.sleep(0.05)
                step_res["valueMatched"] = matched
                if not matched:
                    raise RuntimeError("ValuePattern readback did not match written string")

            elif op == "invoke":
                if not bool(el.GetCurrentPropertyValue(uia.UIA_IsInvokePatternAvailablePropertyId)):
                    raise RuntimeError("Target control does not support InvokePattern")
                pat = el.GetCurrentPattern(uia.UIA_InvokePatternId).QueryInterface(uia.IUIAutomationInvokePattern)
                pat.Invoke()

            elif op == "select":
                if not bool(el.GetCurrentPropertyValue(uia.UIA_IsSelectionItemPatternAvailablePropertyId)):
                    raise RuntimeError("Target control does not support SelectionItemPattern")
                pat = el.GetCurrentPattern(uia.UIA_SelectionItemPatternId).QueryInterface(uia.IUIAutomationSelectionItemPattern)
                pat.Select()
                step_res["isSelected"] = bool(pat.CurrentIsSelected)

            elif op == "toggle":
                if not bool(el.GetCurrentPropertyValue(uia.UIA_IsTogglePatternAvailablePropertyId)):
                    raise RuntimeError("Target control does not support TogglePattern")
                pat = el.GetCurrentPattern(uia.UIA_TogglePatternId).QueryInterface(uia.IUIAutomationTogglePattern)
                desired = act.get("state")
                if desired is None:
                    pat.Toggle()
                else:
                    want_on = str(desired).lower() in ("on", "true", "1", "checked")
                    target_state = 1 if want_on else 0
                    for _ in range(3):
                        if int(pat.CurrentToggleState) == target_state:
                            break
                        pat.Toggle()
                        time.sleep(0.05)
                    if int(pat.CurrentToggleState) != target_state:
                        raise RuntimeError("Toggle state did not reach desired value")
                step_res["toggleState"] = _el_toggle_state(el)

            elif op in ("expand", "collapse"):
                if not bool(el.GetCurrentPropertyValue(uia.UIA_IsExpandCollapsePatternAvailablePropertyId)):
                    raise RuntimeError("Target control does not support ExpandCollapsePattern")
                pat = el.GetCurrentPattern(uia.UIA_ExpandCollapsePatternId).QueryInterface(uia.IUIAutomationExpandCollapsePattern)
                if op == "expand":
                    pat.Expand()
                else:
                    pat.Collapse()

            elif op == "read":
                info = _el_record(el, include_patterns=True)
                info["toggleState"] = _el_toggle_state(el)
                info["isSelected"] = _el_is_selected(el)
                key = act.get("key")
                if key:
                    step_res["key"] = key
                    step_res["readValue"] = info.get(key)
                else:
                    step_res["info"] = info

            elif op == "assert":
                field = str(act.get("field", "name"))
                info = _el_record(el, include_patterns=False)
                info["toggleState"] = _el_toggle_state(el)
                info["isSelected"] = _el_is_selected(el)
                actual = info.get(field)
                actual_str = "" if actual is None else str(actual)
                if "equals" in act and actual_str != str(act["equals"]):
                    raise RuntimeError(f"Assertion failed: {field}={actual_str!r} != {act['equals']!r}")
                if "contains" in act and str(act["contains"]) not in actual_str:
                    raise RuntimeError(f"Assertion failed: {field}={actual_str!r} does not contain {act['contains']!r}")
                step_res["asserted"] = field

            else:
                raise RuntimeError(f"Unsupported op: {op}")

            results.append(step_res)
        except Exception as exc:
            step_res["error"] = str(exc)
            results.append(step_res)
            fg_after = user32.GetForegroundWindow()
            return {
                "ok": False,
                "autoSetFocus": False,
                "error": f"action[{idx}] ({op}) failed: {exc}",
                "results": results,
                "foregroundBefore": f"0x{fg_before:X}" if fg_before else None,
                "foregroundAfter": f"0x{fg_after:X}" if fg_after else None,
                "pointerBefore": ptr_before,
                "pointerAfter": _cursor_pos(),
            }

    fg_after = user32.GetForegroundWindow()
    return {
        "ok": True,
        "autoSetFocus": False,
        "results": results,
        "foregroundBefore": f"0x{fg_before:X}" if fg_before else None,
        "foregroundAfter": f"0x{fg_after:X}" if fg_after else None,
        "pointerBefore": ptr_before,
        "pointerAfter": _cursor_pos(),
    }


def _dispatch(args, raw):
    mode = args.get("mode")
    if mode == "windows":
        return mode_windows(args)
    if mode == "inspect":
        return mode_inspect(args, raw)
    if mode == "batch":
        return mode_batch(args, raw)
    return {"ok": False, "error": f"Unsupported mode: {mode}"}


def _run_daemon():
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    raw = None
    try:
        raw, _safe = _init_uia()
        emit({"ready": True, "autoSetFocus": False})
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            text = line.strip()
            if not text:
                continue
            req_id = None
            try:
                req = json.loads(text)
                if not isinstance(req, dict):
                    raise ValueError("request must be a JSON object")
                req_id = req.get("id")
                args = req.get("args")
                if not isinstance(args, dict):
                    raise ValueError("args must be a JSON object")
                out = _dispatch(args, raw)
            except Exception as exc:
                out = {"ok": False, "error": str(exc)}
            emit({"id": req_id, "result": out})
        return 0
    except Exception as exc:
        emit({"ready": False, "error": str(exc)})
        return 1
    finally:
        if raw is not None:
            comtypes.CoUninitialize()


def main():
    if "--daemon" in sys.argv[1:]:
        return _run_daemon()
    args_json = None
    if "--args-json" in sys.argv[1:]:
        idx = sys.argv.index("--args-json")
        if idx + 1 < len(sys.argv):
            args_json = sys.argv[idx + 1]
    args = json.loads(args_json if args_json is not None else os.environ.get("WIN_UIA_ARGS", "{}"))
    raw = None
    try:
        raw, _safe = _init_uia()
        out = _dispatch(args, raw)
        emit(out)
        return 0 if out.get("ok") else 1
    except Exception as exc:
        emit({"ok": False, "error": str(exc)})
        return 1
    finally:
        if raw is not None:
            comtypes.CoUninitialize()


if __name__ == "__main__":
    raise SystemExit(main())
