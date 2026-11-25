#!/usr/bin/env python3
"""First-use environment & authorization checker and guided setup wizard for Auto Computer."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any

_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from _common import (
    configure_utf8_stdio,
    emit_json,
    ensure_comtypes_overlay,
    find_chrome_exe,
    get_auto_computer_home,
    get_tools_dir,
)
from browser_headless import _find_edge_exe
import browser_session as bs


PLAYWRIGHT_EXT_STORE_URL = (
    "https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
)


def _check_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _read_devtools_port(browser: str) -> dict[str, Any]:
    local_app = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app:
        return {"present": False, "listening": False, "port": None, "path": None}
    sub = Path("Google") / "Chrome" if browser == "chrome" else Path("Microsoft") / "Edge"
    port_file = Path(local_app) / sub / "User Data" / "DevToolsActivePort"
    if not port_file.is_file():
        return {"present": False, "listening": False, "port": None, "path": str(port_file)}
    try:
        lines = port_file.read_text(encoding="utf-8", errors="replace").splitlines()
        port = int(lines[0].strip()) if lines else None
    except Exception:
        port = None
    listening = False
    if port and 1 <= port <= 65535:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.6):
                listening = True
        except OSError:
            listening = False
    return {"present": True, "listening": listening, "port": port, "path": str(port_file)}


def _tool_status() -> dict[str, Any]:
    ac_home = get_auto_computer_home()
    tools = get_tools_dir()
    ensure_comtypes_overlay()

    node_exe = shutil.which("node")
    node_ver = None
    if node_exe:
        try:
            proc = subprocess.run([node_exe, "--version"], capture_output=True, text=True, timeout=5)
            if proc.returncode == 0:
                node_ver = proc.stdout.strip()
        except Exception:
            pass

    playwright_cli = Path(
        os.environ.get("AUTO_COMPUTER_PLAYWRIGHT_CLI", "").strip()
        or str(tools / "playwright" / "node_modules" / "@playwright" / "cli" / "playwright-cli.js")
    )
    chrome_mcp = tools / "chrome-devtools-mcp" / "node_modules" / "chrome-devtools-mcp" / "out" / "Default" / "gen" / "src" / "bin" / "chrome-devtools-mcp.js"
    winapp_env = os.environ.get("AUTO_COMPUTER_WINAPP", "").strip()
    winapp_exe = Path(winapp_env) if winapp_env else (tools / "winapp" / "winapp.exe")
    if not winapp_exe.is_file():
        w = shutil.which("winapp") or shutil.which("winapp.exe")
        if w:
            winapp_exe = Path(w)
    ab_env = os.environ.get("AUTO_COMPUTER_AGENT_BROWSER", "").strip()
    ab_exe = Path(ab_env) if ab_env else (tools / "agent-browser" / "agent-browser.exe")
    if not ab_exe.is_file():
        w = shutil.which("agent-browser")
        if w:
            ab_exe = Path(w)

    return {
        "home": str(ac_home),
        "toolsDir": str(tools),
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
            "comtypes": _check_module("comtypes"),
            "pywin32": _check_module("win32gui") and _check_module("win32crypt"),
            "pillow": _check_module("PIL"),
            "numpy": _check_module("numpy"),
            "winrtOcr": _check_module("winrt.windows.media.ocr"),
        },
        "node": {
            "executable": node_exe,
            "version": node_ver,
            "playwrightCli": playwright_cli.is_file(),
            "chromeDevtoolsMcp": chrome_mcp.is_file(),
        },
        "optionalBinaries": {
            "winapp": str(winapp_exe) if winapp_exe.is_file() else None,
            "agentBrowser": str(ab_exe) if ab_exe.is_file() else None,
        },
    }


def collect_status(profile: str = "Default") -> dict[str, Any]:
    tools = _tool_status()
    chrome_exe = find_chrome_exe()
    edge_exe = _find_edge_exe()

    browsers: dict[str, Any] = {}
    for name, exe in (("chrome", chrome_exe), ("edge", edge_exe)):
        proc_name = "chrome.exe" if name == "chrome" else "msedge.exe"
        running = bs._is_running(proc_name) if os.name == "nt" else False
        version = bs._version_text(bs._browser_version(exe)) if exe else None
        ext_ver = bs._extension_version(name, profile) if exe and os.name == "nt" else None
        _, tok_src = bs._token_source(name, profile) if os.name == "nt" else (None, "unavailable")
        devtools = _read_devtools_port(name) if os.name == "nt" else {"present": False, "listening": False}
        browsers[name] = {
            "installed": bool(exe),
            "executable": exe,
            "version": version,
            "running": running,
            "remoteDebugging": devtools,
            "playwrightExtension": {
                "installed": ext_ver is not None,
                "version": ext_ver,
                "tokenSource": tok_src,
                "tokenSaved": tok_src in {"dpapi_file", "process_environment"},
            },
        }

    py_ok = all(tools["python"][k] for k in ("comtypes", "pywin32", "pillow", "numpy"))
    return {
        "ok": True,
        "coreReady": py_ok,
        "autoProfileReady": bool(edge_exe or chrome_exe) and bool(tools["node"]["executable"]),
        "tools": tools,
        "browsers": browsers,
    }


def _print_report(status: dict[str, Any]) -> None:
    py = status["tools"]["python"]
    node = status["tools"]["node"]
    opt = status["tools"]["optionalBinaries"]
    print("\n=== Auto Computer 环境与授权体检 ===")
    print(f"数据根目录: {status['tools']['home']}")
    print(
        f"[Python] {py['version']} | comtypes={py['comtypes']} pywin32={py['pywin32']} "
        f"pillow={py['pillow']} numpy={py['numpy']} winrt-ocr={py['winrtOcr']}"
    )
    print(
        f"[Node]   {node['version'] or '未检测到'} | @playwright/cli={node['playwrightCli']} "
        f"chrome-devtools-mcp={node['chromeDevtoolsMcp']}"
    )
    print(
        f"[可选]   winapp={opt['winapp'] or '未安装'} | agent-browser={opt['agentBrowser'] or '未安装'}"
    )
    print("\n--- 能力就绪状态 ---")
    print("  [就绪] 域 1：本地文件 / Excel & CSV 对账 / 图像排版" if py["pillow"] and py["numpy"] else "  [缺依赖] 域 1：缺少 pillow/numpy")
    print("  [就绪] 域 3：Windows 后台 UIA / MSAA / OCR" if py["comtypes"] and py["pywin32"] else "  [缺依赖] 域 3：缺少 comtypes/pywin32")
    print("  [就绪] 域 2 (零授权隔离模式)：edge-devtools profile='auto'（无需浏览器弹窗）" if status["autoProfileReady"] else "  [未就绪] 域 2 (隔离模式)：需要 Edge/Chrome 与 Node.js")

    for bname, label in (("chrome", "Chrome"), ("edge", "Edge")):
        b = status["browsers"][bname]
        if not b["installed"]:
            print(f"  [未安装] {label}")
            continue
        rd = b["remoteDebugging"]
        ext = b["playwrightExtension"]
        rd_text = (
            f"已开启并监听 (port {rd['port']})"
            if rd["listening"]
            else ("已检测到端口文件（浏览器当前未运行或未监听）" if rd["present"] else "未开启")
        )
        ext_text = f"v{ext['version']}" if ext["installed"] else "未安装"
        tok_text = "已保存 (DPAPI)" if ext["tokenSaved"] else "未保存"
        print(
            f"  [{label} v{b['version'] or '?'}] 运行中={b['running']} | 远程调试={rd_text} | "
            f"Playwright扩展={ext_text} | 令牌={tok_text}"
        )


def _prompt_yn(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans in {"y", "yes", "1", "true"}


def _wizard(profile: str) -> dict[str, Any]:
    status = collect_status(profile)
    _print_report(status)
    if not sys.stdin.isatty():
        return status

    print("\n=== 首次使用授权引导（安全模式：不绕过任何浏览器弹窗） ===")
    print("说明：隔离模式（edge-devtools profile='auto'）、本地文件与 UIA 后台控制已可直接使用。")
    print("仅当你需要让 AI 接管你【当前日常已登录的 Chrome / Edge】时，才需要完成下面对应的一次性授权：\n")

    for bname, inspect_url, label in (
        ("chrome", "chrome://inspect/#remote-debugging", "Chrome"),
        ("edge", "edge://inspect/#remote-debugging", "Edge"),
    ):
        b = status["browsers"][bname]
        if not b["installed"]:
            continue
        if not b["remoteDebugging"]["listening"]:
            print(f"[{label} 日常浏览器远程调试]")
            print(f"  1) 在 {label} 地址栏打开：{inspect_url}")
            print("  2) 勾选开启“允许远程调试 / Remote debugging”开关")
            print("  3) 首次通过 MCP 连接时，在弹出的提示框点一次「Allow」即可。")
            if _prompt_yn(f"是否已完成 {label} 的远程调试开启并需要立即复检？", default=False):
                pass
        ext = b["playwrightExtension"]
        if not ext["installed"]:
            print(f"\n[{label} Playwright Extension（用于后台重连现有标签页，可选）]")
            if _prompt_yn(f"未检测到 {label} 的 Playwright Extension，是否在浏览器打开官方扩展安装页？", default=False):
                webbrowser.open(PLAYWRIGHT_EXT_STORE_URL)
                input("安装完成后按回车继续检查...")
        updated = collect_status(profile)["browsers"][bname]
        if updated["playwrightExtension"]["installed"] and not updated["playwrightExtension"]["tokenSaved"]:
            print(f"\n[{label} 扩展令牌 DPAPI 加密保存]")
            print(f"  点击 {label} 工具栏的 Playwright Extension 图标 -> Manage clients -> 复制 Token。")
            if _prompt_yn(f"现在粘贴保存 {label} ({profile}) 的扩展令牌（本地 DPAPI 加密，不明文落盘）？", default=True):
                try:
                    res = bs._save_token(bname, profile)
                    print(f"  已加密保存：{res}")
                except Exception as exc:
                    print(f"  保存跳过/失败：{exc}")

    final_status = collect_status(profile)
    _print_report(final_status)
    return final_status


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only check and print status without interactive prompts")
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON status")
    parser.add_argument("--profile", default="Default", help="Chromium profile name (default: Default)")
    args = parser.parse_args()

    profile = bs._profile_name(args.profile)
    if args.json:
        emit_json(collect_status(profile), 0)
    if args.check:
        status = collect_status(profile)
        _print_report(status)
        raise SystemExit(0 if status["coreReady"] else 1)
    final_status = _wizard(profile)
    raise SystemExit(0 if final_status["coreReady"] else 1)


if __name__ == "__main__":
    main()
