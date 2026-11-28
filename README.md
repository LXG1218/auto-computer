# Auto Computer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64%20%2F%20ARM64)-0078D4.svg)](#)

**Auto Computer 让 AI 在 Windows 上帮你处理文件、操作网页和使用桌面软件。** 你照常用电脑，它能在后台做的步骤就后台做。遇到必须使用鼠标或键盘的步骤，它会短暂借用前台。后续步骤能在后台完成时，就切回后台。借用前台时，屏幕边缘会出现可点击穿透的极光特效，上方会显示接管提示，按 `Esc` 可以急停。

- **处理文件和表格** · 查找和整理文件、核对 CSV / Excel 中的价格或库存、修改已保存的文本或 CSV 并读回核验。
- **读写网页** · 批量读取公开网页、填写表单，或操作你已配对并授权的 Chrome / Edge 标签页。
- **操作桌面软件** · 识别窗口里的按钮和输入框，尝试后台点击、填写和读取结果。部分界面需要短暂借用前台。

支持 Codex、Antigravity 和兼容 stdio MCP 的客户端。你可以从一句话开始，例如「对比这两份库存表，导出有差异的商品」。

---

## 给 AI Agent 的一键安装 / 更新指令

复制下面整段发给你正在用的 Agent。它会先检查电脑上已有的环境，再按你的选择安装、接入和验收。

```text
请帮我在 Windows 上安装或更新 https://github.com/LXG1218/auto-computer 的 Auto Computer，并验证我选择的宿主可以使用它。

1. 先检查，再安装
   - 先查当前 Agent、已有 Auto Computer 安装目录与 runtime.json，以及能用的 Python 3.10+、Node.js/npm 和 Edge 或 Chrome。复用已有且兼容的工具、版本和路径。已有安装优先沿用其 HomeDir、ToolsDir 和已配好的 Python 环境。
   - 缺少 Python、Node.js/npm 等通用依赖时，优先按我已有的存储偏好放在可写的 D: 或 E: 共享工具目录（如 E:\DevTools），供其他项目复用。未给过路径偏好时，问我是否把它们统一放在 D: 或 E: 的某个目录。没有合适的数据盘时，给出当前用户可写的位置，由我选定。使用官方来源，核对实际可执行路径和版本，让本次安装进程能找到它们。保留其他项目已有的环境和全局设置。
   - 默认只接入当前 Agent。发现本机还有其他可接入的 Agent 时，列出已发现的宿主，问我是否一起接入。如果还需要决定共享依赖路径，把这两个问题合并问一次。我已明确选择的路径或宿主直接沿用。

2. 按选定路径和宿主安装
   - 从官方仓库取得源码。已有干净的检出就快进更新，有本地修改时先保留原检出，再用独立的干净源码目录。没有 Git 时可下载官方 Release 的源码 ZIP。
   - 项目专用文件放在 Auto Computer 自己的目录。MCP 服务放在 <HomeDir>\mcp，Skill 和 MCP 注册项放在对应宿主支持的位置。Python 包放本项目的 .venv，Playwright CLI 放 <ToolsDir>\playwright。首次安装可用 -HomeDir 指定项目目录，默认是 %LOCALAPPDATA%\AutoComputer（已有 AUTO_COMPUTER_HOME 时优先使用）。-ToolsDir 可单独指定，默认是 HomeDir\tools。即使项目放在数据盘，宿主的 Skill 和 MCP 配置也按该宿主的目录规则写入。
   - 用 install.ps1 的 -Target codex、-Target antigravity 或 -Target generic 接入所选客户端。Codex 自动注册需要当前 PowerShell 能找到 codex CLI。仅在我同意同时接入 Codex 和 Antigravity 时使用 -Target all。通用客户端从 <HomeDir>\mcp-config.json 导入对应的两个 stdio MCP 条目，Skill 按该宿主支持的方式安装。多个宿主复用同一个 HomeDir。
   - 新机器装好共享的基础 Python 后，让安装器在项目目录创建 .venv。已有兼容且用于本项目的 Python 环境可以用 -PythonExe 复用，确需给该环境补包时加 -InstallPythonDeps。Node.js 和 npm 需能在当前 PowerShell 中找到。需要截图、按键等前台工具时，核对 WinApp 是否可用，并说明缺失的能力。
   - 升级时沿用原 HomeDir，保留我的 local-rules.md、本机软件和站点适配（adapters，含画像卡）、浏览器令牌（secrets）和登录配置（profiles）。只合并 Auto Computer 的 edge-devtools 和 windows-uia 注册项，保留其他 MCP、用户全局规则和网络代理设置。浏览器沿用已有扩展授权，不向 chrome-devtools 加 --autoConnect。
   - 同步项目发布的 SKILL.md 和 references 中的通用软件技巧。通用技巧按需查阅，遇到冲突以我的当前指令、本机规则和已验证的本机适配为准。如果我曾直接改过这些同名文件，更新前按宿主分别备份自定义内容，并告诉我备份位置。我的本机规则和适配继续保存在 HomeDir 的持久化位置。

3. 验证并把结果告诉我
   - 看安装器末尾的环境自检，再按需重载所选宿主，确认 MCP 工具实际可见。仅在我需要操作日常已登录的 Chrome / Edge 且尚未配对时，运行对应项目目录里的 pair-browser.cmd，请我在浏览器里完成一次授权。
   - 列出实际安装或复用的 Python、Node.js、项目 HomeDir、ToolsDir、宿主 Skill 和 MCP 配置位置。升级后核对本机规则和适配仍可读取。明确哪些宿主已接入、哪些能力已验证，以及还缺什么或需要我完成哪一步。
```

