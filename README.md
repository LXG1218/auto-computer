# Auto Computer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64%20%2F%20ARM64)-0078D4.svg)](#)

**Auto Computer** 是一套面向 AI Coding Agent（Antigravity / Codex / 兼容 MCP 的客户端）的**后台优先 Windows 自动化 Skill 与 MCP 桥接工具包**。

相比传统的“截图 → 猜坐标 → 抢鼠标键盘”循环，它优先通过本地脚本、结构化 DOM/CDP 与 Windows 原生 UI Automation（`AutoSetFocus=false`）在后台完成执行并读回验证，尽量不打断用户当前窗口、鼠标和剪贴板。

---

## 核心能力（4 大域）

1. **域 1｜本地文件 / 表格 / 图像排版**
   - `scripts/run_task.py`（支持 `batch` 单进程多步串联）、`scripts/files_fast.py`、`scripts/table_ops.py`、`scripts/edit_saved.py`、`scripts/image_layout.py`。
   - 原生支持 `.xlsx` / `.xlsm` / `.csv` / `.tsv` 对账、安全编辑自动备份与回滚清单。
2. **域 2｜网页自动化（零弹窗隔离 Profile + 日常登录态接管）**
   - `mcp/edge_mcp.cjs`（`edge-devtools` MCP）：支持 `profile="auto"` 独立持久化配置（**免 Allow 弹窗、无自动化横幅**）以及 `profile="daily"` 接管日常 Edge。
   - 配合 `chrome-devtools-mcp`、`@playwright/cli`、`scripts/browser_session.py`（DPAPI 加密保存扩展令牌）与 `scripts/browser_headless.py`。
3. **域 3｜Windows 原生桌面控制（UIA → MSAA → Win32 → OCR → 显式前台兜底）**
   - `mcp/desktop_mcp.py`（`windows-uia` MCP）：封装 `list_windows`、`inspect_window`、`run_actions`（强制 `IUIAutomation2.AutoSetFocus = False` 校验）与带边界校验的 `foreground_desktop`。
   - 配套 `scripts/uia_background.py`、`scripts/msaa_background.py`、`scripts/win_background.py`、`scripts/window_ocr.py`（WinRT 内存直传 OCR）。
4. **域 4｜跨软件数据流转**
   - 强制先读回核验上一步产出，再写入下一域，防止未确认状态串联传播。

---

## 快速安装（3 步上手）

### 前置要求
- Windows 10 / 11（支持 x64 与 Windows ARM64）
- Python 3.10+
- Node.js 20+（用于浏览器 MCP）

### 1. 克隆并一键安装

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1
```

`install.ps1` 会自动完成：
- 在 `%LOCALAPPDATA%\AutoComputer\.venv` 创建独立 Python 虚拟环境并安装核心依赖（`comtypes`、`pywin32`、`pillow`、`numpy` 及可选 WinRT OCR）；
- 安装 `@playwright/cli` 与 `chrome-devtools-mcp` 到 `%LOCALAPPDATA%\AutoComputer\tools`；
- 自动备份并安装 Skill 到 `~/.gemini/config/skills/auto-computer`（Antigravity）和 `~/.codex/skills/auto-computer`（Codex）；
- 自动合并 `edge-devtools`、`windows-uia`、`chrome-devtools` 到 Antigravity 的 `mcp_config.json`。

> 可选参数：`.\install.ps1 -Target antigravity`（或 `codex` / `none`）、`-HomeDir D:\MyTools\AutoComputer`、`-SkipNode`、`-SkipOcr`、`-SkipMcpConfig`。

### 2. 首次使用授权体检与引导向导

```powershell
.\setup-auth.ps1
```

也可以只做只读体检：

```powershell
.\setup-auth.ps1 --check
```

---

## 首次使用需要完成哪些授权？

出于浏览器和系统安全机制，**涉及接管你个人日常已登录浏览器的权限，不能也不应该由外部脚本强行绕过**。各能力首次使用说明如下：

| 能力 | 是否需要额外授权 | 首次使用需要做什么 |
| --- | --- | --- |
| 本地文件 / 表格 / 图像排版 | **免授权** | 安装后立即可用 |
| Windows 后台 UIA / MSAA / OCR | **免授权** | 安装后立即可用 |
| 网页隔离模式 (`edge-devtools` `profile="auto"`) | **免授权** | 独立持久化目录，无弹窗、无自动化横幅，立即可用 |
| **Chrome 日常已登录会话** (`chrome-devtools` MCP) | **需一次性 1 步授权** | 在 Chrome 打开 `chrome://inspect/#remote-debugging` 开启远程调试，首次连接弹窗点一次 **Allow** |
| **Edge 日常已登录会话** (`edge-devtools` `profile="daily"`) | **需一次性 1 步授权** | 在 Edge 打开 `edge://inspect/#remote-debugging` 开启远程调试 |
| **Playwright 扩展后台重连** (`browser_session.py`) | **需一次性保存令牌** | 安装官方 [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)，运行 `.\setup-auth.ps1` 粘贴令牌（本机 Windows DPAPI 加密存储） |

运行 `.\setup-auth.ps1` 会自动检测以上所有项目，给出缺项指引并可在你点完授权后立即复检通过。

---

## 目录结构

```text
auto-computer/
├── install.ps1                 # 一键安装部署脚本
├── setup-auth.ps1              # 首次使用授权检查与引导入口
├── requirements.txt            # 核心 Python 依赖
├── requirements-ocr.txt        # 可选 WinRT 内存 OCR 依赖
├── templates/
│   └── mcp_config.example.json # 通用 MCP 配置模板
├── mcp/
│   ├── desktop_mcp.py          # windows-uia MCP 服务端
│   └── edge_mcp.cjs            # edge-devtools MCP 服务端
└── skill/auto-computer/
    ├── SKILL.md                # Skill 主路由规范
    ├── references/
    │   └── tool-routes.md      # 工具命令与适配参考
    └── scripts/                # 自动化核心脚本与 setup_auth.py
```

---

## 可选进阶组件

- **WinApp CLI**（增强 `foreground_desktop` / `winapp ui`）：可安装官方 [WinApp CLI](https://github.com/microsoft/winappCli/releases) 并放到 `%LOCALAPPDATA%\AutoComputer\tools\winapp\winapp.exe`（或设置环境变量 `AUTO_COMPUTER_WINAPP`）。
- **agent-browser**（CLI 高速无头批处理）：可设置环境变量 `AUTO_COMPUTER_AGENT_BROWSER` 或放到 `%LOCALAPPDATA%\AutoComputer\tools\agent-browser\agent-browser.exe`。

## 安全边界

- 默认禁止抢夺前台、移动用户鼠标或污染剪贴板；
- UIA 写入强制校验 `IUIAutomation2.AutoSetFocus == false` 与操作前后状态观测；
- 浏览器扩展令牌仅通过当前用户 Windows DPAPI 加密存储在本地 `%LOCALAPPDATA%\AutoComputer\secrets`；
- 严禁绕过浏览器 Allow 授权提示、验证码或人工确认环节。

## License

[MIT](./LICENSE)
