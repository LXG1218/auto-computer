# 工具命令参考

调用当前任务需要的工具。先用工具自身 `--help` 或当前 MCP schema 确认本机版本支持的参数。按授权在专用目录准备缺少的工具，保留现有软件和全局配置。

## 本机工具路径

默认数据与工具根目录为 `$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { "$env:LOCALAPPDATA\AutoComputer" }`，运行 `install.ps1` 后各组件默认位于 `$acHome`。先做 `--version` 或 `setup_auth.py --check` 确认可用性。

```powershell
$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { Join-Path $env:LOCALAPPDATA 'AutoComputer' }
$pythonExe = Join-Path $acHome '.venv\Scripts\python.exe'
$nodeExe = 'node'
$cliEntry = Join-Path $acHome 'tools\playwright\node_modules\@playwright\cli\playwright-cli.js'
$chromeDevtoolsEntry = Join-Path $acHome 'tools\chrome-devtools-mcp\node_modules\chrome-devtools-mcp\out\Default\gen\src\bin\chrome-devtools-mcp.js'
$winappExe = Join-Path $acHome 'tools\winapp\winapp.exe'
$agentBrowserExe = Join-Path $acHome 'tools\agent-browser\agent-browser.exe'
& $nodeExe $cliEntry --version
$env:WINAPP_CLI_TELEMETRY_OPTOUT = '1'
if (Test-Path $winappExe) { & $winappExe --version }
& $nodeExe $chromeDevtoolsEntry --version
if (Test-Path $agentBrowserExe) { & $agentBrowserExe --version }
```

Playwright 子命令使用 `& $nodeExe $cliEntry ...`。WinApp 子命令使用 `& $winappExe ui ...`。本地脚本与桌面状态读回默认使用 `$acHome\.venv\Scripts\python.exe`（也可通过环境变量 `AUTO_COMPUTER_PYTHON` 指定）。

## 文件移动、对账和源文件编辑

以下示例在 Skill 目录执行，以用户实际给出的路径、筛选条件和字段替换示例参数。文件移动使用一个命令完成筛选、移动、读回和恢复记录。任务授权移动多个匹配项时加 `--allow-multiple`。

```powershell
$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { Join-Path $env:LOCALAPPDATA 'AutoComputer' }
$pythonExe = Join-Path $acHome '.venv\Scripts\python.exe'
$backupRoot = Join-Path $acHome 'backups'
& $pythonExe .\scripts\run_task.py file-find --root 'D:\Source' --glob '*.json' --recursive
& $pythonExe .\scripts\run_task.py file-move --root 'D:\Source' --glob 'invoice.pdf' --destination 'D:\Archive'
& $pythonExe .\scripts\run_task.py file-undo --manifest 'D:\Archive\实际返回的记录.json'
& $pythonExe .\scripts\run_task.py table-reconcile --left 'D:\A.csv' --right 'D:\B.xlsx' --header-row 5 --key '商品编号' --columns '价格,库存'
& $pythonExe .\scripts\edit_saved.py text --file 'D:\Doc.txt' --backup-root $backupRoot --before '旧文本' --after '新文本'
& $pythonExe .\scripts\edit_saved.py csv --file 'D:\Orders.csv' --backup-root $backupRoot --key-column '编号' --key '2609' --column '状态' --before '待核对' --after '已核对'
& $pythonExe .\scripts\edit_saved.py restore --audit (Join-Path $backupRoot '实际返回的审计.json')
& $pythonExe .\scripts\run_task.py batch --steps '[{"op":"edit-csv","file":"D:\\Orders.csv","backup_root":"backups","key_column":"编号","key":"2609","column":"状态","before":"待核对","after":"已核对"},{"op":"read-text","file":"D:\\Orders.csv","contains":"2609"}]'
```

连续多步文件/表格/编辑任务优先使用 `run_task.py batch --steps ...`，在单次 Python 进程内执行并支持 `"$prev.manifest"`、`"$prev.audit"`、`"$step[0].manifest"` 引用，出错即停并保留已完成步骤证据。`file-find` / `files_fast.py find` 默认不递归，查子目录时需显式传 `--recursive`。表格含前置标题行（如第 5/6 行才是正式列名）时，可为 `table_ops.py` 与 `table-reconcile` 传入 `--header-row N`（左右表表头行不同时用 `--left-header-row` / `--right-header-row`）。源文件编辑返回实际前后值及备份、审计路径。把单个任务的备份保存在专用目录，按审计记录关联原文件和版本。恢复前核对当前内容仍为脚本写入的版本。发现其他改动时保留现场及备份。`table-reconcile` 读取 CSV、TSV 和 XLSX，`edit_saved.py` 处理 UTF-8 文本及 CSV 的明确位置。

