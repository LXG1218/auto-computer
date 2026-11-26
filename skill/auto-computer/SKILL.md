---
name: auto-computer
description: >-
  Background-first Windows automation for files, spreadsheets, image layouts,
  Edge/Chrome web workflows (named pipes + Playwright Extension), and desktop UIA/OCR.
---

# Auto Computer

从目标出发，优先在后台完成文件、网页和桌面操作，并读回核验结果。
- **本机私有规则优先**：若 `%AUTO_COMPUTER_HOME%\local-rules.md` 存在，执行前先读取并优先遵守其中的网络分流或本机约定。
- **自检与配对**：环境核对用 `.\setup-auth.ps1 --check`；浏览器扩展首次配对用 `.\pair-browser.cmd`。

## 4 大域唯一路由

| 域与场景 | 首选唯一路径 | 补充与校验 |
| --- | --- | --- |
| **域 1｜文件 / 表格 / 图像** | 单步：`scripts/run_task.py` / `table_ops.py` / `edit_saved.py` / `image_layout.py`；≥2 步：**一律 `run_task.py batch --steps ...`** | 修改前留备份，含前置表头传 `--header-row N`，写回后读回确认。 |
| **域 2｜网页 · 匿名公开** | `edge-devtools`（`profile="auto"` 命名管道）或 `browser_headless.py` / `agent-browser` | 0 端口、0 弹窗、0 横幅，绝不碰日常浏览器。 |
| **域 2｜网页 · 需要登录态** | **① 先查日常已有**：`browser_session.py status --browser <chrome\|edge>` 查看运行中的日常浏览器，若存在目标会话/标签则 `reconnect` 后台复用，**任务结束立即 `browser_session.py detach` 当场消横幅**；<br>**② 无对应标签时**：改用 `edge-devtools`（`profile="kept"` 命名管道专用空间）。 | 严禁使用原生远程调试端口 / `--autoConnect` / `--enable-automation`。发送、提交、下单前必须核对账号与目标页。 |
| **域 3｜Windows 桌面** | ① 原生 CLI 或 `%AUTO_COMPUTER_HOME%\adapters` 已持久化绑定 → ② `windows-uia` 后台 UIA（`list_windows` / `inspect_window` 传 `delta_base` 省 token / `run_actions` 或 `uia_background.py`）→ ③ `msaa_background.py` + `win_background.py` → ④ `window_ocr.py` | 必须短暂借前台时用 `foreground_desktop`（自动查 `POINT_COVERED`、显示边缘光晕+顶部提示条并监听 `Esc` 急停，动作完立即收起）。 |
| **域 4｜跨软件衔接** | 上一步落盘并读回核验后，再经结构化数据/文件/接口写入下一域 | 禁止未读回即串联写入。 |

## 核心执行边界

1. **绝不绕过授权与弹窗**：禁止强点或绕过浏览器授权框；缺扩展令牌时仅提示用户运行 `.\pair-browser.cmd` 或改用 `profile="kept"`。
2. **后台与前台切换纪律**：桌面操作默认保持原前台并用 `desktop_state.py` 观测状态；借前台仅处理当前单个自绘/画布障碍，用户已切换窗口时不抢回。
3. **跨平台/编码陷阱**：PowerShell 5.1 严禁用管道直接给 `agent-browser batch` 传 JSON，改用无 BOM 临时文件重定向；通过 `install.ps1` 升级时保留 `%AUTO_COMPUTER_HOME%\adapters`、`secrets`、`profiles` 与 `local-rules.md`。
4. **动作沉淀与授权**：新验证通过的 `(app, action)` 后台绑定用 `adapter_registry.py record` 存入 `%AUTO_COMPUTER_HOME%\adapters`（不写入 Git 仓库）；删除、覆盖无关文件、发送、发布、下单前须有明确授权。

常用命令与工具参数见 [工具命令参考](./references/tool-routes.md)。
