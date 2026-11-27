#!/usr/bin/env python3
"""Run one bounded task against an exact active tab in an authorized daily browser."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from _common import configure_utf8_stdio, emit_json
import browser_session as sessions

MAX_ACTIONS = 20
MAX_SELECTOR = 512
MAX_VALUE = 2048
ACTION_OPS = {"click", "fill", "select", "check", "uncheck", "read", "wait_for", "read_list"}
MIN_DAILY_CLI = (0, 1, 25)


def _int_opt(val: Any, default: int, low: int, high: int, label: str) -> int:
    if val is None:
        return default
    if isinstance(val, bool) or not isinstance(val, int) or not (low <= val <= high):
        raise ValueError(f"{label} must be an integer between {low} and {high}")
    return val


def _valid_url(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ch in value for ch in "\0\r\n"):
        raise ValueError("url must be a non-empty http/https URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be a non-empty http/https URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credential-bearing URLs are not allowed")
    return value


def _validate(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    browser = args.get("browser", "chrome")
    profile = args.get("profile", "Default")
    if browser not in {"chrome", "edge"}:
        raise ValueError("browser must be chrome or edge")
    profile = sessions._profile_name(profile)
    url = _valid_url(args.get("url"))
    actions = args.get("actions", [])
    if not isinstance(actions, list) or len(actions) > MAX_ACTIONS:
        raise ValueError(f"actions must be an array with at most {MAX_ACTIONS} items")
    clean_actions = []
    for index, action in enumerate(actions):
        op = action.get("op") or action.get("action") or action.get("type") if isinstance(action, dict) else None
        if not isinstance(action, dict) or op not in ACTION_OPS:
            raise ValueError(f"actions[{index}].op must be one of {', '.join(sorted(ACTION_OPS))}")
        raw_sel = action.get("selector") or (action.get("item_selector") if op == "read_list" else None)
        if not isinstance(raw_sel, str) or not raw_sel.strip() or len(raw_sel) > MAX_SELECTOR or any(ch in raw_sel for ch in "\0\r\n"):
            raise ValueError(f"actions[{index}].selector must be a non-empty explicit selector")
        item: dict[str, Any] = {"op": op, "selector": raw_sel}
        if op in {"fill", "select"}:
            value = action.get("value")
            if not isinstance(value, str) or len(value) > MAX_VALUE or "\0" in value:
                raise ValueError(f"actions[{index}].value must be a string of at most {MAX_VALUE} characters")
            item["value"] = value
        elif op == "wait_for":
            item["timeout_ms"] = _int_opt(action.get("timeout_ms"), 3000, 100, 10000, f"actions[{index}].timeout_ms")
            item["min_count"] = _int_opt(action.get("min_count"), 1, 1, 1000, f"actions[{index}].min_count")
        elif op == "read":
            read_as = action.get("as")
            if read_as is None or read_as == "":
                read_as = "text"
            if not isinstance(read_as, str) or read_as not in {"text", "count", "attribute"}:
                raise ValueError(f"actions[{index}].as must be text, count, or attribute")
            item["as"] = read_as
            if read_as == "attribute":
                attr = action.get("attribute")
                if not isinstance(attr, str) or not attr.strip() or len(attr) > 128 or any(ch in attr for ch in "\0\r\n"):
                    raise ValueError(f"actions[{index}].attribute must be a non-empty string of at most 128 characters")
                item["attribute"] = attr
        elif op == "read_list":
            item["limit"] = _int_opt(action.get("limit"), 10, 1, 30, f"actions[{index}].limit")
            item["text_limit"] = _int_opt(action.get("text_limit"), 240, 50, 600, f"actions[{index}].text_limit")
        clean_actions.append(item)
    summary = args.get("read_summary", not bool(clean_actions))
    if not isinstance(summary, bool):
        raise ValueError("read_summary must be a boolean")
    return {"browser": browser, "profile": profile, "url": url, "actions": clean_actions, "read_summary": summary}


def _command(session: str, *command: str, timeout: int = 15) -> str:
    if not sessions.CLI.is_file():
        raise FileNotFoundError(f"Playwright CLI not found: {sessions.CLI}")
    node = os.environ.get("AUTO_COMPUTER_NODE") or "node"
    proc = subprocess.run(
        [node, str(sessions.CLI), f"-s={session}", *command],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=sessions._cli_env(),
    )
    if proc.returncode:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(detail[:500] or f"Playwright CLI {command[0]} failed with exit {proc.returncode}")
    return proc.stdout


def _cli_version() -> tuple[int, int, int] | None:
    if not sessions.CLI.is_file():
        raise FileNotFoundError(f"Playwright CLI not found: {sessions.CLI}")
    package_file = sessions.CLI.parent / "package.json"
    try:
        version = json.loads(package_file.read_text(encoding="utf-8")).get("version", "")
    except (OSError, json.JSONDecodeError):
        return None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?", version)
    return tuple(int(match.group(i)) for i in range(1, 4)) if match else None


def _task_code(
    url: str,
    actions: list[dict[str, Any]],
    read_summary: bool,
    *,
    close_blank_on_miss: bool = False,
) -> str:
    payload = json.dumps(
        {
            "url": url,
            "actions": actions,
            "read_summary": read_summary,
            "close_blank_on_miss": close_blank_on_miss,
        },
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return (
        "async page => { const task = " + payload + "; const steps = []; "
        "const expected = new URL(task.url); if (!['http:', 'https:'].includes(expected.protocol) || expected.username || expected.password) "
        "return { ok: false, available: false, reason: 'invalid_target_url', steps }; "
        "const normUrl = u => { const x = new URL(u); return x.origin + (x.pathname.replace(/\\/+$/, '') || '/') + x.search; }; "
        "const safeOriginPath = u => { try { const x = new URL(u); return (x.protocol === 'http:' || x.protocol === 'https:') ? (x.origin + x.pathname) : (x.protocol + '//'); } catch { return ''; } }; "
        "const exactUrl = expected.href; const normExpected = normUrl(exactUrl); const pages = page.context().pages(); "
        "let exactMatched = true; let matches = pages.filter(p => { try { return new URL(p.url()).href === exactUrl; } catch { return false; } }); "
        "if (matches.length === 0) { const nm = pages.filter(p => { try { return normUrl(p.url()) === normExpected; } catch { return false; } }); "
        "if (nm.length === 1) { exactMatched = false; matches = nm; } } "
        "if (matches.length !== 1) { const openUrls = Array.from(new Set(pages.map(p => safeOriginPath(p.url())).filter(Boolean))).slice(0, 8); "
        "const allBlank = openUrls.length > 0 && openUrls.every(u => u.startsWith('about:') || u.endsWith('newtab')); "
        "let blankClosed = 0; "
        "if (allBlank && task.close_blank_on_miss) { for (const p of pages) { try { if (p.url() === 'about:blank' && !p.isClosed()) { await p.close(); blankClosed++; } } catch {} } } "
        "return { ok: false, available: false, reason: matches.length ? 'ambiguous_target_url' : 'target_tab_unavailable', open_urls: openUrls, ...(allBlank ? { hint: 'only_blank_tab_authorize_target_in_extension' } : {}), ...(blankClosed ? { blank_tabs_closed: blankClosed } : {}), steps }; } "
        "const targetPage = matches[0]; const matchedHref = new URL(targetPage.url()).href; "
        "const samePage = () => !targetPage.isClosed() && (() => { try { return exactMatched ? new URL(targetPage.url()).href === exactUrl : normUrl(targetPage.url()) === normExpected; } catch { return false; } })(); "
        "const fail = (reason, needsReadback = false) => ({ ok: false, reason, steps, ...(needsReadback ? { needs_readback: true } : {}) }); "
        "if (!samePage()) return fail('target_tab_url_mismatch'); "
        "for (let i = 0; i < task.actions.length; i++) { const action = task.actions[i]; "
        "if (!samePage()) return fail('page_url_changed'); const target = targetPage.locator(action.selector); const options = { timeout: 3000 }; "
        "try { if (action.op === 'click') await target.click(options); else if (action.op === 'fill') await target.fill(action.value, options); "
        "else if (action.op === 'select') await target.selectOption(action.value, options); else if (action.op === 'check') await target.check(options); "
        "else if (action.op === 'uncheck') await target.uncheck(options); "
        "else if (action.op === 'wait_for') { const dl = Date.now() + (action.timeout_ms || 3000); const minC = action.min_count || 1; let cnt = 0; "
        "while (true) { if (!samePage()) return fail('page_url_changed'); cnt = await target.count(); if (cnt >= minC) break; "
        "if (Date.now() >= dl) throw new Error('wait_for timed out (found ' + cnt + ')'); await targetPage.waitForTimeout(30); } "
        "steps.push({ op: action.op, count: cnt }); continue; } "
        "else if (action.op === 'read_list') { const items = await target.evaluateAll((els, cfg) => els.slice(0, cfg.limit).map(el => { "
        "if (el instanceof HTMLInputElement && el.type.toLowerCase() === 'password') return { text: '[password value hidden]' }; "
        "const link = el.matches && el.matches('a[href]') ? el : el.querySelector('a[href]'); "
        "const row = { text: (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, cfg.textLimit) }; "
        "if (link && link.href) row.href = String(link.href).slice(0, 1024); "
        "const ttl = link ? (link.innerText || link.getAttribute('title') || '').replace(/\\s+/g, ' ').trim().slice(0, 160) : ''; "
        "if (ttl && !row.text.includes(ttl)) row.title = ttl; return row; }), { limit: action.limit || 10, textLimit: action.text_limit || 240 }); "
        "if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, count: items.length, items }); continue; } "
        "else if (action.op === 'read') { const readAs = action.as || 'text'; "
        "if (readAs === 'count') { const cnt = await target.count(); if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, count: cnt }); continue; } "
        "if (readAs === 'attribute') { const value = await target.evaluate((el, attr) => { "
        "const type = String(el.getAttribute('type') || '').toLowerCase(); const autocomplete = String(el.getAttribute('autocomplete') || '').toLowerCase(); "
        "if (el.hidden || type === 'hidden' || type === 'password' || autocomplete.includes('password')) return '[redacted]'; "
        "const v = el.getAttribute(attr); return v === null ? null : String(v).slice(0, 2000); }, action.attribute, options); "
        "if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, attribute: action.attribute, value }); continue; } "
        "const text = await target.evaluate(el => { "
        "const type = String(el.getAttribute('type') || '').toLowerCase(); const autocomplete = String(el.getAttribute('autocomplete') || '').toLowerCase(); "
        "if (el.hidden || type === 'hidden' || type === 'password' || autocomplete.includes('password')) return '[redacted]'; "
        "if ((typeof HTMLInputElement !== 'undefined' && el instanceof HTMLInputElement) || (typeof HTMLTextAreaElement !== 'undefined' && el instanceof HTMLTextAreaElement)) return el.value.slice(0, 2000); "
        "return (el.innerText || el.textContent || '').trim().slice(0, 2000); }, undefined, options); "
        "if (!samePage()) return fail('page_url_changed'); steps.push({ op: action.op, text: String(text).slice(0, 2000) }); continue; } "
        "steps.push({ op: action.op, ok: true }); "
        "} catch (error) { steps.push({ op: action.op, ok: false, error: String(error.message || error).slice(0, 300) }); return fail('action_failed', action.op !== 'read' && action.op !== 'read_list' && action.op !== 'wait_for'); } "
        "if (!samePage() && i + 1 < task.actions.length) return fail('page_url_changed'); } "
        "const result = { ok: true, available: true, verified_url: matchedHref, page_changed: !samePage(), "
        "target_tab_verified: true, active_tab_verified: targetPage === page, steps }; "
        "if (task.read_summary && samePage()) { result.title = await targetPage.title(); "
        "result.summary = (await targetPage.locator('body').innerText({ timeout: 3000 }).catch(() => '')).slice(0, 1200); } return result; }"
    )


def _run_code_result(output: str) -> dict[str, Any]:
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Playwright CLI returned invalid run-code JSON") from exc
    if not isinstance(value, dict) or not isinstance(value.get("ok"), bool):
        raise RuntimeError("Playwright CLI returned an unsupported run-code result")
    return value


def run_task(raw_args: Any) -> dict[str, Any]:
    args = _validate(raw_args)
    browser, profile, url = args["browser"], args["profile"], args["url"]
    managed_session = sessions._default_session(browser, profile)
    started = time.perf_counter()
    cli_version = _cli_version()
    if cli_version is None or cli_version < MIN_DAILY_CLI:
        return {
            "ok": False, "available": False, "browser": browser, "profile": profile, "url": url,
            "blockers": ["unsupported_or_unverified_playwright_cli_version"],
            "playwright_cli_version": ".".join(map(str, cli_version)) if cli_version else None,
            "minimum_playwright_cli_version": ".".join(map(str, MIN_DAILY_CLI)),
        }
    status = sessions._status(browser, profile, managed_session)
    base = {"browser": browser, "profile": profile, "url": url}
    reusable = bool(status.get("session_reusable"))
    custom = next((item for item in status.get("open_browser_sessions", [])
                   if isinstance(item, dict) and item.get("profile") and str(item["profile"]).casefold() == profile.casefold()
                   and item.get("attached") and item.get("compatible")
                   and sessions.SESSION_RE.fullmatch(str(item.get("name", "")))), None)
    if not reusable and not custom and not status.get("supports_native_background_flag"):
        return {
            **base,
            "ok": False,
            "available": False,
            "blockers": ["unsupported_browser_version_for_background_attach"],
            "profile_verified": False,
            "active_tab_verified": False,
        }
    if not (reusable or custom or status.get("can_reconnect_in_background")):
        return {
            **base,
            "ok": False,
            "available": False,
            "blockers": status.get("blockers", []),
            "profile_verified": False,
            "active_tab_verified": False,
        }

    managed_new_attach = not reusable and not custom
    code = _task_code(
        url,
        args["actions"],
        args["read_summary"],
        close_blank_on_miss=managed_new_attach,
    )
    if len(subprocess.list2cmdline([str(sessions.CLI), code])) > 30_000:
        raise ValueError("Browser action batch exceeds the Windows command line limit. Use a smaller batch.")

    attached = False
    result: dict[str, Any] | None = None
    try:
        if reusable:
            session = managed_session
            attach = {"ok": True, "session_reusable": True}
            attached = True
        elif custom:
            session = str(custom["name"])
            attach = {"ok": True, "session_reusable": True, "session_verified": True}
        else:
            session = managed_session
            attach = sessions._reconnect(browser, profile, session, strict_foreground=True, status=status)
            attached = bool(attach.get("session_verified"))
        if not attach.get("ok"):
            result = {
                **base,
                "ok": False,
                "available": False,
                "blockers": attach.get("blockers", [attach.get("error", "daily browser attach was not verified")]),
                "profile_verified": bool(attach.get("session_verified")),
                "active_tab_verified": False,
            }
            return result

        try:
            output = _command(session, "--raw", "run-code", code, timeout=40)
            run_result = _run_code_result(output)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            result = {"ok": False, "reason": "task_result_unknown", "needs_readback": True}
            return result
        result = {
            **run_result,
            "profile_verified": bool(attach.get("session_verified") or attach.get("session_reusable")),
            "target_tab_verified": bool(run_result.get("target_tab_verified")),
            "active_tab_verified": bool(run_result.get("active_tab_verified")),
            "session_reused": bool(attach.get("session_reusable")),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        return result
    finally:
        if attached:
            try:
                already_closed_blanks = bool(isinstance(result, dict) and result.get("blank_tabs_closed"))
                detach_result = (
                    sessions._detach(browser, profile, session, close_blank_tabs=False)
                    if already_closed_blanks
                    else sessions._detach(browser, profile, session)
                )
                if result is not None:
                    result["detached"] = bool(detach_result.get("ok"))
                    if detach_result.get("closed_blank_tabs") and not result.get("blank_tabs_closed"):
                        result["blank_tabs_closed"] = detach_result["closed_blank_tabs"]
                    if not detach_result.get("ok"):
                        result["detach_error"] = detach_result.get("error", "detach was not verified")
            except Exception as exc:
                if result is not None:
                    result["detached"] = False
                    result["detach_error"] = str(exc)[:300]


def main() -> None:
    configure_utf8_stdio()
    try:
        args = json.load(sys.stdin)
        emit_json(run_task(args), 0)
    except Exception as exc:
        emit_json({"ok": False, "available": False, "error": str(exc)[:500]}, 1)


if __name__ == "__main__":
    main()