## Excel 16.0 已保存工作簿

在本机已用 Excel Automation 的无界面实例修改测试 `.xlsx`，备份和写回后文件分别读出 B2 的原值和新值。实际任务可以按已保存文件试用这一后端。先确认 `DispatchEx` 创建的实例 PID 与现有 Excel 进程不同且 `Application.Visible` 为假，只打开用户指定的已保存文件或本次测试文件，只关闭本次创建的实例。操作前留备份，写回后用单元格读回和独立文件读取确认实际变化。

用户要求改当前打开的工作簿或保留未保存内容时，定位并核对其当前会话。测试结果覆盖已保存 `.xlsx` 的独立后台实例。

## CLI-Anything 应用适配

先用软件自身的命令行或公开 API。任务需要额外封装时，按应用查 [CLI-Anything 工具目录](https://github.com/HKUDS/CLI-Anything/blob/main/registry.json)，选择一个对应的 `agent-harness`。核对源码、许可证、版本、Windows/ARM64 依赖以及命令实际连接的后端，再在 `auto-computer` 的专用目录试用。候选包通过本 Skill 直接调用经过验收的 CLI。

先用临时文件或可逆操作确认命令运行、结构化输出能解析，以及目标产物或应用状态确实改变。软件依赖实际渲染器或服务时，要验收该后端的真实结果。需要封装新动作时，可按 [上游 HARNESS 方法](https://github.com/HKUDS/CLI-Anything/blob/main/cli-anything-plugin/HARNESS.md) 核对数据格式、调用目标和读回证据，再把通过的动作登记进本 Skill。

涉及现有 Edge 标签页、打开的文档或应用未保存状态时，核对 CLI 是否能连接同一个会话。文件转换或新建文档可按该任务指定的文件路径和后端执行。

## 本地后台控件

以下示例在 Skill 目录执行。示例中的句柄、PID、AutomationId 和文字均需从当前任务取得。

先通过 `Get-Process -Name <exe-name>` 确认目标 PID 和 `MainWindowHandle`。有多个窗口或主句柄为空时，使用 `winapp ui list-windows -a <pid> --show-hidden` 核对窗口句柄。查找原生输入控件时，可用 MSAA 返回的窗口句柄作为候选，再检查原生类和顶层归属。

```powershell
$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { Join-Path $env:LOCALAPPDATA 'AutoComputer' }
$pythonExe = Join-Path $acHome '.venv\Scripts\python.exe'
& $pythonExe .\scripts\desktop_state.py
& $pythonExe .\scripts\win_background.py show-noactivate --hwnd 0x12345 --pid 1234
& $pythonExe .\scripts\win_background.py set-native-edit --hwnd 0x12345 --control-hwnd 0x12346 --pid 1234 --text '测试文字'
& $pythonExe .\scripts\win_background.py minimize-noactivate --hwnd 0x12345 --pid 1234
```

原生路线用目标顶层窗口、同 PID 和控件所属窗口核对范围。`set-native-edit` 通过 `WM_SETTEXT` 写入支持的 Win32 Edit/RichEdit，再用 `WM_GETTEXT` 逐字确认。隐藏窗口和最小化窗口使用无激活显示，只有需要恢复本次临时状态时才用 `hide-noactivate` 或 `minimize-noactivate`。控件返回 `readback_matches` 表示文字已核对。前台还需核对 `foreground_observed`、`foreground_hook_installed`、前后句柄和 `foreground_events`，并比较鼠标、剪贴板序号。

普通后台写入遇到目标窗口或它的所属对话框在前台时会暂停。用户明确要求在前台演示，且指定窗口已在前台时，可为 `set-native-edit` 加 `--foreground-demo`。UIA 的同名参数放在 `set-value`、`invoke` 或 `select` 子命令之前。模式参数允许操作该窗口的控件，不转移系统输入。返回暂停后，先根据 `action_attempted` 和读回证据确认实际是否生效，再决定续跑。

UIA 和 MSAA 使用 Python 的 `comtypes`。脚本会自动检测 venv 内依赖或挂载 `$acHome\tools\python-overlays`（也可用环境变量 `AUTO_COMPUTER_OVERLAY` 覆盖），无需每次手动拼接 `PYTHONPATH`。

```powershell
& $pythonExe .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 --automation-id Input --control-type edit inspect
& $pythonExe .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 --automation-id Input --control-type edit set-value --value '测试文字'
& $pythonExe .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 --automation-id Apply --expect-automation-id Status --expect-value '已应用' invoke
& $pythonExe .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 batch --actions '[{"operation":"find","name":"确定","control_type":"button"}]'
& $pythonExe .\scripts\msaa_background.py --hwnd 0x12345 --pid 1234 --name '搜索'
```

`uia_background.py` 先关闭并读回 `IUIAutomation2.AutoSetFocus` 再检索。`find` 只返回匹配元数据。`inspect` 读取支持的 ValuePattern。`invoke`、`select` 必须指定能在操作后精确读回的页面状态，操作前检查该状态是否已生效。多步顺序动作优先使用 `batch`，在同一个校验后的 `AutoSetFocus=false` 会话中执行并按序停错。工具报告 `action_attempted=true` 而读回失败时，先查当前状态再决定是否需要其他动作。为零的前台句柄表示状态未观测到，需要补验（`desktop_state.py` 默认自动短重试瞬态零句柄）。

`msaa_background.py` 在指定窗口和进程内按确切名称或角色查询，可以收窄 `--max-nodes`、`--max-depth` 与 `--max-child-hwnds`。它返回匹配标识和遍历范围，用于定位和复核。出现遍历上限时，仅凭零匹配不能断定控件不存在。

实现参考 [Microsoft UIA AutoSetFocus](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomation2-put_autosetfocus)、[Microsoft 窗口显示命令](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-showwindow)、[Ghost](https://github.com/NORTHTEKDevs/ghost)、[desk-mcp 的 MSAA 实现](https://github.com/DigitalDog1/desk-mcp/blob/main/worker.ps1) 和 [Acc-v2](https://github.com/Descolada/Acc-v2)。

## 桌面截图和前台操作

`windows-uia` 暴露 `foreground_desktop`。用户允许前台操作后，用 `list_windows` 确定目标窗口。调用需带 `foreground: true`、`hwnd`、`expected_pid` 和 `expected_bounds`。将 `list_windows` 返回的 `physicalBounds` 按原顺序传入 `expected_bounds`。用 `action: screenshot` 取得目标窗口的屏幕裁剪和坐标范围，再用最新观察做一次前台动作。`click`、右键、双击、拖动、滚动、快捷键和文字输入使用已安装的 WinApp CLI。当前 `scroll` 需要可定位的 UIA 控件。

坐标使用截图返回的物理屏幕范围。输入还要提供 `expected_foreground`。如果用户已切换窗口、窗口移动或旧句柄失效，先重新观察。后台识别文字仍使用已有 PrintWindow 和 OCR 路线。已有 WinForms 样例实测，遮挡时用 `winapp ui screenshot -w <hwnd> --json` 取得目标画面，前台句柄保持一致。该窗口图与屏幕裁剪的尺寸可能不同，坐标操作使用显式前台图。在另一个窗口弹出菜单、文件选择框或警告时，重新列出并确认该对话框的句柄之后再操作。WinApp CLI 的返回状态可确认输入是否注入。文件、控件或新画面提供业务结果证据。[WinApp UI 自动化行为](https://github.com/microsoft/winappCli/blob/main/docs/ui-automation.md)

Codex 未连接 `windows-uia` 时，可从本机 `%LOCALAPPDATA%\AutoComputer\mcp\desktop_mcp.py` 的 stdio 接口调用。先用 `tools/list` 取得 `foreground_desktop` 的精确参数。旧应用专用前台动作在 MCP 的 `run_actions` 中须显式传 `allow_foreground: true`，直接调用 PowerShell 时须传 `-AllowForeground`。

## 已授权窗口的本机识别

现有接口、控件及适用的开源方案均走不通时，可在任务指定窗口使用。Python 环境优先使用已安装的 `winrt` 进行内存直传识别（单子进程内完成 `PrintWindow` 与 `Windows.Media.Ocr`），若环境缺少 `winrt` 则自动回退到 PNG + `local_ocr.ps1`。

```powershell
& $pythonExe .\scripts\window_ocr.py --hwnd 0x12345 --pid 1234 --keyword '搜索' --language-tag zh-Hans-CN
& $pythonExe .\scripts\window_ocr.py --hwnd 0x12345 --pid 1234 --region 900,350,600,300 --keyword '搜索' --language-tag zh-Hans-CN
```

工具只截图指定窗口的客户区，在本机 OCR 后返回指定关键词的匹配矩形和前后观测状态。传 `--region x,y,w,h` 可缩小识别区域进一步提速，返回坐标仍自动换算为客户区左上角原点。临时图像用完清理，不进入对话。若需补充识别语言包（如英文 `en-US`），由管理员 PowerShell 执行：`Add-WindowsCapability -Online -Name 'Language.OCR~~~en-US~0.0.1.0'`。只在匹配唯一、窗口状态与当前任务一致时使用定位结果。应用后台是否接受动作，按实际动作读回判定。

实现参考 [PrintWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow) 与 [Windows.Media.Ocr](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)。

## Playwright CLI：连接现有 Edge

官方文档：

- [Playwright CLI](https://playwright.dev/docs/getting-started-cli)
- [会话连接与扩展模式](https://github.com/microsoft/playwright/blob/main/packages/playwright-core/src/tools/skills/playwright-cli/references/session-management.md)
- [官方 Playwright 扩展](https://chromewebstore.google.com/detail/playwright-extension/mmlmfjhmonkocbjadbfplnigmagldckm)

商店从当前 Edge 网络不可达时，可从官方扩展的本地解压目录安装（在 Edge 的 `edge://extensions` 打开开发人员模式后加载解压目录）。操作步骤见 [微软本地扩展加载说明](https://learn.microsoft.com/en-us/microsoft-edge/extensions/getting-started/extension-sideloading)。

首选命令：

```powershell
$env:PLAYWRIGHT_MCP_BACKGROUND = '1'
& $nodeExe $cliEntry attach --extension=msedge --no-tab-group
& $nodeExe $cliEntry -s=msedge snapshot
```

扩展连接采用用户已安装的官方扩展，并遵循连接页的标签选择。优先复用已完成授权的会话，记录会话名和启动时的工作目录。缺少已授权连接时，先检查目标浏览器与扩展连接状态。需要首次配对时，仅报告本机授权入口，由用户自行选择时机完成。随后用 `snapshot` 核对明确页面元素，分批操作并读回。使用 `-s=msedge detach` 断开连接并保留现有标签页。

本机 Edge 157 和 Playwright Extension 0.6.0 已用 `Default` 配置连接现有标签页。若点「Allow」后原 relay 报 WebSocket 错误，先核对监听是否仍在。实际重试时给本次 Node 进程的 `NODE_OPTIONS` 加 `--dns-result-order=ipv4first`，确认新 relay 地址为 `127.0.0.1`，再用新地址配对。当前 Edge 配置和代理设置维持原样。[Playwright IPv4 回环排障](https://github.com/microsoft/playwright/issues/41180)

隐藏标签页的本机实测中，输入框 `fill` 和文件输入框 `setInputFiles` 能读回结果。菜单由 JavaScript 事件处理时，先定位唯一控件，再用它实际监听的事件打开菜单，逐级核对 `aria-expanded` 或可见项后继续。[Playwright 操作检查](https://playwright.dev/docs/actionability)

`upload` 需要页面文件选择器已经打开。网页有可定位的文件输入框时，可用 `run-code` 调用 `locator.setInputFiles()`，传入用户提供并授权用于该页面的文件。读取页面的文件名、数量或上传状态确认结果。这个路径适合避免唤起 Windows 文件选择窗口。

### 隐藏标签页的纯 CSS 菜单

当 `:hover` 控制菜单显示且常规悬停超时，复用已授权的 Edge 会话。先用 `run-code` 在唯一匹配的目标标签页只读检查触发项、菜单项和 `document.styleSheets`、`document.adoptedStyleSheets`。按触发项和菜单范围筛选相关 `CSSStyleRule`，记录浏览器返回的精确 `selectorText`。把这些信息写为 UTF-8 请求文件。以下是两级菜单样例。

```json
{
  "url": "http://127.0.0.1:53656/",
  "hoverSelectors": ["#menu", "#submenu"],
  "ruleSelectors": [".menu:hover > .submenu, .submenu:hover > .deep"],
  "itemSelector": "#deep"
}
```

从已连接会话的工作目录运行，传入 Skill 脚本和请求文件的绝对路径。

```powershell
& $nodeExe "$skillRoot\scripts\edge_css_menu.cjs" --cli $cliEntry --session $sessionName --request-file $requestPath
# 若需在同一 run-code 合并后续操作以消除嵌套 Node 冷启，可加 --emit-snippet --post-snippet <post.js> 输出单次代码：
& $nodeExe "$skillRoot\scripts\edge_css_menu.cjs" --emit-snippet --request-file $requestPath --post-snippet $postJs
```

读取 Playwright JSON 的 `result`，确认 `ok: true`、`restored: true` 和 `items` 命中预期，后台验收时确认 `visibility: hidden`。脚本在一次页面调用内临时改写指定样式规则、读取浮现的选项并恢复。它只负责读取选项。样式规则受同源限制或目标不在文档范围内时，检查页面提供的键盘、搜索或按钮入口。能执行时读回选中值。若仍缺少可验证的入口，记录缺失的规则或控件。[Storybook Pseudo States](https://github.com/storybookjs/storybook-addon-pseudo-states)

## Playwright MCP：同扩展后备

官方文档：

- [Playwright MCP 扩展配置](https://github.com/microsoft/playwright.dev/blob/main/mcp/configuration/browser-extension.mdx)
- [官方扩展说明](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md)

配置示意（仅供用户明确授权添加 MCP 时参考，不代表本机已配置）：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "node",
      "args": [
        "<AUTO_COMPUTER_HOME>/tools/playwright-mcp/node_modules/@playwright/mcp/cli.js",
        "--browser", "msedge", "--extension", "--background", "--no-tab-group"
      ]
    }
  }
}
```

它复用同一官方扩展和现有登录态。只有本机已提供该 MCP 且 CLI 路线受阻时使用；不要擅自更改现有 MCP 配置。

## 日常浏览器连接与令牌

运行 `browser_session.py status` 查看指定日常 Chrome 或 Edge 的运行版本、Profile、扩展和可复用连接。已有授权会话优先使用。Chrome 扩展支持后台打开连接页时，可重连同一 Profile。Edge 现有授权会话仍可复用，新的严格后台重连待验证。

```powershell
$pythonExe = Join-Path $env:LOCALAPPDATA 'AutoComputer\.venv\Scripts\python.exe'
& $pythonExe .\scripts\browser_session.py status --browser chrome --profile Default
& $pythonExe .\scripts\browser_session.py save-token --browser chrome --profile Default
& $pythonExe .\scripts\browser_session.py reconnect --browser chrome --profile Default --session auto-computer-chrome-default
```

`save-token` 是用户本机的一次性设置。用户在对应浏览器 Profile 点击 Playwright Extension 图标，从管理页复制认证令牌，在终端的隐藏输入中粘贴。令牌经 Windows 当前用户 DPAPI 加密保存，使用时仅传给 Playwright 子进程。支持调用环境中已提供的 `PLAYWRIGHT_MCP_EXTENSION_TOKEN`。令牌保留在本机授权流程，避免发到聊天或写进命令参数。

Chrome `158.0.8127.2` 起，已运行的 Chrome 配合 Playwright Extension 0.5.0 或以上、认证令牌和后台模式，可以后台打开连接页。缺少令牌或版本条件时返回缺失条件。保持现有 Profile，保留已有授权会话。验证前台句柄和活动窗口状态，再开始任务写入。[Playwright 扩展认证及后台模式](https://github.com/microsoft/playwright/blob/main/packages/extension/README.md)

## 浏览器登录态预判

先按 [浏览器登录态与工具选择](../SKILL.md#浏览器登录态与工具选择) 确定路线。所有需要登录态的任务，在本机指定的日常浏览器复用已获授权的后台连接。`chrome-devtools` 对应 Chrome，Playwright 扩展使用目标浏览器内的授权，`edge-devtools` 的 `profile="daily"` 对应日常 Edge。后台开页与操作要保留当前窗口、活动标签和鼠标。匿名公开只读且未指定日常窗口时，才使用无头或自动 Profile。连接、登录或账号不匹配时核对同一浏览器、Profile 和标签页，保持既有会话。

## Edge DevTools MCP：独立自动 Profile 与日常 Edge

- 服务入口：`%LOCALAPPDATA%\AutoComputer\mcp\edge_mcp.cjs`（宿主 `mcp_config.json` 中的 `edge-devtools`）。
- **核心工具（7 个，重载 Antigravity MCP 后生效）**：
  - `open_and_run`（独立自动 Profile）：在独立持久化 Profile（默认 `%LOCALAPPDATA%\AutoComputer\profiles\edge-auto` 或 `chrome-auto`，可用 `AUTO_COMPUTER_PROFILE_ROOT` 覆盖）上 **1 次调用完成**「开页 → 40ms 轮询等待 DOM/`wait_selector`（绝不等 `networkidle`）→ 最多 30 步交互/提取（含 `read_list`）→ 读回摘要与关闭」。默认 `profile="auto"`, `browser="edge"|"chrome"`, `visible=false`；严格后台任务保持 `visible=false`。
  - `list_tabs`、`inspect_tab`、`open_tab`、`close_tab`、`run_actions`（支持 `read_list`、`read`、`assert`、`fill`、`select`、`check`、`uncheck`、`click`、`press_enter`、`wait`、`wait_for`、`set_files`、`read_hover_menu`）、`stop_auto_browser`。
- **双模式分工**：
  - `profile="auto"`（工具参数默认值）：用于可匿名完成的公开只读任务或用户明确要求的匿名隔离。
  - `profile="daily"`：接管已启用 `edge://inspect/#remote-debugging` 的日常 Edge，用于需要账号的任务或用户指定日常 Edge 的任务。
- 安全边界：仅允许 `http:`/`https:` URL，拒绝任意 JavaScript，不返回密码字段，后台操作不激活日常窗口。

## 无头高速通道与日常浏览器拉起（agent-browser 直调 + browser_headless.py）

- **公开匿名只读的无头批处理（Argument Mode）**：把每步作为独立字符串参数传给可选安装的 `agent-browser.exe`（`%LOCALAPPDATA%\AutoComputer\tools\agent-browser\agent-browser.exe` 或环境变量 `AUTO_COMPUTER_AGENT_BROWSER`），省去约 250–300 ms 的 Python 包装冷启且无编码陷阱：

```powershell
$agentBrowserExe = Join-Path $env:LOCALAPPDATA 'AutoComputer\tools\agent-browser\agent-browser.exe'
$edgeExe = 'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
& $agentBrowserExe --session ac-fast --executable-path $edgeExe batch --bail --json `
  "open https://example.com" "snapshot -i" "close"
```

- **含复杂多引号 JS/`eval` 的 JSON 批处理**：**严禁在 Windows PowerShell 中用 `'[[...]]' | & $agentBrowserExe batch` 管道传 JSON**（Windows PowerShell 5.1 管道会注入 `EF BB BF` UTF-8 BOM，触发 `Invalid JSON input: expected value at line 1 column 1`）。应写无 BOM UTF-8 临时文件并用 `cmd.exe /d /c` 输入重定向（或由 Python `subprocess.run(..., input=...)` 传入）：

```powershell
$tmp = Join-Path $env:TEMP "ab_batch.json"
[System.IO.File]::WriteAllText($tmp, '[["open","https://example.com"],["snapshot","-i"],["close"]]', [System.Text.UTF8Encoding]::new($false))
cmd.exe /d /c "`"$agentBrowserExe`" --session ac-fast --executable-path `"$edgeExe`" batch --bail --json < `"$tmp`""
Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
```

- **多页结构化抓取与日常浏览器拉起**：`scripts/browser_headless.py` 负责多页正文/链接/选择器一次性抓取（`headless-fetch`），或统一 JSON 封装（`headless-batch`），以及日常浏览器的只读检测（`open-logged-in`）：

```powershell
& $pythonExe .\scripts\browser_headless.py headless-fetch --browser edge --url "https://example.com" --selector "h1,a"
& $pythonExe .\scripts\browser_headless.py open-logged-in --browser edge
```

- **本机日常会话**。`open-logged-in` 仅检查对应浏览器进程以及可用连接，继续在获授权的日常浏览器中后台开页。`open-auto-profile` 会出现独立可见窗口，保持后台或需要账号时均不调用。

## Chrome DevTools MCP：Chrome 现有会话与抓包排错

- 上游：`ChromeDevTools/chrome-devtools-mcp`（由 `install.ps1` 安装在 `%LOCALAPPDATA%\AutoComputer\tools\chrome-devtools-mcp`）。
- 宿主配置：宿主 `mcp_config.json` 中的 `chrome-devtools`，使用本地入口 + `--autoConnect`。
- 优先先通过 `.\setup-auth.ps1 --check` 确认本机授权状态；Chrome 首次连接需勾选 `chrome://inspect/#remote-debugging` 并在弹窗点击一次 `Allow`。
- 极速少回合流程：Chrome 已开时**直接调 MCP，不再先空跑 `open-logged-in`**；优先复用实际目标标签页；必须新开时显式 `new_page(background=true)`，选择页面时 `select_page(bringToFront=false)`，再用**单次 `evaluate_script`**（或 `take_snapshot` + 合并操作）读回结构化结果，常规任务控制在 **2–3 次工具调用内**（审计排错时配合 `list_network_requests` / `list_console_messages` / `get_css_styles`）。
- 边界与风险：
  - `--autoConnect` 会暴露该 Chrome Profile 下的标签页、Cookie 与存储状态，注意隐私与授权边界；
  - 仅用于目标在 Chrome 或需要 Network/Console/Cookie/样式审计的场景，不用于替代 Edge 的 Playwright 批量后台填表；
  - 交互动作可能影响标签页或焦点状态，严格后台任务需在动作前后用 `scripts/desktop_state.py` 核对前台句柄。

## Microsoft WinApp CLI：桌面 UIA

官方文档：

- [UI Automation 命令](https://github.com/microsoft/winappCli/blob/main/docs/ui-automation.md)
- [UI Automation 行为与桌面占用说明](https://github.com/microsoft/WinAppCli/blob/main/plugins/winapp/skills/winapp-ui-automation/SKILL.md)
- [WinApp CLI Releases](https://github.com/microsoft/WinAppCli/releases)

基础命令示例：

```powershell
winapp ui inspect -a notepad
winapp ui list-windows -a notepad
winapp ui search Save -a notepad
winapp ui get-value FileName -a notepad
winapp ui wait-for SavedIndicator -a notepad --timeout 10000
```

控件名称只是语法示例，必须根据实际 inspect/search 结果替换。优先 UIA-pattern 命令；`click`、`focus`、`send-keys`、截图等可能占用桌面或改变焦点的命令不属于默认路线。上游 v0.8.0 有 Windows ARM64 发布；仍须按实际安装版本与命令帮助判断能力。

本机用 v0.8.0 实测读取已打开的窗口时，前台和鼠标保持原位。最小化 WPF 样例的 `set-value` 写入和读回均成功，但窗口成为前台。当前证据支持后台读取。严格后台写入要按目标应用和动作另行验收。

## FlaUI-MCP：桌面 UIA 后备

上游：[FlaUI-MCP](https://github.com/shanselman/FlaUI-MCP)

支持 ARM64 的 Windows 发布包名称会随版本变化；安装前检查官方 Releases 中的 `win-arm64` 自包含包及版本说明。已连接时优先 `windows_list_windows` / `windows_snapshot`，用元素 ref 定位，再选 `windows_get_text`、`windows_fill` 或已确认支持的语义动作，并读回结果。`windows_click` 可能移动指针；`windows_focus` 会将窗口置前；`windows_type`、`windows_send_keys` 可能依赖实际焦点。工具名和具体参数以连接 MCP 的 schema 为准。

经授权启用时，在宿主 MCP 配置中将 `flaui` 的 `command` 指向已解压的 `FlaUI-MCP.exe` 绝对路径，用标准输入输出连接。

## Windows-MCP：窄范围后备

上游：[CursorTouch/Windows-MCP README](https://github.com/CursorTouch/Windows-MCP/blob/main/README.md) 与 [pyproject.toml](https://github.com/CursorTouch/Windows-MCP/blob/main/pyproject.toml)

上游 0.9.0 声明 Python `>=3.14`。在这台 ARM64 机器按 Python 3.14 的原生目标做预编译包解析时，依赖 `dxcam>=0.3.0` 缺少匹配的 ARM64 wheel。需要另行验证兼容环境才启用。上游默认开放所有工具，接入时用 `--tools` 指定本次需要的能力。只读等待示例：

```json
{
  "mcpServers": {
    "windows-mcp": {
      "command": "uvx",
      "args": ["--python", "3.14", "windows-mcp==0.9.0", "serve", "--tools", "WaitFor"]
    }
  }
}
```

`Snapshot` 返回整桌面状态，可能包含其他窗口、私密标签页的可访问文本；`use_vision=true` 还会附带截图。默认不要调用 `Screenshot`、`Snapshot`，也不要启用截图视觉。只有工具 schema 确认可把采集范围严格限制到用户指定目标、且本次任务确需结构观察时才考虑 `Snapshot`，并显式关闭视觉；若无法限制，就改用 WinApp/FlaUI 的目标窗口 UIA 查询。禁止为了取巧启用通用桌面键鼠、PowerShell、文件系统、注册表等工具。直接输入和切焦点操作可能要求桌面控制权并干扰用户；不要启用或绕过控制门。

## Ghost：受限末位备选

上游：[NORTHTEKDevs/ghost](https://github.com/NORTHTEKDevs/ghost)

官方发布页：[Ghost Releases](https://github.com/NORTHTEKDevs/ghost/releases)

0.23.4 发布物包括 `ghost-windows-x64.zip`。在兼容环境并获授权后，可将 `ghost` MCP 的 `command` 指向已解压的 `ghost-mcp.exe`，保留默认后台策略。此包为 x64，本机 ARM64 优先使用两套 CLI。目标操作适用时，检查实际返回的 `verified`、`focus_preserved` 和 `cursor_preserved`；严格保持前台的任务只使用经验证不会激活目标的动作。

## 按动作持久化的本机适配库（`adapter_registry.py`）与静默启动

本机适配与授权数据独立存放在 `%AUTO_COMPUTER_HOME%`（默认 `%LOCALAPPDATA%\AutoComputer`），与 Skill 文件解耦，升级覆盖不丢失：

```powershell
$pythonExe = Join-Path $env:LOCALAPPDATA 'AutoComputer\.venv\Scripts\python.exe'
# 被动枚举可见非微信窗口
& $pythonExe .\scripts\adapter_registry.py discover
# 列出当前机器已持久化的全部 (app, action) 绑定
& $pythonExe .\scripts\adapter_registry.py list
# 读取单个动作绑定
& $pythonExe .\scripts\adapter_registry.py get sample-app pause
# 实测读回验证通过后记录动作绑定（禁止传入微信/Weixin）
& $pythonExe .\scripts\adapter_registry.py record sample-app pause uia '{"name":"Pause","control_type":"button"}' --verification tested_background --evidence "background invoke toggled status"
```

- 经核验的静默桌面启动配置保存在 `%AUTO_COMPUTER_HOME%\adapters\launch\<app_id>.json`（由 `desktop_policy.py` 校验精确可执行路径、文件版本与后台启动参数，通过 `windows-uia` 的 `launch_registered_app` 以 `SW_SHOWMINNOACTIVE` 启动）。
- **微信（WeChat/Weixin）硬隔离**：`desktop_policy.py`、`adapter_registry.py`、`desktop_mcp.py`、`uia_background.py`、`msaa_background.py`、`win_background.py`、`window_ocr.py` 与 `win_uia.ps1` 均会直接拒绝微信进程及其所有派生子进程。

## 自助授权、多 Agent MCP 与安全覆盖升级

- **首次自检与浏览器配对**：仓库与安装目录均提供 `.\setup-auth.ps1` 和双击入口 `.\pair-browser.cmd`（支持 `--check`、`--json`、`--pair chrome|edge`）。DPAPI 令牌保存在 `%AUTO_COMPUTER_HOME%\secrets`。
- **Codex CLI MCP 一键注册**：运行 `.\install.ps1 -Target codex`（或默认 `-Target all`）自动通过 `codex mcp add` 注册 `edge-devtools` 与 `windows-uia` 并同步 `~/.codex/skills/auto-computer`。其他 MCP 客户端可直接导入 `%AUTO_COMPUTER_HOME%\mcp-config.json`（参见 `templates/mcp_config.example.json`）。
- **安全覆盖升级**：`.\install.ps1` 仅备份并刷新 `skills/`、`mcp/` 及入口脚本，绝不清空或覆盖 `%AUTO_COMPUTER_HOME%\adapters` 与 `%AUTO_COMPUTER_HOME%\secrets`。
