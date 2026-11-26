# Auto Computer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64%20%2F%20ARM64)-0078D4.svg)](#)

**Auto Computer** 是面向 AI Coding Agent（Antigravity / Codex / 通用 MCP 客户端）的**后台优先 Windows 自动化 Skill 与轻量 MCP 工具包**。

相比“截图 → 猜坐标 → 抢鼠标键盘”循环，它通过**本地脚本、Chromium 命名管道（`--remote-debugging-pipe`）、Playwright 扩展令牌与常驻 Windows UIA 守护进程（`AutoSetFocus=false`）**在后台完成操作与核验；仅在目标软件必须借用前台时，才显示可按 `Esc` 立即停止的屏幕边缘光晕与顶部提示条。

---

## 给 AI Agent 的一键安装 / 更新指令（直接复制发给你的 Agent）

想让任意 Agent（Antigravity / Codex 等）**一次性装全所有功能**（不分基础/进阶）或在更新时**完整保留本机规则与授权**，把下面这段话直接发给它即可：

```text
请帮我在 Windows 本机完整安装或更新 https://github.com/LXG1218/auto-computer 仓库里的 Auto Computer，要求一次性装全全部功能，开箱即用，不做基础/进阶拆分：

1. 克隆或更新仓库：
   - 若本地尚无仓库，克隆 `https://github.com/LXG1218/auto-computer.git`；
   - 若已是更新，先 `git pull` 拉取最新代码，然后直接覆盖运行 `.\install.ps1`（默认 `-Target all` 会自动配置 Python `.venv`、全部基础+WinRT OCR 依赖、Node `@playwright/cli`、常驻 UIA MCP、命名管道浏览器 MCP，以及 Antigravity / Codex / 通用 MCP 配置与 Skill；不要跳过可装项）。
2. 全量体检与收尾：
   - 运行 `.\setup-auth.ps1 --check` 验证域 1（文件/表格/图像）、域 2（命名管道 Chrome/Edge 网页）、域 3（常驻 Windows UIA + MSAA + WinRT OCR + 借前台安全护栏）全部显示就绪；
   - 若我需要后台接管日常已登录的 Chrome / Edge 标签页，直接运行 `.\pair-browser.cmd` 并指引我点选一次浏览器扩展配对，使之完全就绪。
3. 更新与安装期间【严禁改动或覆盖】以下本机资产：
   - `%LOCALAPPDATA%\AutoComputer\adapters\`（本机已学习的软件控件适配库）
   - `%LOCALAPPDATA%\AutoComputer\secrets\`（本机 DPAPI 加密的浏览器扩展令牌）
   - `%LOCALAPPDATA%\AutoComputer\profiles\`（`kept` 专用登录浏览器空间与登录态）
   - `%LOCALAPPDATA%\AutoComputer\local-rules.md`（用户本机私有规则，如网络/代理分流、常用网站路由）
   - 宿主已有的其他 MCP Server 配置（仅增量更新 `edge-devtools` 与 `windows-uia`，严禁删除其他 MCP，也不得往 `chrome-devtools` 写入 `--autoConnect`）
   - 用户全局规则文件（如 `~/.codex/AGENTS.md`、`~/.gemini/config/GEMINI.md`）
```

---

## 命令行安装（手动触发等同上）

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1
.\setup-auth.ps1 --check
```

- `.\install.ps1` 默认 `-Target all`：一次性安装完整 Python 依赖与 WinRT OCR（`requirements.txt` + `requirements-ocr.txt`）、Node `@playwright/cli`，并同步 Antigravity（`~/.gemini/config`）、Codex（`~/.codex` + `codex mcp add`）与通用 `%LOCALAPPDATA%\AutoComputer\mcp-config.json`（若未装 Codex 会自动跳过 Codex 注册而不中断其余安装）。
- 如本机已有虚拟环境，可传 `-PythonExe <path>`；重新运行 `.\install.ps1` 即为无损升级，脚本自动备份旧文件并完整保留 `adapters/`、`secrets/`、`profiles/` 与 `local-rules.md`。
- 如需后台复用当前日常浏览器已打开登录态标签页，执行 `.\pair-browser.cmd` 完成一次 Chrome / Edge Playwright 扩展配对即可。

---

## 核心能力

1. **域 1｜本地文件 / 表格 / 图像排版**：`run_task.py`（`batch` 单进程闭环）、`files_fast.py`、`table_ops.py`（`.xlsx`/`.xlsm`/`.csv`/`.tsv` 对账）、`edit_saved.py`（自动备份与回滚）、`image_layout.py`。
2. **域 2｜零端口零横幅网页自动化（Edge & Chrome 全适配）**：
   - `mcp/edge_mcp.cjs`（`edge-devtools` MCP）：基于 Chromium `--remote-debugging-pipe` 命名管道，支持 `profile="auto"`（匿名隔离）与 `profile="kept"`（专用保留登录空间），**0 TCP 端口、0 Allow 弹窗、0 自动化横幅**；带 `read`/`read_list` 时默认跳过冗余整页摘要。
   - `edge-devtools.daily_browser_run`：单次调用内检查 Chrome / Edge 已授权日常会话、核对目标标签 URL（支持 `read`/`read_list`/`wait_for`）并自动 `detach` 消掉扩展调试横幅；命令行入口仍可通过 `scripts/browser_session.py` 使用。
3. **域 3｜常驻 Windows UIA 守护与极光借前台护栏**：
   - `mcp/desktop_mcp.py`（`windows-uia` MCP）+ `scripts/win_uia_worker.py`：常驻 STA Python UIA 守护进程消除每次冷启动，`inspect_window` 支持 `max_bytes` 字节预算与 `delta_base` 增量控件对比，大幅压缩响应体积与 Token；
   - `run_actions` 支持传入 `binding_app` + `actions[].binding`，在一次调用内完成已验证本机控件的执行与读回，省去重复控件树扫描；
   - `foreground_desktop` 与 `run_actions(allow_foreground=true)` 在短时借前台期间联动 `mcp/foreground_guard.py` 显示**点击穿透 32-bit 极光流光边框 + 悬浮提示胶囊 + 底层物理钩子 `Esc` 红闪急停与粘键释放**，动作完立即收起，并在键鼠注入前校验 `POINT_COVERED` 窗口遮挡。
4. **域 4｜跨软件数据流转与本机规则解耦**：
   - 强制先读回核验再跨软件写入；
   - 本机私有规则独立存放在 `%AUTO_COMPUTER_HOME%\local-rules.md`（不进 Git 仓库、升级永不覆盖）。

---

## 目录结构

```text
auto-computer/
├── install.ps1                 # 全量一键安装/无损升级脚本（保护 adapters / secrets / profiles / local-rules.md）
├── setup-auth.ps1              # 环境与各域就绪核验入口
├── pair-browser.cmd            # Chrome / Edge 扩展一键配对入口
├── mcp/
│   ├── desktop_mcp.py          # windows-uia MCP（常驻守护进程、增量树、字节预算、遮挡校验与管道防死锁）
│   ├── foreground_guard.py     # 跨 Agent 借前台点击穿透边缘光晕 + 顶部提示条 + Esc 急停
│   └── edge_mcp.cjs            # edge-devtools MCP（命名管道 auto/kept 模式）
├── tests/                      # 单元与回归测试
└── skill/auto-computer/
    ├── SKILL.md                # 精简主路由规范（自动加载本机 local-rules.md）
    ├── references/
    │   └── tool-routes.md      # 工具命令速查
    └── scripts/                # 核心脚本（win_uia_worker.py / browser_session.py / adapter_registry.py 等）
```

## License

[MIT](./LICENSE)
