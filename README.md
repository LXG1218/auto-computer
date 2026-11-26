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

## 多 Agent 一键安装与接入（Antigravity / Codex / 通用 MCP）

### 前置要求
- Windows 10 / 11（支持 x64 与 Windows ARM64）
- Python 3.10+、Node.js 20+（用于浏览器 MCP）

### 1. 一键安装（同时支持 Antigravity + Codex + 通用 MCP）

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1
```

| 客户端 / 场景 | 安装方式 | 写入/注册内容 |
| --- | --- | --- |
| **全部自动接入（默认）** | `.\install.ps1`（即 `-Target all`） | 同时配置 Antigravity 与 Codex，并复用已有 Python/工具环境 |
| **Codex CLI MCP 一键安装** | `.\install.ps1 -Target codex` | 自动安装 Skill 到 `~/.codex/skills/auto-computer`，并通过 `codex mcp add` 一键注册 `edge-devtools` 与 `windows-uia` MCP |
| **Antigravity IDE** | `.\install.ps1 -Target antigravity` | 同步 Skill 到 `~/.gemini/config/skills/auto-computer`，合并更新 `~/.gemini/config/mcp_config.json` |
| **其他 MCP 客户端（通用）** | `.\install.ps1 -Target generic` | 生成标准 `%LOCALAPPDATA%\AutoComputer\mcp-config.json`（模板见 `templates/mcp_config.example.json`）供任意 Agent 导入 |

> 高级参数：`-PythonExe <已有python.exe>`（复用现有 `.venv`）、`-ToolsDir <已有tools目录>`、`-SkipNode`、`-SkipOcr`、`-SkipMcpConfig`。

---

## 首次使用：一键自助授权与扩展配对

出于安全机制，涉及接管个人已登录日常浏览器的权限**不会由脚本强行绕过**，而是通过内嵌自助向导一次性完成配置：

- **双击向导或命令行配对**：在安装目录（`%LOCALAPPDATA%\AutoComputer`）或仓库根目录直接双击 **`pair-browser.cmd`**，或在终端执行：

```powershell
.\setup-auth.ps1 --check          # 只读环境、MCP 与扩展/令牌体检
.\setup-auth.ps1 --pair chrome    # 引导安装 Playwright Extension 并加密保存 Chrome 令牌
.\setup-auth.ps1 --pair edge      # 引导配对 Edge 扩展令牌
```

| 能力 | 是否需要授权 | 首次使用需要做什么 |
| --- | --- | --- |
| 本地文件 / 表格 / 图像排版 | **免授权** | 安装后立即可用 |
| Windows 后台 UIA / MSAA / OCR | **免授权** | 安装后立即可用 |
| 网页隔离模式 (`edge-devtools` `profile="auto"`) | **免授权** | 独立持久化目录，**无弹窗、无“自动测试软件正在控制浏览器”横幅**，立即可用 |
| **Chrome 日常已登录会话** (`chrome-devtools` MCP) | **需一次性 1 步授权** | 在 Chrome 打开 `chrome://inspect/#remote-debugging` 开启远程调试，首次连接弹窗点一次 **Allow** |
| **Edge 日常已登录会话** (`edge-devtools` `profile="daily"`) | **需一次性 1 步授权** | 在 Edge 打开 `edge://inspect/#remote-debugging` 开启远程调试 |
| **Playwright 扩展后台重连** (`browser_session.py`) | **需一次性配对** | 运行 `.\pair-browser.cmd` 安装官方 [Playwright Extension](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm) 并粘贴令牌（Windows DPAPI 本机加密存入 `secrets/`） |

---

## v1.1.0 核心机制（后台优先 × 安全边界 × 本机持久化）

