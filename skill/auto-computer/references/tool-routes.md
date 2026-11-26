# 工具命令参考

## 本机工具路径

```powershell
$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { Join-Path $env:LOCALAPPDATA 'AutoComputer' }
$runtime = Get-Content -Raw -Encoding utf8 -LiteralPath (Join-Path $acHome 'runtime.json') | ConvertFrom-Json
$py = if ($env:AUTO_COMPUTER_PYTHON) { $env:AUTO_COMPUTER_PYTHON } else { $runtime.python }
$toolsDir = if ($env:AUTO_COMPUTER_TOOLS_DIR) { $env:AUTO_COMPUTER_TOOLS_DIR } else { $runtime.tools }
$cliEntry = Join-Path $toolsDir 'playwright\node_modules\@playwright\cli\playwright-cli.js'
```

## 域 1：文件、表格与图像

```powershell
& $py .\scripts\run_task.py file-find --root 'D:\Source' --glob '*.xlsx' --recursive
& $py .\scripts\run_task.py table-reconcile --left 'D:\A.csv' --right 'D:\B.xlsx' --header-row 5 --key '商品编号' --columns '价格,库存'
& $py .\scripts\edit_saved.py csv --file 'D:\Orders.csv' --backup-root "$acHome\backups" --key-column '编号' --key '2609' --column '状态' --before '待核对' --after '已核对'
& $py .\scripts\run_task.py batch --steps '[{"op":"edit-csv","file":"D:\\Orders.csv","backup_root":"backups","key_column":"编号","key":"2609","column":"状态","before":"待核对","after":"已核对"},{"op":"read-text","file":"D:\\Orders.csv","contains":"2609"}]'
```

## 域 2：网页自动化

### 1) `edge-devtools` MCP（命名管道：`auto` 匿名 / `kept` 专用登录）
- 入口：`%AUTO_COMPUTER_HOME%\mcp\edge_mcp.cjs`（0 TCP 端口、0 Allow 弹窗、0 横幅）。
- `open_and_run` / `list_tabs` / `inspect_tab` / `open_tab` / `close_tab` / `run_actions` / `stop_auto_browser`：
  - `profile="auto"`（默认）：匿名临时/隔离任务；
  - `profile="kept"`：专用长期保留登录空间（`profiles/<browser>-kept`），适合日常浏览器未打开该站点的登录态后台任务；首次登录传 `visible: true`。
  - `open_and_run` 已通过 `read` 或 `read_list` 返回所需数据时传 `read_summary: false` 跳过正文摘要。

### 2) 日常浏览器

- 首选 `daily_browser_run`。例如 `{"browser":"edge","profile":"Default","url":"https://example.com/orders","actions":[{"op":"read","selector":"#order-count"}]}`。工具在获准会话中确认目标 URL 后才操作，并集中处理连接和断开。动作支持 `read`、`click`、`fill`、`select`、`check`、`uncheck`。有动作时默认跳过额外摘要。
- 扩展只能操作已授权给该会话的标签。若令牌重连只获得空白页，请在扩展中授权原标签，或在任务允许时使用 `kept`。要求使用当前标签页的任务遇到目标不可用时，先报告原因。
- 若返回 `needs_readback`，先核对实际状态，再决定如何继续。已完成的写操作不得重复。
- MCP 不可用时走 CLI。`reconnect` 本身会检查状态。`status` 用于排障。

```powershell
& $py .\scripts\browser_session.py reconnect --browser edge --profile Default --strict-foreground
& $py .\scripts\browser_session.py detach --browser edge --profile Default
```

- 隐藏标签页纯 CSS `:hover` 菜单探测：`node .\scripts\edge_css_menu.cjs --cli $cliEntry --session <session> --request-file <req.json>`。

### 3) 无头高速通道（`agent-browser` / `browser_headless.py`）

```powershell
& $py .\scripts\browser_headless.py headless-fetch --browser edge --url "https://example.com" --selector "h1,a"
```
- **PowerShell 5.1 传 JSON 给 `agent-browser batch`**：禁止管道传字符串（会产生 BOM 报错），必须写无 BOM UTF-8 文件再用 `cmd.exe /d /c "... batch --bail --json < file.json"`。

## 域 3：Windows 桌面（UIA → MSAA → Win32 → OCR → 借前台）

1. **`windows-uia` MCP（`mcp/desktop_mcp.py`）**：
   - 已知目标窗口和已验证绑定时，直接调用 `run_actions`。示例 `{"hwnd":"0x12345","binding_app":"Example","actions":[{"op":"invoke","binding":"apply"},{"op":"read","binding":"status","key":"name"}]}`。`binding_app` 用窗口所属进程名（省略 `.exe`），`binding` 用已登记的动作名。工具核对 PID 并要求绑定只命中一个控件。出现失败时按返回的步骤结果定向检查，再继续尚未完成的动作。
   - 需要找控件时，先按进程 `list_windows`，再用控件名或 ID 定向 `inspect_window`。重复查看同一窗口、同一筛选时可传上次完整结果的 `tree_id` 作 `delta_base`。后台执行仍强制 `AutoSetFocus=false`。
   - **短暂借前台**：`foreground_desktop`（传 `foreground=true`、`expected_pid`、`expected_bounds`、`expected_foreground`）。`click`/`drag` 自动先检 `POINT_COVERED` 遮挡；所有输入动作自动触发 `foreground_guard.py` 屏幕边缘光晕、顶部窄提示条与 `Esc` 急停，动作完即收起。
   - **静默启动**：`launch_registered_app(id)` 仅启动 `%AUTO_COMPUTER_HOME%\adapters\launch\<id>.json` 已核验应用（`SW_SHOWMINNOACTIVE`）。
2. **后台脚本与适配器沉淀**：

```powershell
& $py .\scripts\desktop_state.py
& $py .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 inspect
& $py .\scripts\msaa_background.py --hwnd 0x12345 --pid 1234 --name '搜索'
& $py .\scripts\win_background.py set-native-edit --hwnd 0x12345 --control-hwnd 0x12346 --pid 1234 --text '内容'
& $py .\scripts\window_ocr.py --hwnd 0x12345 --pid 1234 --keyword '搜索' --language-tag zh-Hans-CN
& $py .\scripts\adapter_registry.py record Example apply uia '{"name":"Apply","control_type":"button"}' --verification tested_background --evidence "readback ok"
```

## 升级与本机私有数据保护

- 通过 `.\install.ps1` 升级时，完整保留 `%AUTO_COMPUTER_HOME%\adapters`、`secrets`、`profiles` 与本机私有 `local-rules.md`。
- 本机私密分流规则写于 `%AUTO_COMPUTER_HOME%\local-rules.md`，严禁提交到公开仓库。
