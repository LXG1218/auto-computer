#!/usr/bin/env python3
"""Narrow Windows UIA adapter that never asks UIA to set keyboard focus.

The caller must supply an exact top-level HWND and its expected process ID.
Discovery is exact-match only. Mutations use only a control's advertised UIA
Value, Invoke, or SelectionItem pattern; there is no keyboard/mouse fallback.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

import win_background as wb
from desktop_policy import ensure_unprotected_pid
from _common import ensure_comtypes_overlay, parse_json_arg


CLSID_CUIAutomation8 = "{e22ad333-b25f-460c-83d0-0581107395c9}"
IID_IUIAutomation2 = "{34723aff-0dd6-49d3-abf3-56f8c57f1d58}"
UIA_E_ELEMENTNOTAVAILABLE = 0x80040201
PASSWORD_REDACTION = "<redacted>"
MAX_WAIT_SECONDS = 5.0


class AdapterError(RuntimeError):
    pass


class ObservedActionError(AdapterError):
    def __init__(self, message: str, action_attempted: bool,
                 observation: dict[str, Any] | None):
        super().__init__(message)
        self.action_attempted = action_attempted
        self.observation = observation


def _load_comtypes():
    ensure_comtypes_overlay()
    try:
        import comtypes
        import comtypes.client
    except ImportError as exc:
        raise AdapterError(
            "comtypes is required. Add the staged pure-comtypes overlay to PYTHONPATH."
        ) from exc
    # Generate bindings from the system's UIAutomationCore typelib; no package
    # download or external service is used by this script.
    comtypes.client.GetModule(os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                           "System32", "UIAutomationCore.dll"))
    from comtypes.gen import UIAutomationClient as uia
    return comtypes, comtypes.client, uia


def _get(obj: Any, *names: str) -> Any:
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AdapterError(f"UIA typelib is missing expected member: {' / '.join(names)}")


def _text(value: Any) -> str:
    return "" if value is None else str(value)


class Adapter:
    def __init__(self, hwnd: int, expected_pid: int):
        if os.name != "nt":
            raise AdapterError("This adapter runs only on Windows.")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                         ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.hwnd = hwnd
        if not self.user32.IsWindow(hwnd):
            raise AdapterError(f"HWND 0x{hwnd:x} is not a valid window.")
        actual_pid = wintypes.DWORD()
        if not self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(actual_pid)):
            raise ctypes.WinError(ctypes.get_last_error())
        if actual_pid.value != expected_pid:
            raise AdapterError(
                f"HWND process mismatch: expected {expected_pid}, got {actual_pid.value}."
            )
        ensure_unprotected_pid(expected_pid)
        self.pid = expected_pid

        self.comtypes, client, self.uia = _load_comtypes()
        self.comtypes.CoInitialize()
        try:
            self.raw = client.CreateObject(CLSID_CUIAutomation8,
                                           interface=self.uia.IUIAutomation)
            self.safe = self.raw.QueryInterface(self.uia.IUIAutomation2)
        except Exception as exc:
            self.comtypes.CoUninitialize()
            raise AdapterError(f"Could not create/query CUIAutomation8 as IUIAutomation2: {exc}") from exc

        # This must precede even target-window element discovery.
        prop_name = "AutoSetFocus" if hasattr(type(self.safe), "AutoSetFocus") else "autoSetFocus"
        setattr(self.safe, prop_name, False)
        if bool(_get(self.safe, "AutoSetFocus", "autoSetFocus")):
            self.close()
            raise AdapterError("IUIAutomation2 AutoSetFocus readback was not FALSE; refusing UIA.")
        self.root = self.raw.ElementFromHandle(hwnd)
        if not self.root:
            self.close()
            raise AdapterError("UIA could not resolve the exact target HWND.")
        if int(self.root.CurrentProcessId) != expected_pid:
            self.close()
            raise AdapterError("UIA root process ID did not match the expected PID.")
        self.focus_off_verified = True

    def close(self):
        if getattr(self, "comtypes", None) is not None:
            self.comtypes.CoUninitialize()
            self.comtypes = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _condition(self, args):
        uia = self.uia
        pid = _get(uia, "UIA_ProcessIdPropertyId")
        auto_id = _get(uia, "UIA_AutomationIdPropertyId")
        name = _get(uia, "UIA_NamePropertyId")
        ctype = _get(uia, "UIA_ControlTypePropertyId")
        conds = [self.raw.CreatePropertyCondition(pid, self.pid)]
        if args.name is not None:
            conds.append(self.raw.CreatePropertyCondition(name, args.name))
        if args.automation_id is not None:
            conds.append(self.raw.CreatePropertyCondition(auto_id, args.automation_id))
        if args.control_type is not None:
            control_types = {
                "button": _get(uia, "UIA_ButtonControlTypeId"),
                "edit": _get(uia, "UIA_EditControlTypeId"),
                "text": _get(uia, "UIA_TextControlTypeId"),
                "list": _get(uia, "UIA_ListControlTypeId"),
                "listitem": _get(uia, "UIA_ListItemControlTypeId"),
                "combobox": _get(uia, "UIA_ComboBoxControlTypeId"),
                "checkbox": _get(uia, "UIA_CheckBoxControlTypeId"),
                "radiobutton": _get(uia, "UIA_RadioButtonControlTypeId"),
            }
            conds.append(self.raw.CreatePropertyCondition(ctype,
                                                          control_types[args.control_type]))
        result = conds[0]
        for cond in conds[1:]:
            result = self.raw.CreateAndCondition(result, cond)
        return result

    def find(self, args):
        if args.name is None and args.automation_id is None:
            raise AdapterError("find requires exact --name or --automation-id.")
        scope = _get(self.uia, "TreeScope_Descendants")
        matches = self.root.FindAll(scope, self._condition(args))
        count = int(matches.Length)
        if count == 0:
            raise AdapterError("No exact matching control found inside the validated HWND.")
        if count != 1:
            raise AdapterError(f"Exact selector matched {count} controls; refusing ambiguity.")
        return matches.GetElement(0)

    def record(self, el):
        password = bool(el.CurrentIsPassword)
        return {
            "name": PASSWORD_REDACTION if password else _text(el.CurrentName),
            "automation_id": _text(el.CurrentAutomationId),
            "control_type_id": int(el.CurrentControlType),
            "process_id": int(el.CurrentProcessId),
            "is_password": password,
        }

    def value(self, el):
        if bool(el.CurrentIsPassword):
            raise AdapterError("Password controls are not readable or writable.")
        self._require_pattern(el, "UIA_IsValuePatternAvailablePropertyId")
        pat = el.GetCurrentPattern(_get(self.uia, "UIA_ValuePatternId"))
        return pat.QueryInterface(self.uia.IUIAutomationValuePattern)

    def _require_pattern(self, el, availability_property):
        available = el.GetCurrentPropertyValue(_get(self.uia, availability_property))
        if not bool(available):
            raise AdapterError(f"Control does not advertise {availability_property.removeprefix('UIA_Is').removesuffix('AvailablePropertyId')}.")

    def perform(self, args):
        mutating = args.operation in ("set-value", "invoke", "select")
        before, watch = wb._begin_observation() if mutating else (None, None)
        action_attempted = False
        observation = None
        foreground_gate = None
        result: dict[str, Any] = {}
        error = None
        try:
            if args.operation in ("invoke", "select"):
                self._validate_expected(args)
            el = self.find(args)
            if mutating and bool(el.CurrentIsPassword):
                raise AdapterError("Password controls cannot be targeted for mutation.")
            result = {"target": self.record(el)}
            if args.operation == "find":
                pass
            elif args.operation == "inspect":
                if bool(el.CurrentIsPassword):
                    result["value_pattern_available"] = None
                    result["value"] = PASSWORD_REDACTION
                else:
                    available = bool(el.GetCurrentPropertyValue(
                        _get(self.uia, "UIA_IsValuePatternAvailablePropertyId")))
                    result["value_pattern_available"] = available
                    result["value"] = _text(self.value(el).CurrentValue) if available else None
            elif args.operation == "set-value":
                pat = self.value(el)
                foreground_gate = wb.foreground_write_gate(self.hwnd, before, watch, args.foreground_demo)
                if not foreground_gate["allowed"]:
                    raise AdapterError(f"foreground_write_paused: {foreground_gate['reason']}")
                action_attempted = True
                pat.SetValue(args.value)
                result["verified_value"] = self._wait_value(el, args.value)
            elif args.operation == "invoke":
                self._require_pattern(el, "UIA_IsInvokePatternAvailablePropertyId")
                pat = el.GetCurrentPattern(_get(self.uia, "UIA_InvokePatternId"))
                pattern = pat.QueryInterface(self.uia.IUIAutomationInvokePattern)
                foreground_gate = wb.foreground_write_gate(self.hwnd, before, watch, args.foreground_demo)
                if not foreground_gate["allowed"]:
                    raise AdapterError(f"foreground_write_paused: {foreground_gate['reason']}")
                action_attempted = True
                pattern.Invoke()
                result["verified_state"] = self._wait_expected(args)
            elif args.operation == "select":
                self._require_pattern(el, "UIA_IsSelectionItemPatternAvailablePropertyId")
                pat = el.GetCurrentPattern(_get(self.uia, "UIA_SelectionItemPatternId"))
                pattern = pat.QueryInterface(self.uia.IUIAutomationSelectionItemPattern)
                foreground_gate = wb.foreground_write_gate(self.hwnd, before, watch, args.foreground_demo)
                if not foreground_gate["allowed"]:
                    raise AdapterError(f"foreground_write_paused: {foreground_gate['reason']}")
                action_attempted = True
                pattern.Select()
                result["verified_state"] = self._wait_expected(args)
        except Exception as exc:
            error = exc
        finally:
            if mutating:
                try:
                    observed = wb._observe(before, watch)
                    baseline = before["foreground"]
                    foreground_measured = (
                        bool(observed.get("foreground_hook_installed"))
                        and bool(observed.get("foreground_observed"))
                        and baseline is not None and baseline != 0
                        and observed["foreground_after"] is not None
                    )
                    observed["foreground_unchanged"] = (
                        observed["foreground_after"] == observed["foreground_before"]
                        and not observed["foreground_events"]
                    ) if foreground_measured else None
                    observed["foreground_observation"] = (
                        "unobserved_baseline" if baseline == 0 else
                        "changed" if observed["foreground_unchanged"] is False else
                        "unchanged" if observed["foreground_unchanged"] is True else "incomplete"
                    )
                    observed["pointer_unchanged"] = (
                        observed["pointer_before"] == observed["pointer_after"]
                    ) if observed["pointer_before"] is not None and observed["pointer_after"] is not None else None
                    observed["clipboard_sequence_unchanged"] = (
                        observed["clipboard_sequence_before"] == observed["clipboard_sequence_after"]
                    )
                    if foreground_gate is not None:
                        observed["foreground_write_gate"] = foreground_gate
                    observation = observed
                except Exception as exc:
                    observation = {"foreground_observation": "incomplete",
                                   "observation_error": str(exc)}
                finally:
                    watch.close()
        if error is not None:
            if mutating:
                raise ObservedActionError(str(error), action_attempted, observation) from error
            raise error
        if mutating:
            result["action_attempted"] = action_attempted
            result["interaction_observation"] = observation
            if action_attempted:
                fg_unchanged = observation.get("foreground_unchanged") if isinstance(observation, dict) else None
                if fg_unchanged is False:
                    result["ok"] = False
                    result["acceptance"] = "foreground-changed-after-action"
                elif fg_unchanged is not True:
                    result["ok"] = False
                    result["acceptance"] = "foreground-unobserved-after-action"
        return result

    def _wait_value(self, el, expected):
        end = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            try:
                got = _text(self.value(el).CurrentValue)
                if got == expected:
                    return got
            except Exception:
                pass
            if time.monotonic() >= end:
                raise AdapterError("Timed out verifying exact UIA ValuePattern value.")
            time.sleep(0.05)

    def _wait_expected(self, args):
        self._validate_expected(args)
        end = time.monotonic() + MAX_WAIT_SECONDS
        while True:
            try:
                expected = argparse.Namespace(name=args.expect_name,
                                              automation_id=args.expect_automation_id,
                                              control_type=args.expect_control_type)
                status = self.find(expected)
                if args.expect_name is not None and _text(status.CurrentName) != args.expect_name:
                    status = None
                if args.expect_value is not None:
                    if status is not None and bool(status.CurrentIsPassword):
                        raise AdapterError("Expected state resolves to a password control.")
                    if status is not None:
                        value = _text(self.value(status).CurrentValue)
                        if value == args.expect_value:
                            return {"name": _text(status.CurrentName), "value": value}
                elif status is not None:
                    return {"name": _text(status.CurrentName)}
            except AdapterError as exc:
                # An absent/not-yet-updated exact selector can become true.
                # Ambiguous selectors and unsafe controls are terminal.
                if "No exact matching control" not in str(exc):
                    raise
            if time.monotonic() >= end:
                raise AdapterError("Timed out waiting for the expected exact state.")
            time.sleep(0.05)

    @staticmethod
    def _validate_expected(args):
        if args.expect_name is None and args.expect_value is None:
            raise AdapterError("invoke/select requires --expect-name or --expect-value for bounded verification.")
        if args.expect_name is None and args.expect_automation_id is None:
            raise AdapterError("invoke/select expected state requires exact --expect-name or --expect-automation-id selector.")


def _parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hwnd", required=True, type=lambda s: int(s, 0),
                   help="exact top-level HWND (decimal or 0x-prefixed hex)")
    p.add_argument("--pid", required=True, type=int, help="expected owning process ID")
    p.add_argument("--foreground-demo", action="store_true",
                   help="allow a mutation only while the exact target top-level window is foreground")
    p.add_argument("--name", help="exact target Name")
    p.add_argument("--automation-id", help="exact target AutomationId")
    p.add_argument("--control-type", choices=("button", "edit", "text", "list",
                   "listitem", "combobox", "checkbox", "radiobutton"))
    p.add_argument("--expect-name", help="exact status Name to verify after invoke/select")
    p.add_argument("--expect-automation-id", help="exact status AutomationId")
    p.add_argument("--expect-control-type", choices=("button", "edit", "text", "list",
                   "listitem", "combobox", "checkbox", "radiobutton"))
    p.add_argument("--expect-value", help="exact non-password status Value")
    sub = p.add_subparsers(dest="operation", required=True)
    sub.add_parser("find", help="find one exact match and return metadata only")
    sub.add_parser("inspect", help="read matching element metadata and advertised ValuePattern")
    sv = sub.add_parser("set-value", help="set a supported non-password ValuePattern")
    sv.add_argument("--value", required=True)
    sub.add_parser("invoke", help="invoke a supported control; provide expected status")
    sub.add_parser("select", help="select a supported item; provide expected status")
    bt = sub.add_parser("batch", help="run multiple sequential UIA actions in one adapter session")
    bt.add_argument("--actions", required=True, help="JSON array or file path of action objects")
    return p


VALID_CONTROL_TYPES = (
    "button", "edit", "text", "list",
    "listitem", "combobox", "checkbox", "radiobutton",
)


def _parse_strict_bool(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise AdapterError(f"{field_name} must be a JSON boolean (true/false), got {type(value).__name__}: {value!r}")
    return value


def _run_batch(adapter: Adapter, raw_actions: Any, default_foreground_demo: bool) -> dict[str, Any]:
    if not isinstance(raw_actions, list) or not raw_actions:
        raise AdapterError("batch --actions must be a non-empty JSON array.")
    default_fg = _parse_strict_bool(default_foreground_demo, "foreground_demo")
    valid_ops = {"find", "inspect", "set-value", "invoke", "select"}
    prepared: list[argparse.Namespace] = []
    for idx, item in enumerate(raw_actions):
        if not isinstance(item, dict):
            raise AdapterError(f"batch action[{idx}] must be a JSON object.")
        op = str(item.get("operation") or "").strip()
        if op not in valid_ops:
            raise AdapterError(f"batch action[{idx}] unsupported operation: {op!r}")
        control_type = item.get("control_type")
        if control_type is not None and control_type not in VALID_CONTROL_TYPES:
            raise AdapterError(f"batch action[{idx}] invalid control_type: {control_type!r}")
        expect_control_type = item.get("expect_control_type")
        if expect_control_type is not None and expect_control_type not in VALID_CONTROL_TYPES:
            raise AdapterError(f"batch action[{idx}] invalid expect_control_type: {expect_control_type!r}")
        if "foreground_demo" in item:
            fg_demo = _parse_strict_bool(item["foreground_demo"], f"batch action[{idx}].foreground_demo")
        else:
            fg_demo = default_fg
        if op == "set-value" and item.get("value") is None:
            raise AdapterError(f"batch action[{idx}] set-value requires a non-null 'value'.")
        step_ns = argparse.Namespace(
            operation=op,
            name=item.get("name"),
            automation_id=item.get("automation_id"),
            control_type=control_type,
            value=item.get("value"),
            expect_name=item.get("expect_name"),
            expect_automation_id=item.get("expect_automation_id"),
            expect_control_type=expect_control_type,
            expect_value=item.get("expect_value"),
            foreground_demo=fg_demo,
        )
        if op in ("invoke", "select"):
            adapter._validate_expected(step_ns)
        prepared.append(step_ns)

    steps: list[dict[str, Any]] = []
    for idx, step_ns in enumerate(prepared):
        try:
            res = adapter.perform(step_ns)
        except Exception as exc:
            failed_step: dict[str, Any] = {
                "index": idx,
                "operation": step_ns.operation,
                "ok": False,
                "error": str(exc),
                "action_attempted": exc.action_attempted if isinstance(exc, ObservedActionError) else False,
            }
            if isinstance(exc, ObservedActionError) and exc.observation is not None:
                failed_step["interaction_observation"] = exc.observation
            steps.append(failed_step)
            return {
                "ok": False,
                "error": str(exc),
                "failed_index": idx,
                "completed_count": idx,
                "count": len(steps),
                "steps": steps,
            }
        res["operation"] = step_ns.operation
        res["index"] = idx
        steps.append(res)
        if not res.get("ok", True):
            return {
                "ok": False,
                "failed_index": idx,
                "completed_count": idx,
                "count": len(steps),
                "acceptance": res.get("acceptance"),
                "steps": steps,
            }
    return {"ok": True, "count": len(steps), "steps": steps}


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.pid <= 0:
        raise SystemExit("--pid must be positive")
    if args.operation in ("invoke", "select") and args.expect_name is None and args.expect_value is None:
        raise SystemExit("invoke/select requires --expect-name or --expect-value")
    if args.operation in ("invoke", "select") and args.expect_name is None and args.expect_automation_id is None:
        raise SystemExit("invoke/select expected state requires --expect-name or --expect-automation-id selector")
    adapter = None
    try:
        adapter = Adapter(args.hwnd, args.pid)
        with adapter:
            if args.operation == "batch":
                raw_actions = parse_json_arg(args.actions)
                result = _run_batch(adapter, raw_actions, bool(args.foreground_demo))
            else:
                result = adapter.perform(args)
            result["focus_safety"] = {
                "class": "CUIAutomation8",
                "interface": "IUIAutomation2",
                "auto_set_focus": False,
                "readback_verified_before_discovery": adapter.focus_off_verified,
            }
            print(json.dumps(result, ensure_ascii=True))
            return 0 if result.get("ok", True) else 2
    except Exception as exc:
        failure = {"error": str(exc)}
        if isinstance(exc, ObservedActionError):
            failure["action_attempted"] = exc.action_attempted
            failure["interaction_observation"] = exc.observation
        if adapter is not None and getattr(adapter, "focus_off_verified", False):
            failure["focus_safety"] = {
                "class": "CUIAutomation8",
                "interface": "IUIAutomation2",
                "auto_set_focus": False,
                "readback_verified_before_discovery": True,
            }
        print(json.dumps(failure, ensure_ascii=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
