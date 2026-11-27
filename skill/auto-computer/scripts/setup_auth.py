#!/usr/bin/env python3
"""First-use environment & authorization checker and guided setup wizard for Auto Computer."""
from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
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
    find_edge_exe,
    get_auto_computer_home,
    get_tools_dir,
)
import browser_session as bs


PLAYWRIGHT_EXT_STORE_URL = (
    "https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm"
)


def _check_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ModuleNotFoundError, ValueError):
        return False


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
    winapp_env = os.environ.get("AUTO_COMPUTER_WINAPP", "").strip()
    winapp_exe = Path(winapp_env) if winapp_env else (tools / "winapp" / "winapp.exe")
    if not winapp_exe.is_file():
        w = shutil.which("winapp") or shutil.which("winapp.exe")
        if w:
            winapp_exe = Path(w)

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
        },
        "optionalBinaries": {
            "winapp": str(winapp_exe) if winapp_exe.is_file() else None,
        },
    }


def collect_status(profile: str = "Default") -> dict[str, Any]:
    tools = _tool_status()
    chrome_exe = find_chrome_exe()
    edge_exe = find_edge_exe()

    browsers: dict[str, Any] = {}
    for name, exe in (("chrome", chrome_exe), ("edge", edge_exe)):
        proc_name = "chrome.exe" if name == "chrome" else "msedge.exe"
        running = bs._is_running(proc_name) if os.name == "nt" else False
        version = bs._version_text(bs._browser_version(exe)) if exe else None
        ext_ver = bs._extension_version(name, profile) if exe and os.name == "nt" else None
        _, tok_src = bs._token_source(name, profile) if os.name == "nt" else (None, "unavailable")
        browsers[name] = {
            "installed": bool(exe),
            "executable": exe,
            "version": version,
            "running": running,
            "namedPipeReady": bool(exe and tools["node"]["executable"]),
            "playwrightExtension": {
                "installed": ext_ver is not None,
                "version": ext_ver,
                "tokenSource": tok_src,
                "tokenSaved": tok_src in {"dpapi_file", "process_environment"},
            },
        }

    py_ok = all(tools["python"][k] for k in ("comtypes", "pywin32", "pillow", "numpy"))
    ac_home = get_auto_computer_home()
    return {
        "ok": True,
        "coreReady": py_ok,
        "autoProfileReady": bool(edge_exe or chrome_exe) and bool(tools["node"]["executable"]),
        "persistence": {
            "adaptersDir": str(ac_home / "adapters"),
            "secretsDir": str(ac_home / "secrets"),
            "profilesDir": str(ac_home / "profiles"),
            "localRules": str(ac_home / "local-rules.md"),
        },
        "tools": tools,
        "browsers": browsers,
    }


def _print_report(status: dict[str, Any]) -> None:
    py = status["tools"]["python"]
    node = status["tools"]["node"]
    opt = status["tools"]["optionalBinaries"]
    pers = status.get("persistence", {})
    local_rules = Path(pers.get("localRules", ""))
    print("\n=== Auto Computer 环境与授权体检 ===")
    print(
        f"持久化目录(升级保留): adapters={pers.get('adaptersDir', '')} | "
        f"secrets={pers.get('secretsDir', '')} | local-rules={'已配置' if local_rules.is_file() else '未配置'}"
    )
    print(
        f"[Python] {py['version']} | comtypes={py['comtypes']} pywin32={py['pywin32']} "
        f"pillow={py['pillow']} numpy={py['numpy']} winrt-ocr={py['winrtOcr']}"
    )
    print(
        f"[Node]   {node['version'] or '未检测到'} | @playwright/cli={node['playwrightCli']} | "
        f"winapp={opt['winapp'] or '未安装'}"
    )
    print("  [就绪] 域 1：本地文件 / Excel & CSV 对账 / 图像排版" if py["pillow"] and py["numpy"] else "  [缺依赖] 域 1：缺少 pillow/numpy")
    print("  [就绪] 域 2：命名管道 (auto/kept) 零端口零横幅" if status["autoProfileReady"] else "  [未就绪] 域 2：需要 Edge/Chrome 与 Node.js")
    print("  [就绪] 域 3：Windows 后台 UIA / MSAA / OCR + 借前台安全护栏" if py["comtypes"] and py["pywin32"] else "  [缺依赖] 域 3：缺少 comtypes/pywin32")

    for bname, label in (("chrome", "Chrome"), ("edge", "Edge")):
        b = status["browsers"][bname]
        if not b["installed"]:
            print(f"  [未安装] {label}")
            continue
        ext = b["playwrightExtension"]
        ext_text = f"v{ext['version']}" if ext["installed"] else "未安装"
        tok_text = {"dpapi_file": "本机已保存 (DPAPI)", "process_environment": "当前进程已配置"}.get(ext["tokenSource"], "未配置")
        print(
            f"  [{label} v{b['version'] or '?'}] 运行中={b['running']} | 命名管道(auto/kept)={b['namedPipeReady']} | "
            f"Playwright扩展={ext_text} | 令牌={tok_text}"
        )


