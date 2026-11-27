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
  - `open_and_run` 已通过 `read` 或 `read_list` 返回所需数据时默认跳过正文摘要；后台无头（`visible=false`、`close_after=true`）抓完无残余页面时自动退出浏览器并置 `browser_closed=true`（无需再调 `stop_auto_browser`）；任何 `visible=true` 可见窗永不自动回收。

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
   - 需要找控件时，先按进程 `list_windows`（默认已过滤输入法/0×0 噪声窗；必要时传 `include_hidden:true`），再用控件名或 ID 定向 `inspect_window`。重复查看同一窗口、同一筛选时可传上次完整结果的 `tree_id` 作 `delta_base`。后台执行仍强制 `AutoSetFocus=false`（空闲 10 分钟自动回收守护进程）。
   - **短暂借前台**：`foreground_desktop`（传 `foreground=true`、`expected_pid`、`expected_bounds`、`expected_foreground`）。`click`/`drag` 自动先检 `POINT_COVERED` 遮挡；所有输入动作自动触发 `foreground_guard.py` 屏幕边缘光晕、顶部窄提示条与 `Esc` 急停，动作完即收起。
   - **静默启动**：`launch_registered_app(id)` 仅启动 `%AUTO_COMPUTER_HOME%\adapters\launch\<id>.json` 已核验应用（`SW_SHOWMINNOACTIVE`）。
   - **自绘无控件桌面应用（如微信 4.x `Weixin`、QQ/钉钉/飞书/千牛等）**：
     - 碰到桌面应用先查 `adapter_registry.py profile-get <AppName>`；已知控件树为空（如微信 4.x 进程 `Weixin` / 窗类 `Qt51514QWindowIcon`）**直接跳过 `inspect_window` 找子控件**，且勿在无桌面会话的 shell 中调 `GetWindowRect`。
     - **单次前台发消息（首推，1 次调用省 `list_windows` + 1 次极光）**：`run_actions(process="Weixin", allow_foreground=true, actions=[{"op":"set_value","automationId":"__fg_search_and_send__","name":"<联系人>","value":"<消息>"}])`（强核验 `headerMatched=true` 后才输入，未匹配或重名中止；内置 2x 放大 OCR + 像素差分抗换字体核验、动态分界与 `Enter`/按钮发送）。也可传 `hwnd` 或分步调 `__fg_search_open__` 与 `__fg_send_message__`。
     - **通用自绘 OCR 原语**：`__fg_ocr__`（只读 `op="read"` 后台抓屏）、`__fg_click_text__`（`op="invoke", name="<文案>"`）、`__fg_type_verify__`（`op="set_value", value="<文本>", name="<锚点>", submit=true|false`）。
2. **后台脚本与三层记忆沉淀**：

```powershell
& $py .\scripts\desktop_state.py
& $py .\scripts\uia_background.py --hwnd 0x12345 --pid 1234 inspect
& $py .\scripts\msaa_background.py --hwnd 0x12345 --pid 1234 --name '搜索'
& $py .\scripts\win_background.py set-native-edit --hwnd 0x12345 --control-hwnd 0x12346 --pid 1234 --text '内容'
& $py .\scripts\window_ocr.py --hwnd 0x12345 --pid 1234 --keyword '搜索' --language-tag zh-Hans-CN
& $py .\scripts\adapter_registry.py profile-record Weixin app '{"process":"Weixin","main_class":"Qt51514QWindowIcon","ui_kind":"self_drawn_ocr","preferred_route":"__fg_search_and_send__"}' --evidence "verified"
& $py .\scripts\adapter_registry.py record Example apply uia '{"name":"Apply","control_type":"button"}' --verification tested_background --evidence "readback ok"
```

## 升级与三层记忆系统约定

- 通过 `.\install.ps1` 升级时，完整保留 `%AUTO_COMPUTER_HOME%\adapters`（含 `profiles/` 画像卡）、`secrets`、`profiles` 与本机私有 `local-rules.md`。
- 本机私密分流规则写于 `%AUTO_COMPUTER_HOME%\local-rules.md`，严禁提交到公开仓库。
- **记忆防膨胀门槛**：只有“踩坑超过 1 轮试错 / 进程名或控件反直觉 / 高频复用”的稳定事实才写入 `%AUTO_COMPUTER_HOME%\adapters\profiles\`；软件改版失效时原地覆盖，不追加流水账。
