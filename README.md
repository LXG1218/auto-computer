# Auto Computer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64%20%2F%20ARM64)-0078D4.svg)](#)

**Auto Computer** 是面向 AI Coding Agent（Antigravity / Codex / 通用 MCP 客户端）的**后台优先 Windows 自动化 Skill 与轻量 MCP 工具包**。

相比传统的“截图 → 猜坐标 → 抢鼠标键盘”循环，它优先通过本地脚本、Chromium 命名管道（`--remote-debugging-pipe`）、Playwright 扩展令牌与 Windows 原生 UI Automation（`AutoSetFocus=false`）在后台完成执行并读回核验。

---

## 核心能力（v1.2.0）

1. **域 1｜本地文件 / 表格 / 图像排版**：`run_task.py`（`batch` 单进程闭环）、`files_fast.py`、`table_ops.py`（`.xlsx`/`.xlsm`/`.csv`/`.tsv` 对账）、`edit_saved.py`（自动备份与回滚）、`image_layout.py`。
2. **域 2｜零端口零横幅网页自动化（Edge & Chrome 全适配）**：
   - `mcp/edge_mcp.cjs`（`edge-devtools` MCP）：基于 Chromium `--remote-debugging-pipe` 命名管道，支持 `profile="auto"`（匿名隔离）与 `profile="kept"`（专用保留登录空间），**0 TCP 端口、0 Allow 弹窗、0 自动化横幅**。
   - `scripts/browser_session.py`：同时适配 Chrome 与 Edge（支持商店与本地离线解压扩展检测），通过 DPAPI 加密令牌后台复用日常已打开标签页，**任务完成后立即 `detach` 自动消掉扩展调试横幅**。
3. **域 3｜Windows 桌面控制与跨 Agent 借前台护栏**：
   - `mcp/desktop_mcp.py`（`windows-uia` MCP）：`inspect_window` 支持 `max_bytes` 字节预算与 `delta_base` 增量控件对比，大幅降低 Token 消耗；
   - `foreground_desktop` 在 `click`/`drag` 前自动检测 `POINT_COVERED` 窗口遮挡，并联动 `mcp/foreground_guard.py` 在短暂借前台期间显示**点击穿透屏幕边缘光晕 + 顶部窄提示条 + `Esc` 急停**，动作完立即自动收起；
   - 配套 `uia_background.py`、`msaa_background.py`、`win_background.py`、`window_ocr.py`（WinRT 内存 OCR）。
4. **域 4｜跨软件数据流转与本机私有规则解耦**：
   - 强制先读回核验再跨软件写入；
   - 本机私有策略可写在 `%AUTO_COMPUTER_HOME%\local-rules.md`（不进 Git 仓库、升级永不覆盖）。

---

## 一键安装与升级（Antigravity / Codex / 通用 MCP）

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1
```

| 参数 | 说明 |
| --- | --- |
| `.\install.ps1`（默认 `-Target all`） | 同时同步 Antigravity（`~/.gemini/config`）、Codex（`~/.codex` + `codex mcp add`）与通用 `%LOCALAPPDATA%\AutoComputer\mcp-config.json` |
| `-Target codex` / `antigravity` / `generic` | 按指定客户端安装 |
| `-PythonExe <path>` / `-ToolsDir <dir>` / `-SkipNode` | 复用本机 `.venv` 与工具目录 |

> 覆盖升级时完整保留 `%AUTO_COMPUTER_HOME%\adapters`（软件控件适配库）、`secrets`（DPAPI 令牌）、`profiles`（`kept` 登录空间）与 `local-rules.md`（本机私有规则）。

---

## 首次使用：环境体检与扩展配对

```powershell
.\setup-auth.ps1 --check          # 环境、MCP 与浏览器状态只读体检
.\pair-browser.cmd                 # 双击或命令行引导 Chrome / Edge Playwright 扩展令牌配对
```

- 公开/匿名网页、专用 `kept` 登录空间、本地文件表格、Windows 后台 UIA/OCR 均**免配置立即可用**；
- 需要后台复用日常已打开 Chrome/Edge 标签页时，用 `.\pair-browser.cmd` 一次性把 Playwright Extension 令牌加密存至本机 `secrets/`。

---

## 目录结构

```text
auto-computer/
├── install.ps1                 # 一键安装/升级脚本（保护 adapters / secrets / profiles / local-rules.md）
├── setup-auth.ps1              # 环境与授权体检入口
├── pair-browser.cmd            # 浏览器扩展一键配对入口
├── mcp/
│   ├── desktop_mcp.py          # windows-uia MCP（含增量树、字节预算与遮挡校验）
│   ├── foreground_guard.py     # 跨 Agent 借前台点击穿透边缘光晕 + 顶部提示条 + Esc 急停
│   └── edge_mcp.cjs            # edge-devtools MCP（命名管道 auto/kept 模式）
├── tests/                      # 单元与回归测试
└── skill/auto-computer/
    ├── SKILL.md                # 精简主路由规范（自动读取本机 local-rules.md）
    ├── references/
    │   └── tool-routes.md      # 工具命令速查
    └── scripts/                # 核心脚本（browser_session.py / adapter_registry.py 等）
```

## License

[MIT](./LICENSE)
