---
name: auto-computer
description: >-
  Background-first Windows automation for files, spreadsheets, image layouts,
  Edge/Chrome web workflows (named pipes + Playwright Extension), and desktop UIA/OCR.
---

# Auto Computer

优先后台完成并读回核验。每任务读一次已有 `%AUTO_COMPUTER_HOME%\local-rules.md`。参数明确直接调。需要软件技巧时按需查下方参考，本机规则和已验证适配优先。`.\setup-auth.ps1 --check` 仅用于排障（安装末尾已自动校验）。

| 域 | 首选路径 | 要点 |
| --- | --- | --- |
| 文件/表格/图像 | `run_task.py`、`table_ops.py`、`edit_saved.py`、`image_layout.py`（多步 `batch`） | 写前备份、写后核对；未知表头行用 `--header-auto`（或 `--header-row N`）。 |
| 匿名网页 | `edge-devtools.open_and_run`（`profile="auto"`） | 多页用 `urls`/`pages` 复用单浏览器；定向排查用 `inspect_tab` 过滤；无头抓完自动关闭。 |
| 日常登录网页 | `edge-devtools.daily_browser_run`（可选 `fallback_to_kept=true`） | 核对精确 URL 并自动断开；未开标签按需直降 `kept`。 |
| Windows 桌面 | 画像卡 `adapters/profiles` → 后台 UIA（`binding_app`+`binding`）→ OCR/借前台 | `run_actions` 支持直传 `process` 省去 `list_windows`；自绘无控件（如 `Weixin`）直走单次前台串跑。 |
| 跨软件 | 先落盘读回再传下一域 | 状态不明先只读核查。 |

## 边界

- 禁止日常浏览器用原生调试端口、`--autoConnect`、`--enable-automation` 或绕弹窗；首次配对用 `.\pair-browser.cmd`。
- 禁止抢回用户切走的前台；删除无关文件/发送/发布/下单前核对目标；踩坑>1轮、反直觉结构或高频复用才沉淀到 `adapter_registry.py`，过时原地覆盖。

参考：[文件表格](./references/tool-routes.md#域-1文件表格与图像) · [网页](./references/tool-routes.md#域-2网页自动化) · [桌面](./references/tool-routes.md#域-3windows-桌面uia--msaa--win32--ocr--借前台)
