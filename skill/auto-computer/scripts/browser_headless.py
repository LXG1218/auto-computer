#!/usr/bin/env python3
"""Headless browser fast-lane (agent-browser)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from _common import configure_utf8_stdio, emit_json, find_chrome_exe, get_tools_dir, parse_json_arg

DEFAULT_AGENT_BROWSER = str(get_tools_dir() / "agent-browser" / "agent-browser.exe")


def _find_edge_exe() -> str | None:
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


def _resolve_browser_exe(browser: str) -> str:
    b = browser.lower()
    exe = _find_edge_exe() if b == "edge" else find_chrome_exe()
    if not exe:
        raise FileNotFoundError(f"Browser executable not found for {browser}")
    return exe


def _find_agent_browser() -> str:
    env_path = os.environ.get("AUTO_COMPUTER_AGENT_BROWSER", "").strip()
    candidates = [env_path, DEFAULT_AGENT_BROWSER]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    import shutil

    which = shutil.which("agent-browser")
    if which:
        return which
    raise FileNotFoundError(
        "agent-browser executable not found. Set AUTO_COMPUTER_AGENT_BROWSER or stage at "
        f"{DEFAULT_AGENT_BROWSER}"
    )


def _run_agent_browser_batch(
    commands: list[list[str]],
    *,
    browser: str,
    session: str,
    keep_alive: bool,
    timeout_s: float,
) -> dict[str, Any]:
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty JSON array of command arrays")
    ab_exe = _find_agent_browser()
    browser_exe = _resolve_browser_exe(browser)

    payload_cmds = [list(map(str, c)) for c in commands]
    if not keep_alive and payload_cmds[-1] != ["close"]:
        payload_cmds.append(["close"])

    proc = subprocess.run(
        [ab_exe, "--session", session, "--executable-path", browser_exe, "batch", "--bail", "--json"],
        input=json.dumps(payload_cmds, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )

    steps: list[dict[str, Any]] = []
    parse_error = None
    if proc.stdout.strip():
        try:
            parsed = json.loads(proc.stdout)
            if isinstance(parsed, list):
                steps = parsed
            else:
                steps = [parsed]
        except json.JSONDecodeError as exc:
            parse_error = f"Failed to parse agent-browser JSON output: {exc}"

    ok = proc.returncode == 0 and parse_error is None and bool(steps) and all(
        isinstance(s, dict) and s.get("success") is True for s in steps
    )

    if not ok and not keep_alive:
        # Best-effort cleanup of headless session on mid-batch failure.
        try:
            subprocess.run(
                [ab_exe, "--session", session, "close"],
                capture_output=True,
                text=True,
                timeout=8,
            )
        except Exception:
            pass

    out: dict[str, Any] = {
        "ok": ok,
        "backend": "agent-browser",
        "browser": browser,
        "executable": browser_exe,
        "session": session,
        "keep_alive": keep_alive,
        "step_count": len(steps),
        "steps": steps,
    }
    if not ok:
        failed = next((s for s in steps if isinstance(s, dict) and s.get("success") is False), None)
        out["error"] = (
            parse_error
            or (failed.get("error") if failed else None)
            or proc.stderr.strip()
            or f"agent-browser exited with code {proc.returncode}"
        )
    return out


def _headless_fetch(args: argparse.Namespace) -> dict[str, Any]:
    urls: list[str] = list(args.urls)
    selector = args.selector or ""
    max_text = int(args.max_text)
    js_extract = (
        f"(() => {{ const sel = {json.dumps(selector)}; "
        f"const text = (document.body ? document.body.innerText : '').trim().slice(0, {max_text}); "
        "const items = sel ? Array.from(document.querySelectorAll(sel)).slice(0, 100).map(el => ({"
        "text: (el.innerText || el.textContent || '').trim().slice(0, 500), "
        "href: el.href || el.getAttribute('href') || null"
        "})) : []; "
        "const links = Array.from(document.querySelectorAll('a[href]')).slice(0, 50).map(a => ({"
        "text: (a.innerText || a.textContent || '').trim().slice(0, 200), href: a.href"
        "})); "
        "return { url: location.href, title: document.title, text, items, links }; })()"
    )

    cmds: list[list[str]] = []
    eval_indices: list[int] = []
    for url in urls:
        cmds.append(["open", url])
        if args.wait_selector:
            cmds.append(["wait", args.wait_selector])
        if args.wait_ms and args.wait_ms > 0:
            cmds.append(["wait", str(int(args.wait_ms))])
        eval_indices.append(len(cmds))
        cmds.append(["eval", js_extract])

    res = _run_agent_browser_batch(
        cmds,
        browser=args.browser,
        session=args.session,
        keep_alive=bool(args.keep_alive),
        timeout_s=float(args.timeout),
    )

    pages: list[dict[str, Any]] = []
    steps = res.get("steps", [])
    for idx in eval_indices:
        if idx < len(steps):
            val = steps[idx].get("result", {})
            if isinstance(val, dict) and "result" in val and isinstance(val["result"], dict):
                pages.append(val["result"])
            elif isinstance(val, dict):
                pages.append(val)

    return {
        "ok": res["ok"],
        "backend": "agent-browser",
        "browser": args.browser,
        "session": args.session,
        "keep_alive": bool(args.keep_alive),
        "page_count": len(pages),
        "pages": pages,
        **({"error": res["error"]} if not res["ok"] and "error" in res else {}),
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_batch = sub.add_parser("headless-batch", help="run commands in isolated headless browser via agent-browser batch")
    p_batch.add_argument("--commands", required=True, help="JSON array of command arrays or path to JSON file")
    p_batch.add_argument("--browser", choices=("edge", "chrome"), default="edge")
    p_batch.add_argument("--session", default=f"ac-hl-{os.getpid()}")
    p_batch.add_argument("--keep-alive", action="store_true")
    p_batch.add_argument("--timeout", type=float, default=60.0)

    p_fetch = sub.add_parser("headless-fetch", help="fetch one or more public URLs in one headless browser session")
    p_fetch.add_argument("--url", dest="urls", action="append", required=True)
    p_fetch.add_argument("--selector", help="optional CSS selector to extract matching items")
    p_fetch.add_argument("--wait-selector", help="optional CSS selector to wait for before extraction")
    p_fetch.add_argument("--wait-ms", type=int, default=0)
    p_fetch.add_argument("--max-text", type=int, default=4000)
    p_fetch.add_argument("--browser", choices=("edge", "chrome"), default="edge")
    p_fetch.add_argument("--session", default=f"ac-fetch-{os.getpid()}")
    p_fetch.add_argument("--keep-alive", action="store_true")
    p_fetch.add_argument("--timeout", type=float, default=60.0)

    args = parser.parse_args()
    t0 = time.perf_counter()
    try:
        if args.cmd == "headless-batch":
            res = _run_agent_browser_batch(
                parse_json_arg(args.commands),
                browser=args.browser,
                session=args.session,
                keep_alive=bool(args.keep_alive),
                timeout_s=float(args.timeout),
            )
        elif args.cmd == "headless-fetch":
            res = _headless_fetch(args)
        else:
            raise ValueError(f"Unsupported command: {args.cmd}")
        res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        emit_json(res, 0 if res.get("ok") else 1)
    except Exception as exc:
        emit_json({"ok": False, "error": str(exc),
                   "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2)}, 1)


if __name__ == "__main__":
    main()