## 命令行安装

先准备可用的 Python 3.10+、Node.js 和 npm。需要网页能力时准备 Edge 或 Chrome。下面以仅接入 Codex 为例，安装时需能运行 `codex`。

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1 -Target codex
```

- 安装 Antigravity 时用 `-Target antigravity`。通用客户端用 `-Target generic` 生成配置。`-Target all` 会为 Codex、Antigravity 与通用配置准备接入，Codex 自动注册仍需 CLI。
- 新安装可用 `-HomeDir 'E:\Tools\AutoComputer'` 指定项目目录。升级沿用实际安装目录，保留原有适配、令牌、登录配置和本机规则，并接收本次发布随附的通用软件技巧。
- 安装器末尾会运行环境自检，独立排障可以执行 `.\setup-auth.ps1 --check`。只有需要操作日常已登录的标签页时才需要配对扩展，已配对的浏览器可继续使用。

## 和其他项目怎么选

| 项目 | 主要适用场景 | 值得关注的能力 |
| :--- | :--- | :--- |
| **Auto Computer** | 在 Windows 上用 Codex、Antigravity 或通用 MCP 客户端做本机工作 | 文件和表格可直接批量处理，也能读写无头网页、已授权的日常浏览器标签页和桌面控件。 |
| dsh-computer-use-win | 在 Windows 上使用 DeepSeek Harness 或独立 MCP 操作桌面和浏览器 | 支持后台控件、屏幕提示和虚拟机工作方式。连接已打开的日常浏览器是实验功能，需要在浏览器里确认。 [项目说明](https://github.com/Yu-tao-Li/dsh-computer-use-win) |
| Jason Skill | 在 Codex 里做 Windows / macOS 的文件、浏览器和桌面工作 | 按任务选择文件工具、浏览器或桌面驱动。项目把 `0.2.2` 标为验证发布版，`0.3.0-draft` 是评审稿，依赖 `agent-browser` 和 `agent-desktop`。 [项目说明](https://github.com/13jiason13/jason-skill) |

---

## 各场景实现方式

Auto Computer 按场景选择开销最低、稳定性最高的通道，避免所有问题一刀切走前台视觉循环：

- **本地文件 / 表格 / 图像**：通过本地结构化批处理完成查找、对账、读写与排版；写前自动备份、写后读回核验，并支持非首行表头自动定位与指定表头行（`header_row`）修改，无需先探测表头行。
- **公开与匿名网页**：通过 Chromium 命名管道隔离通信，**0 TCP 端口暴露、0 远程调试横幅、0 Allow 授权弹窗**。支持单次 `urls`/`pages` 多页复用同一无头浏览器（单页超时会保留同批其余页面结果并标明 `page_timeout`）与 `inspect_tab` 定向过滤；纯后台无头抓取在任务完成且无残余页面时**立即自动关闭浏览器进程**，零后台内存残留并省去额外关闭轮次。
- **日常已登录网页**：通过扩展令牌安全附着用户已授权的日常浏览器标签页（兼容 Edge 睡眠/丢弃唤醒及多标签页数变动标题），核对精确 URL 后执行读取或表单动作（与专用通道 `read` 的 `text`/`count`/`attribute` 返回格式完全同构），结束瞬间自动断开并清理连接新建的 `about:blank` 标签页以消掉顶部调试横幅；若日常窗口未开对应标签，可按需一步无缝降级至独立保留登录态的 `kept` 空间。**绝不自动关闭用户日常浏览器或任何可见窗口。**
- **常规 Windows 桌面软件**：`list_windows` 改为 Win32 进程内直查（0 子进程、零 UIA 内存），UIA 守护进程仅在操作控件时按需拉起并在空闲 10 分钟自动休眠；驱动阶段强制不抢焦点，配合响应字节预算控制、增量控件树对比及按进程自动锁定唯一主窗，压低交互轮次与 Token 体积。
- **自绘 / 无控件桌面软件（如微信 4.x、QQ、钉钉、飞书、千牛等）**：绕开空控件树，在单次短暂前台会话内完成“视觉定位 → 标题强校验防误操作 → Unicode 绕输入法精准注入 → **像素差分主核验 + 2× 增强本地 OCR 容错**”；零第三方重依赖，兼容 x64 与 ARM64，不受用户更换系统字体、字号或浅色按钮对比度影响。
- **短暂借前台安全护栏**：仅在目标程序必须前台响应时介入，执行前自动校验坐标窗口遮挡；期间展示可点击穿透的屏幕边缘极光与顶部状态提示，并挂载底层物理 `Esc` 一键急停与粘键释放，动作完立即退场。
- **三层记忆** · 项目随版本发布通用技巧。用户的本机画像卡保存在各自的 HomeDir 下的 adapters\profiles，本机私有规则保存在 local-rules.md，两者随升级保留。Agent 按当前任务查阅通用技巧，只把反复踩坑、结构反直觉或高频复用的稳定经验写入本机画像。

---

## 实测性能与 Token 对比

### 1. 历史同机基准（6 项网页任务与 4 项桌面任务）

| 项目方案 | 10 场景总耗时 | 单场景平均耗时 | 工具调用总次数 | 静态常驻上下文 | 10 场景动态总 Token |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auto Computer** | **1,285.8 ms** | **128.6 ms** | **11 次** | **~1,812 tok** | **5,342 tok** |
| `dsh-computer-use-win (v0.6.4)` | 5,344.9 ms | 534.5 ms | 17 次 | ~7,796 tok | 9,619 tok |
| `jason-skill (0.3.0-draft)` | 12,968.8 ms | 1,296.9 ms | 14 次 | ~1,822 tok | 6,278 tok |

- *说明：这组 Windows 11 ARM64 本地闭环基准使用 Auto Computer v1.3.0 架构，覆盖网页提取、填表、列表、窗口定位和读回核验。Token 按字符估算，静态上下文列记录当时的工具加载配置。DSH v0.6.4 在 DeepSeek Harness 里默认按需加载工具，作为独立 MCP 运行时默认加载全部工具。* [DSH v0.6.4 发布说明](https://github.com/Yu-tao-Li/dsh-computer-use-win/releases/tag/v0.6.4)

### 2. `v1.4.0` 关键专项实测提升（vs `v1.3.0`）

| 专项场景 | 优化前 (`v1.3.0`) | 优化后 (`v1.4.0`) | 实测改善 |
| :--- | :--- | :--- | :--- |
| 桌面空闲常驻内存（单窗进程树） | 41.7 MB Private | **14.9 MB Private**（10 分钟空闲自动休眠） | **空闲内存降 64.3%** |
| 窗口枚举噪声过滤（以微信为例） | 12 窗 / ~358 tok | **5 窗 / ~166 tok** | **Token 减 53.6%** |
| 多字体 / 16px 小字 / 低对比核验（6 组实图） | 2/6（33.3%） | **6/6（100%）** | **抗换字体误杀归零** |
| 自绘单次前台发消息全链路 | 3,994.9 ms（2 轮 / 2 次前台） | **984.0 ms（1 轮 / 1 次前台）** | **提速 4.06×** |
| 非首行表头对账（500 行含前置备注） | 287.2 ms（3 轮） | **91.9 ms（1 轮）** | **提速 3.13×** |
| 高频任务 LLM 往返收敛 | 2–3 轮 | **1 轮直达**（按进程发桌面 / 无头抓完即关 / 网页无缝降级 `kept`） | **显著减少模型等待轮次** |

### 3. `v1.5.0` 关键专项实测提升（vs `v1.4.0`）

| 专项场景 | 优化前 (`v1.4.0`) | 优化后 (`v1.5.0`) | 实测改善 |
| :--- | :--- | :--- | :--- |
| 多页抓取（3 页串联） | 1,888.6 ms（3 次 MCP / ~642 tok） | **1,096.9 ms**（1 次 MCP / **~408 tok**） | **提速 1.72×（-41.9%），Token 省 36.4%** |
| 同页等待 + 多字段读取（含关闭闭环） | 1,053.2 ms（3 次 MCP / ~468 tok） | **902.2 ms**（1 次 MCP / **~313 tok**） | **提速 14.3%，少 2 轮往返，Token 省 33.1%** |
| 富 DOM 定向 `inspect_tab` | 11.4 ms（30 候选 + 2500 字 / ~2,221 tok） | **8.6 ms**（5 候选 + 0 正文 / **~242 tok**） | **响应 Token 省 89.1%（9.2×）** |
| 大列表（550 条）`count` + `read_list` | 失败（超 500 元素报错） | **891.9 ms 一次成功**（`count=550` + 5 条） | **突破 500 上限并有界输出** |
| 桌面冷启动 `list_windows` MCP | 245.1 ms（拉起 UIA 子进程） | **78.7 ms**（Win32 进程内直查，0 子进程） | **提速 3.11×（-67.9%），零 UIA 内存** |
| 日常 / 专用浏览器 `read` 三模式 | 日常通道忽略 `as:"count"` / `"attribute"` | `text` / `count` / `attribute` 返回 **100% 同构** | **消除切换通道重试与报错** |
| 静态常驻 `tools/list` Schema（双 MCP 合计） | ~1,151 tok（4,604 字符） | **~1,188 tok**（4,754 字符） | **仅增 +37 tok**（覆盖多页/过滤/强校验全参数） |

*注：以上为隔离临时 `AUTO_COMPUTER_HOME` 沙箱直连 stdio MCP 实测中位数（浏览 3 轮、桌面/文件 5 轮；Token 按精确字符数 `ASCII/4 + 非ASCII/1.5` 估算）。*

---

## 项目结构

```text
auto-computer/
├── install.ps1       # 一键完整安装与无损升级入口
├── setup-auth.ps1    # 各域环境与权限就绪自检
├── pair-browser.cmd  # Chrome / Edge 日常扩展配对入口
├── mcp/              # 桌面与命名管道浏览器 MCP 服务及借前台安全护栏
├── skill/            # Agent 路由规范、通用规则与三域执行脚本
├── templates/        # 宿主 MCP 配置模板
└── tests/            # 离线单元与回归测试集
```

## License

[MIT](./LICENSE)