1. **后台优先 + 单个障碍短暂借前台 + 立即还回后台**：每步默认后台；获得整项任务临时前台授权后，也仅用于处理眼前无法后台的单个控件（如自绘菜单），障碍一解除立刻停止键鼠输入、后台读回并切回后台（用户切换窗口时不抢回）；**严禁**借前台点击远程调试“允许”。
2. **已有会话自动扫描 + 无自动化横幅**：通过 `browser_session.py status` 列出 `open_browser_sessions`，避免漏掉自定义命名会话（如 `auto-computer-edge-qa-v4`）而误触发弹窗；全链路后台浏览器不启用 `--enable-automation` 横幅。
3. **微信（WeChat/Weixin）风控硬排除**：`desktop_policy.py`、`adapter_registry.py`、`desktop_mcp.py` 及所有 UIA/MSAA/Win32/OCR 脚本默认屏蔽微信主进程及其全部子进程窗口。
4. **静默后台启动与按“动作”持久化适配**：已登记校验的应用通过 `launch_registered_app`（`SW_SHOWMINNOACTIVE`）后台静默启动；探索通过的新控件绑定由 `scripts/adapter_registry.py` 按 `(app, action)` 写入本机 `%AUTO_COMPUTER_HOME%\adapters`。

---

## 给 Agent 的一键升级指令（覆盖更新并保留本机适配与令牌）

> 复制下面整段发给你的 AI Agent（Codex / Antigravity / 其他工具），它会自动覆盖更新脚本与 Skill，**同时完整保留你电脑上已沉淀的软件控件接口 (`adapters/`) 与扩展授权令牌 (`secrets/`)**：

```text
请帮我更新本机 auto-computer 到最新版本并保留我现有的全部本机数据：
1. 进入我本机的 auto-computer 仓库目录（或重新拉取 https://github.com/LXG1218/auto-computer.git ），执行 git pull 获取最新 main 分支；
2. 覆盖更新 skill 与 MCP 时，绝对不能删除或覆盖 %LOCALAPPDATA%\AutoComputer\adapters（我本机各软件控件接口持久化目录）和 %LOCALAPPDATA%\AutoComputer\secrets（浏览器扩展授权令牌目录），也不要丢弃现有自定义环境配置；
3. 运行 .\install.ps1（按我本机已安装的客户端/Python 环境覆盖更新 Skill 与 MCP 桥接）；
4. 升级完成后运行 .\setup-auth.ps1 --check 与 python -m unittest 验证适配目录、授权令牌与核心能力均保留且正常工作，然后向我汇报结果。
```

---

## 目录结构

```text
auto-computer/
├── install.ps1                 # 一键安装/升级脚本（支持 Antigravity/Codex/通用 MCP，保 adapters 与 secrets）
├── setup-auth.ps1              # 首次使用授权体检与配对向导入口
├── pair-browser.cmd            # Windows 一键双击/命令行浏览器扩展配对入口
├── requirements.txt            # 核心 Python 依赖
├── requirements-ocr.txt        # 可选 WinRT 内存 OCR 依赖
├── templates/
│   └── mcp_config.example.json # 通用 stdio MCP 配置模板
├── mcp/
│   ├── desktop_mcp.py          # windows-uia MCP 服务端（含静默启动与微信拦截）
│   └── edge_mcp.cjs            # edge-devtools MCP 服务端
├── tests/                      # 单元与持久化/升级保护验证测试
└── skill/auto-computer/
    ├── SKILL.md                # Skill 主路由规范
    ├── references/
    │   └── tool-routes.md      # 工具命令、适配与升级参考
    └── scripts/                # 核心脚本（含 adapter_registry.py / desktop_policy.py / setup_auth.py）
```

---

## 可选进阶组件与安全边界

- **WinApp CLI**：可安装官方 [WinApp CLI](https://github.com/microsoft/winappCli/releases) 放到 `%LOCALAPPDATA%\AutoComputer\tools\winapp\winapp.exe`（或设 `AUTO_COMPUTER_WINAPP`）。
- **agent-browser**：可放到 `%LOCALAPPDATA%\AutoComputer\tools\agent-browser\agent-browser.exe`（或设 `AUTO_COMPUTER_AGENT_BROWSER`）。
- **安全边界**：默认禁止抢夺前台或污染剪贴板；UIA 写入强制校验 `AutoSetFocus == false`；全面拒绝接管微信进程；扩展令牌仅经 Windows 当前用户 DPAPI 加密存放在本机 `secrets/`；严禁绕过浏览器 Allow 弹窗或验证码。

## License

[MIT](./LICENSE)