def _prompt_yn(question: str, default: bool = True) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    ans = input(f"{question} {hint}: ").strip().lower()
    if not ans:
        return default
    return ans in {"y", "yes", "1", "true"}


def _wizard(profile: str, browser: str | None = None) -> dict[str, Any]:
    status = collect_status(profile)
    _print_report(status)
    if not sys.stdin.isatty():
        if browser:
            raise SystemExit("请在本机交互终端完成扩展配对。令牌只在隐藏输入处粘贴。")
        return status

    print("\n=== 首次使用授权引导（安全模式：不绕过任何浏览器弹窗） ===")
    print("说明：隔离模式（edge-devtools profile='auto'）、本地文件与 UIA 后台控制已可直接使用。")
    print("仅当你需要让 AI 接管你【当前日常已登录的 Chrome / Edge】时，才需要完成下面对应的一次性授权：\n")

    for bname, label in (("chrome", "Chrome"), ("edge", "Edge")):
        if browser and browser != bname:
            continue
        b = status["browsers"][bname]
        if not b["installed"]:
            if browser:
                raise SystemExit(f"未找到 {label}。")
            continue
        ext = b["playwrightExtension"]
        if ext["tokenSaved"]:
            print(f"[{label}] {profile} 已配置扩展令牌。可用 browser_session.py status 单独检查后台连接条件。")
            continue
        if not browser and not _prompt_yn(f"现在给 {label} 的 {profile} 配置后台重连？", default=False):
            continue
        if not ext["installed"]:
            print(f"请在 {label} 的 {profile} 安装官方 Playwright Extension：{PLAYWRIGHT_EXT_STORE_URL}")
            local_ext = Path(status["tools"]["toolsDir"]) / "edge-playwright-extension" / "0.6.0"
            if local_ext.is_dir():
                print(f"或开启开发者模式加载本地解压扩展目录：{local_ext}")
            if _prompt_yn(f"要用默认浏览器打开 Playwright Extension 安装页吗？", default=True):
                try:
                    webbrowser.open(PLAYWRIGHT_EXT_STORE_URL)
                except Exception:
                    pass
            if not _prompt_yn("已在对应浏览器配置中安装并打开 Playwright Extension 吗？", default=False):
                if browser:
                    raise SystemExit("配对待完成。")
                continue
        print(f"在 {label} 的 {profile} 点击 Playwright Extension → Manage clients，复制该配置的 Token。")
        bs._save_token(bname, profile)
        print(f"[{label}] 令牌已由 Windows 当前用户 DPAPI 加密保存（升级覆盖不会丢失）。")

    final_status = collect_status(profile)
    _print_report(final_status)
    return final_status


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only check and print status without interactive prompts")
    parser.add_argument("--json", action="store_true", help="output machine-readable JSON status")
    parser.add_argument("--profile", default="Default", help="Chromium profile name (default: Default)")
    parser.add_argument("--pair", choices=("chrome", "edge"), help="pair this browser profile in a local interactive terminal")
    args = parser.parse_args()

    profile = bs._profile_name(args.profile)
    if args.pair and (args.check or args.json):
        parser.error("--pair requires the interactive wizard")
    if args.json:
        emit_json(collect_status(profile), 0)
    if args.check:
        status = collect_status(profile)
        _print_report(status)
        raise SystemExit(0 if status["coreReady"] else 1)
    final_status = _wizard(profile, args.pair)
    raise SystemExit(0 if final_status["coreReady"] else 1)


if __name__ == "__main__":
    main()
