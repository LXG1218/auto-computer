# 工具命令参考

## 本机工具路径

```powershell
$acHome = if ($env:AUTO_COMPUTER_HOME) { $env:AUTO_COMPUTER_HOME } else { Join-Path $env:LOCALAPPDATA 'AutoComputer' }
$py = 'D:\Codex\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = Join-Path $acHome '.venv\Scripts\python.exe' }
$cliEntry = Join-Path $acHome 'tools\playwright\node_modules\@playwright\cli\playwright-cli.js'
$winappExe = Join-Path $acHome 'tools\winapp\winapp.exe'
$agentBrowserExe = Join-Path $acHome 'tools\agent-browser\agent-browser.exe'
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

### 2) 日常浏览器（Playwright Extension + DPAPI 令牌 + 结束自动消横幅）

```powershell
# 查看运行中的 Chrome / Edge 与已有授权会话
& $py .\scripts\browser_session.py status --browser chrome --profile Default
& $py .\scripts\browser_session.py status --browser edge --profile Default
# 后台重连（内置 NODE_OPTIONS=--dns-result-order=ipv4first）
& $py .\scripts\browser_session.py reconnect --browser chrome --profile Default
# 任务结束后立即执行 detach，当场消除扩展调试横幅（无需重启浏览器）
& $py .\scripts\browser_session.py detach --browser chrome --profile Default
```

- 隐藏标签页纯 CSS `:hover` 菜单探测：`node .\scripts\edge_css_menu.cjs --cli $cliEntry --session <session> --request-file <req.json>`。

### 3) 无头高速通道（`agent-browser` / `browser_headless.py`）

```powershell
& $py .\scripts\browser_headless.py headless-fetch --browser edge --url "https://example.com" --selector "h1,a"
```
- **PowerShell 5.1 传 JSON 给 `agent-browser batch`**：禁止管道传字符串（会产生 BOM 报错），必须写无 BOM UTF-8 文件再用 `cmd.exe /d /c "... batch --bail --json < file.json"`。

## 域 3：Windows 桌面（UIA → MSAA → Win32 → OCR → 借前台）

1. **`windows-uia` MCP（`mcp/desktop_mcp.py`）**：
   - `list_windows` → `inspect_window(hwnd, max_bytes=12000, delta_base="t1")`（二次查看传上次返回的 `tree_id` 只回传增量）→ `run_actions`（强制 `AutoSetFocus=false`）。
   - **短暂借前台**：`foreground_desktop`（传 `foreground=true`、`expected_pid`、`expected_bounds`、`expected_foreground`）。`click`/`drag` 自动先检 `POINT_COVERED` 遮挡；所有输入动作自动触发 `foreground_guard.py` 屏幕边缘光晕、顶部窄提示条与 `Esc` 急停，动作完即收起。
   - **静默启动**：`launch_registered_app(id)` 仅启动 `%AUTO_COMPUTER_HOME%\adapters\launch\<id>.json` 已核验应用（`SW_SHOWMINNOACTIVE`）。
2. **后台脚本与适配器沉淀**：

```powershell
& $py .\scripts\desktop_state.py
& $py .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 inspect
& $py .\scripts\msaa_background.py --hwnd 0x12345 --pid 1234 --name '搜索'
& $py .\scripts\win_background.py set-native-edit --hwnd 0x12345 --control-hwnd 0x12346 --pid 1234 --text '内容'
& $py .\scripts\window_ocr.py --hwnd 0x12345 --pid 1234 --keyword '搜索' --language-tag zh-Hans-CN
& $py .\scripts\adapter_registry.py record app-id action uia '{"name":"Save","control_type":"button"}' --verification tested_background --evidence "readback ok"
```

## 升级与本机私有数据保护

- 通过 `.\install.ps1` 升级时，完整保留 `%AUTO_COMPUTER_HOME%\adapters`、`secrets`、`profiles` 与本机私有 `local-rules.md`。
- 本机私密分流规则写于 `%AUTO_COMPUTER_HOME%\local-rules.md`，严禁提交到公开仓库。
