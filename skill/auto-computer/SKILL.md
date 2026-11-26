---
name: auto-computer
description: >-
  Use when asked for auto computer, auto-computer, or background Windows automation
  for files, spreadsheets, product-image layouts,
  existing Edge/Chrome sessions, and Windows desktop apps. Selects an installed tool
  for each task and verifies the resulting state. Suitable for background-first
  work on Windows ARM64 while preserving the user's browser and desktop state.
---

# Auto Computer

从一个目标出发，在授权范围内衔接文件、表格、现有网页和已打开的桌面软件。能识别的常规任务一次交给脚本完成匹配、执行和读回。Antigravity 根据实际状态处理模糊项、错误和新应用适配。规则和辅助脚本由这一套 Skill 提供。

## 快速检查

- 首次使用或迁移环境前，先运行 `.\setup-auth.ps1 --check`（或 `& $pythonExe .\scripts\setup_auth.py --check`）核对 Python 依赖、MCP 入口与浏览器首次授权状态；需要完成浏览器首次配对时直接双击 `.\pair-browser.cmd` 或运行 `.\setup-auth.ps1 --pair chrome`（保存后的 DPAPI 令牌位于 `%AUTO_COMPUTER_HOME%\secrets`，升级覆盖不丢失）。
- 明确用户指定的文件、Edge/Chrome 标签页、应用窗口和期望结果；检查目标是否唯一、窗口是否已打开、已有登录态是否要复用。
- 只检查与任务有关的工具。先看本会话提供的 MCP、`Get-Command` 和[本机候选工具路径](./references/tool-routes.md#本机候选工具路径)，再用版本或帮助确认功能。
- 对网页或窗口先读取结构并定位语义控件；不要用坐标猜测控件。工具能力、目标窗口和页面状态对不上时，停止该路由并按下表选择下一项。
- 执行前记录影响验收和恢复的状态，例如控件值、文件版本、当前标签页。后台操作使用能监测目标激活的后端，并用 `scripts/desktop_state.py` 比较前台窗口、系统鼠标和剪贴板序号。序号用于观察变化，脚本只读取序号。

## 总路由决策表（4 大域一一对应）

| 域与场景 | 首选唯一路径 | 备选 / 多步与降级规则 |
| --- | --- | --- |
| **域 1｜本地文件 / 表格 / 图像（单步）** | `scripts/run_task.py`（`file-find`/`file-move`/`file-undo`/`table-reconcile`）、`scripts/table_ops.py`、`scripts/edit_saved.py`、`scripts/image_layout.py` | 修改已保存文件先验版本、留备份与审计路径，写回后读实际内容；表格带前置空行/标题行传 `--header-row N`。 |
| **域 1｜本地文件 / 表格 / 编辑（≥2 步）** | **一律用 `scripts/run_task.py batch --steps ...`** | 单次 Python 进程闭环执行，支持 `"$prev.*"` / `"$step[i].*"` 引用与中途停错保留已完成步骤。 |
| **域 2｜网页 · 任务需要账号或指定日常浏览器** | 复用已获授权的同一浏览器和日常 Profile。Chrome 优先复用已授权的 Playwright 扩展或 `chrome-devtools` 连接，断线后按登录态工具规则重连。Edge 使用已连接的 Playwright 扩展或 `edge-devtools` `profile="daily"`。 | 只读确认目标页、账号与任务状态；连接失败时继续排查同一会话。 |
| **域 2｜网页 · 匿名可完成的公开只读任务** | 未指定日常浏览器时，用 `edge-devtools` `open_and_run(profile="auto")` 或 `agent-browser` / `browser_headless.py headless-fetch`。 | 需要账号、用户指定现有窗口，或登录要求尚无法确认时，按下方登录态规则选择。 |
| **域 2｜网页 · 用户要求匿名隔离** | 使用独立 `profile="auto"` 或无头 CLI。 | 仅做匿名任务。保持后台时使用无头路径。 |
| **域 3｜Windows 原生桌面软件（按序降级）** | ① 应用原生 CLI / API 或已验收适配器 → ② `winapp ui inspect/search` + 关闭 `AutoSetFocus` 的 `scripts/uia_background.py`（≥2 步用 `batch`） | UIA 缺控件时 → ③ `scripts/msaa_background.py` + `scripts/win_background.py` → ④ `scripts/window_ocr.py`（WinRT 内存 OCR，可用 `--region` 裁剪）。已连接 FlaUI-MCP / Windows-MCP / Ghost 仅限已验收后台能力。 |
| **域 4｜跨软件衔接** | 上一步落盘并读回核验后，再经文件 / 结构化数据 / 应用接口写入下一域 | 禁止不经读回核验直接串联跨软件写入。 |

按任务选择实际可用的工具。现有路线不足时，可以在授权的专用目录试用已核对源码和许可证的开源工具，暂定依赖先在可逆样例上验证。涉及系统设置、现有软件配置或超出授权的变化时，先说明具体影响并取得同意。

## 浏览器登录态与工具选择

在首次启动浏览器或调用网页工具前，按目标动作判断是否依赖账号，再选择工具。

1. **所有登录态任务使用本机日常桌面浏览器，在后台打开目标和操作。** 发送消息、继续个人会话、发帖、提交、修改账号内数据，以及读取私有页面、后台或余额均走这条路线。“打开 Chrome”“用我开的 Edge”“在这个标签页里”等要求同样沿用对应日常会话。用户指定 Chrome 就只在 Chrome 内找目标 Profile 和标签页。复用已获授权的后台连接，并保留当前前台窗口、活动标签和系统鼠标。独立、游客和无头会话不承接登录态任务。
2. **优先扫描已有会话，打开目标不抢前台，且不显示自动化横幅。** 先运行 `scripts/browser_session.py status --browser <chrome|edge> --profile Default` 读取 `open_browser_sessions`（发现该浏览器已有已授权的自定义命名会话如 `auto-computer-edge-qa-v4`，避免漏掉已有连接而误触发会弹 Allow 弹窗的 Chrome `--autoConnect`）。已有标签页直接查找和复用；Chrome DevTools MCP 开新页传 `new_page(background=true)`，选已有页传 `select_page(bringToFront=false)`。**所有后台浏览器路线**（`edge-devtools` `profile="auto"`/`"daily"` 与 Playwright Extension）均不使用 `--enable-automation`，不显示“自动测试软件正在控制浏览器”横幅。断线后有扩展令牌且浏览器支持后台连接时再后台重连；缺少授权或连接条件时暂停网页写入并报告缺失条件，绝不自动点击远程调试“允许”。
3. **匿名公开只读可走无头。** 确认公开搜索、文档读取等无需账号，且用户未指定日常浏览器时，才使用 `headless-fetch`、`agent-browser` 或 `open_and_run(profile="auto")`。登录要求不明时，先从目标网址、动作和授权页面做只读识别。遇到登录跳转、会话过期、账号不匹配或工具故障，继续核对同一日常浏览器、Profile、目标标签页及连接许可。口令和 Cookie 留在原应用。
4. **发送前后核对。** “打开 Chrome 给 Gemini 发消息”应在获授权的日常 Chrome 后台定位 Gemini，核对登录账号与指定对话，再按用户指令发送。读回消息内容和发送状态，结果不明时先查会话，防止重复发送。发现前台窗口或活动标签被改变时，暂停后续写入并核对现状。

## 硬边界与互斥禁令

- **授权、横幅与微信风控硬边界**：**严禁用脚本强点或绕过浏览器 Allow 远程调试授权弹窗**，且禁止对用户日常浏览器使用带 `--enable-automation` 横幅的自动化控制。**微信（WeChat/Weixin）及其所有子进程窗口全面禁止接入**——严禁扫描、读取、OCR、启动或自动化操作任何微信窗口（由 `desktop_policy.py`、`adapter_registry.py`、`desktop_mcp.py` 及 UIA/MSAA/OCR 脚本共同拦截），防范账号风控。
- **登录态隔离与管道互斥**：未沉淀登录态的站点严禁硬跑无头盲抓，遇到登录/滑块拦截立即停下交由用户处理；不用 `chrome-devtools` 替代 Edge 后台批量；Playwright 扩展与 `edge-devtools` 不同时写同一标签页；**禁止**用 PowerShell 原生管道给 `agent-browser.exe batch` 传 JSON。
- **桌面后台护城河**：Windows 桌面后台写入**严禁**绕过 `uia_background.py` 的 `AutoSetFocus=false` 校验、严格布尔校验、前台窗口门禁与中途断点保留；`msaa_background.py`、`win_background.py`、`window_ocr.py`、`desktop_state.py` 维持原有独立核验边界。
- **人工与授权门禁**：登录、短信/二次验证、权限弹窗由用户在实际浏览器中完成；删除、覆盖无关文件、发送、发布、下单、提交缺少明确授权时一律停在执行前。口令和会话凭据留在原应用内。

## 桌面后台操作与「短暂借前台、自动切回后台」规则

1. **后台启动与目标核对**：禁止直接用前台键鼠双击桌面启动软件。仅当目标已在 `%AUTO_COMPUTER_HOME%\adapters\launch` 登记了经核验的静默参数或无激活入口时，通过 `windows-uia` 的 `launch_registered_app`（`SW_SHOWMINNOACTIVE`，任务栏显示可接受、不抢前台）启动；启动后核对真实 HWND、PID、进程名与当前窗口。
2. **三段式执行与切回规则（默认后台 → 单障碍短暂借前台 → 障碍解除立刻还回后台）**：
   - **每步默认走后台**：页面加载、上传、下载期间释放并保持后台；能用 `adapter_registry.py` 已持久化绑定、UIA（`AutoSetFocus=false`）、MSAA、Win32 原生控件或应用/媒体接口完成的，一律走后台并读回校验。
   - **前台只处理眼前单个障碍**：仅当整项任务已获用户“允许临时用前台”授权，且确认当前控件无可用后台接口（如自绘菜单/画布）或后台动作未生效时，才借用一次前台处理**这一个**具体障碍；**绝不**借前台去点远程调试“允许”或顺手接管整条流程。
   - **障碍一解除立即切回后台**：自绘菜单关闭或该障碍动作完成后，立刻停止发送键鼠输入、从后台读回结果并检查下一步能否恢复后台定位与执行；若借用期间用户已手动切去其他窗口，尊重用户当前窗口，不得把旧窗口抢回前台。
3. **无控件软件按“动作”扫描补齐与持久化**：只扫描当次任务所需动作，按 `UIA → MSAA → 原生 Win32 / 系统与应用接口（可参考 CLI-Anything）→ OCR（仅定位，无控件时不伪造后台执行）` 顺序探测；一旦发现且实测通过某个后台动作，调用 `scripts/adapter_registry.py record` 按 `(app, action)` 持久化到独立本机目录 `%AUTO_COMPUTER_HOME%\adapters`。

普通后台任务保持原前台。用户切到目标窗口时暂停该窗口的写入，继续其他独立步骤。用户切走后重新核对实际内容，再从第一个未完成的动作恢复。需要在已置前的窗口演示语义控件时，仍使用原有 `foreground-demo`。需要截图后模拟真实键鼠时，按下方前台操作模式执行。最小化 WPF 样例曾由 WinApp CLI `set-value` 激活，因此要核对动作后的真实前台状态。

已连接的 FlaUI-MCP 仅选已验证不占用前台的控件操作。Windows-MCP 优先用于可限定范围的等待或取证。Ghost 仅在当前目标动作已验收且保持严格后台条件时参与执行。各工具的能力和启动条件见 [工具命令参考](./references/tool-routes.md)。

检测到目标自行激活或前台、鼠标、剪贴板状态变化，先暂停后续写入，读回已执行动作并说明能确认的变化。完整后台验收须拿到有效的前台句柄和动作前后的状态证据。观测缺失时继续可逆的测试和只读核对，外发或提交留到可以验收时执行。

## 桌面截图和前台操作

后台路线遇到自绘界面、未暴露语义控件的菜单或要求真实键鼠的画布时，用户允许前台操作后，使用 `windows-uia` 的 `foreground_desktop` 截取指定窗口并执行动作。Codex 中未连接该 MCP 时，可用本机 `desktop_mcp.py` 的同一 stdio 工具。工具调用与动作见[工具命令参考](./references/tool-routes.md#桌面截图和前台操作)。

- 调用须显式传 `foreground: true`，并核对目标 HWND、PID 和最近一次观察取得的窗口边界。子对话框弹出后重新列出相关窗口并绑定新目标。系统输入会短暂占用鼠标或键盘。
- 先核对目标截图和控件，检查窗口移动、缩放与遮挡。按最新观察执行一次动作，随后读回应用状态或重新取图。窗口、焦点或画面改变后，从新观察定位动作。输入成功仅表示工具发出了输入，任务结果以应用读回为准。
- 旧脚本内依赖真实键鼠的应用专用操作也必须从显式前台入口调用。常规后台批处理继续校验 AutoSetFocus、前台句柄和读回结果。

## 执行与故障处理

1. 先做只读观察，确认目标、控件和前置状态。独立操作可并行，同一文件、标签页或窗口上的写入和读回按顺序完成。
2. 用户要求修改已有文件且未另说保存方式时，写回指定源文件。写入前核对修改前的版本和可恢复副本。新建或另存按用户指定的路径和格式执行。删除、覆盖其他文件、外发、下单、发布等动作须有明确授权。
3. 立刻读回页面、控件或文件结果并断言用户要求的状态。记录实际改了哪些文件、页、单元格或字段及其改前改后值。跨软件时先核对上一步数据，再通过结构化数据、文件或应用接口写入下一处。
4. 失败后先读错误与当前状态。控件歧义时缩小范围，页面未就绪时等待预期条件，工具缺动作时寻找等效控件或后端。只要有新证据就继续排障，相同失败反复出现时换路线。遵守用户明确给出的任务时限。
5. 在授权的工作区可以自己编写或修复本机适配。先用可逆动作测试，再用于真实目标。遇到真实登录、二次验证或缺少授权时，只暂停依赖该信息的步骤。
6. 卡住时保留已核对成果，记录最后通过的步骤、实际证据和待处理项。可以确认出错内容由本次操作写入且未被其他来源修改时，只恢复出错的一项。结果或修改来源不明时暂停这一项并列出差异及备份。恢复任务前重读当前状态。发送、发布或提交结果未确认时，先查实际结果，避免重复执行。
7. 安全恢复自己临时改变的筛选或窗口显示状态，再核对结果。简要记录所选工具、切换原因、耗时和读回结果。

## 登记应用适配与升级保数据

- 针对具体软件动作发现且实测通过的后台控件选择器，**统一写入独立数据目录 `%AUTO_COMPUTER_HOME%\adapters`**（`scripts/adapter_registry.py`），**不要把本机私有控件绑写死进 Git 跟踪目录**，只把可公开复用的通用方法规则更新到 [工具命令参考](./references/tool-routes.md)。
- 覆盖升级 `auto-computer`（通过 `git pull` + `.\install.ps1`）时，**必须保留**用户本机的 `%AUTO_COMPUTER_HOME%\adapters`（所有软件动作适配记录）与 `%AUTO_COMPUTER_HOME%\secrets`（DPAPI 扩展令牌）。
- 应用私有数据、具体联系人、消息内容、口令和会话凭据保留在当次任务范围内；跨 Agent（Antigravity、Codex CLI 及通用 stdio MCP 客户端）统一通过 `install.ps1` 注册或读取 `%AUTO_COMPUTER_HOME%\mcp-config.json`。

## 本地脚本

- 常规文件查找、移动、恢复、表格对账及单进程多步批处理使用 `scripts/run_task.py`（支持 `batch`）。
- UTF-8 文本和 CSV 定向编辑与恢复使用 `scripts/edit_saved.py`。
- 文件和表格底层功能由 `scripts/files_fast.py`、`scripts/table_ops.py` 提供。
- 参照图排版使用 `scripts/image_layout.py`。
- 无头高速批处理、公开页面抓取与日常浏览器拉起使用 `scripts/browser_headless.py`。

Python 路径从当前环境已配置的解释器中确认。

## 工具命令参考

连接参数、工具试用范围与官方文档链接见 [工具命令参考](./references/tool-routes.md)。日常执行使用已完成相应验收的工具。

