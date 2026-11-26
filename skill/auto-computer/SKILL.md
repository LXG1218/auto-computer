---
name: auto-computer
description: >-
  Background-first Windows automation for files, spreadsheets, image layouts,
  Edge/Chrome web workflows (named pipes + Playwright Extension), and desktop UIA/OCR.
---

# Auto Computer

优先后台完成并读回核验。每任务读一次已有 `%AUTO_COMPUTER_HOME%\local-rules.md`。参数明确直接调，需示例再查下方参考；`.\setup-auth.ps1 --check` 仅用于安装验收与排障。

| 域 | 首选路径 | 要点 |
| --- | --- | --- |
| 文件/表格/图像 | `run_task.py`、`table_ops.py`、`edit_saved.py`、`image_layout.py`（多步 `batch`） | 写前备份、写后核对；非首行表头传 `--header-row N`。 |
| 匿名网页 | `edge-devtools.open_and_run`（`profile="auto"`）或 `agent-browser` | 带 `read`/`read_list` 自动省摘要；需正文传 `read_summary=true`。 |
| 日常登录网页 | `edge-devtools.daily_browser_run`（传 `browser`/`profile`/`url`/`actions`） | 核对精确 URL 并自动断开；未打开时按需用 `kept` 或报告。 |
| Windows 桌面 | 原生接口 → `windows-uia.run_actions`（`binding_app`+`binding`）→ 定向 UIA → MSAA/Win32 → OCR | 绑定失效仅定向重查；借前台时屏幕亮极光且按 `Esc` 急停。 |
| 跨软件 | 先落盘读回再传下一域 | 状态不明先只读核查。 |

## 边界

- 禁止日常浏览器用原生调试端口、`--autoConnect`、`--enable-automation` 或绕弹窗；首次配对用 `.\pair-browser.cmd`。
- 禁止抢回用户切走的前台；删除无关文件/发送/发布/下单前核对目标；新验证绑定用 `adapter_registry.py record` 持久化。
- PowerShell 5.1 给 `agent-browser batch` 传 JSON 须写无 BOM 文件重定向。

参考：[文件表格](./references/tool-routes.md#域-1文件表格与图像) · [网页](./references/tool-routes.md#域-2网页自动化) · [桌面](./references/tool-routes.md#域-3windows-桌面uia--msaa--win32--ocr--借前台)
