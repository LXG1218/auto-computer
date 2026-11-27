# Auto Computer

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64%20%2F%20ARM64)-0078D4.svg)](#)

**Auto Computer** 是面向 AI Coding Agent（Antigravity / Codex / 通用 MCP 客户端）的**后台优先 Windows 自动化 Skill 与轻量 MCP 工具包**。

相比传统的“整屏截图 → 猜坐标 → 抢鼠标键盘”循环，它优先通过后台通道静默执行并读回核验，兼顾极低常驻内存、低 Token 消耗与高频操作单轮直达；仅在目标软件必须交互时短暂借用前台，并全程覆盖安全护栏与一键急停。

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
   - `%LOCALAPPDATA%\AutoComputer\adapters\`（含本机已学习的软件控件适配库与 `profiles/` 应用/站点画像卡片）
   - `%LOCALAPPDATA%\AutoComputer\secrets\`（本机 DPAPI 加密的浏览器扩展令牌）
   - `%LOCALAPPDATA%\AutoComputer\profiles\`（`kept` 专用登录浏览器空间与登录态）
   - `%LOCALAPPDATA%\AutoComputer\local-rules.md`（用户本机私有规则，如网络/代理分流、常用网站路由）
   - 宿主已有的其他 MCP Server 配置（仅增量更新 `edge-devtools` 与 `windows-uia`，严禁删除其他 MCP，也不得往 `chrome-devtools` 写入 `--autoConnect`）
   - 用户全局规则文件（如 `~/.codex/AGENTS.md`、`~/.gemini/config/GEMINI.md`）
```

## 命令行安装（手动触发等同上）

```powershell
git clone https://github.com/LXG1218/auto-computer.git
cd auto-computer
.\install.ps1
.\setup-auth.ps1 --check
```

- `.\install.ps1` 默认一次性完成 Python 环境、WinRT OCR、Node 工具链与各宿主 MCP/Skill 注册；重复运行即为无损升级，会自动备份旧文件并保留本机私有资产与令牌。
- 如需后台复用日常 Chrome / Edge 已打开登录态标签页，执行 `.\pair-browser.cmd` 完成一次浏览器扩展配对即可；扩展令牌使用本机 DPAPI 加密保存，不入仓库。

---

## 各场景实现方式

Auto Computer 按场景选择开销最低、稳定性最高的通道，避免所有问题一刀切走前台视觉循环：

- **本地文件 / 表格 / 图像**：通过本地结构化批处理完成查找、对账、读写与排版；写前自动备份、写后读回核验，并支持非首行表头自动定位，无需先探测表头行。
- **公开与匿名网页**：通过 Chromium 命名管道隔离通信，**0 TCP 端口暴露、0 远程调试横幅、0 Allow 授权弹窗**。纯后台无头抓取在任务完成且无残余页面时**立即自动关闭浏览器进程**，零后台内存残留并省去额外关闭轮次。
- **日常已登录网页**：通过扩展令牌安全附着用户已授权的日常浏览器标签页，核对精确 URL 后执行读取或表单动作，结束瞬间自动断开以消掉顶部调试横幅；若日常窗口未开对应标签，可按需一步无缝降级至独立保留登录态的 `kept` 空间。**绝不自动关闭用户日常浏览器或任何可见窗口。**
- **常规 Windows 桌面软件**：由后台无障碍通道（强制不抢焦点）驱动，配合响应字节预算控制、增量控件树对比及按进程自动锁定唯一主窗，压低交互轮次与 Token 体积；桌面守护进程首次调用按需拉起，空闲 10 分钟自动休眠回收内存。
- **自绘 / 无控件桌面软件（如微信 4.x、QQ、钉钉、飞书、千牛等）**：绕开空控件树，在单次短暂前台会话内完成“视觉定位 → 标题强校验防误操作 → Unicode 绕输入法精准注入 → **像素差分主核验 + 2× 增强本地 OCR 容错**”；零第三方重依赖，兼容 x64 与 ARM64，不受用户更换系统字体、字号或浅色按钮对比度影响。
- **短暂借前台安全护栏**：仅在目标程序必须前台响应时介入，执行前自动校验坐标窗口遮挡；期间展示全屏点击穿透流光边框与顶部状态提示，并挂载底层物理 `Esc` 一键急停与粘键释放，动作完立即退场。
- **三层通用记忆系统**：将精简主路由规则（极低常驻 Token）、按软件/域名隔离的本机画像卡片（命中才按需读取，存 `%LOCALAPPDATA%\AutoComputer\adapters\profiles\`）与本机私有路由（`local-rules.md`）彻底分层；内置防膨胀门槛（仅沉淀反复踩坑、结构反直觉或高频复用的稳定事实，失效即原地覆盖），升级永久保留且永不提交至代码仓库。

---

## 实测性能与 Token 对比

### 1. 同机 10 场景横向对比（6 网页 + 4 桌面，全部 10/10 通过）

| 项目方案 | 10 场景总耗时 | 单场景平均耗时 | 工具调用总次数 | 静态常驻上下文 | 10 场景动态总 Token |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auto Computer** | **1,285.8 ms** | **128.6 ms** | **11 次** | **~1,812 tok** | **5,342 tok** |
| `dsh-computer-use-win (v0.6.4)` | 5,344.9 ms | 534.5 ms | 17 次 | ~7,796 tok | 9,619 tok |
| `jason-skill (0.3.0-draft)` | 12,968.8 ms | 1,296.9 ms | 14 次 | ~1,822 tok | 6,278 tok |

- **横向结论**：同机 10 场景下，Auto Computer 比 `dsh-computer-use-win` 快约 **4.2×**（静态上下文省约 **77%**、动态 Token 省约 **44%**），比 `jason-skill` 快约 **10.1×**（动态 Token 省约 **15%**）。
- *说明：测试环境为 Windows 11 ARM64 本地闭环基准（覆盖页面加载提取、多字段填报、下拉复选、异步表格、列表抓取、窗口定位、UIA 控件树读取、填表保存及增量读回；Token 按 `round(cjk * 1.3 + non_cjk / 4)` 估算；横向 10 场景基准基于 `v1.3.0` 架构采集，`v1.4.0` 在此基础上进一步压低了空闲内存与高频调用轮次）。*

### 2. `v1.4.0` 关键专项实测提升（vs `v1.3.0`）

| 专项场景 | 优化前 (`v1.3.0`) | 优化后 (`v1.4.0`) | 实测改善 |
| :--- | :--- | :--- | :--- |
| 桌面空闲常驻内存（单窗进程树） | 41.7 MB Private | **14.9 MB Private**（10 分钟空闲自动休眠） | **空闲内存降 64.3%** |
| 窗口枚举噪声过滤（以微信为例） | 12 窗 / ~358 tok | **5 窗 / ~166 tok** | **Token 减 53.6%** |
| 多字体 / 16px 小字 / 低对比核验（6 组实图） | 2/6（33.3%） | **6/6（100%）** | **抗换字体误杀归零** |
| 自绘单次前台发消息全链路 | 3,994.9 ms（2 轮 / 2 次前台） | **984.0 ms（1 轮 / 1 次前台）** | **提速 4.06×** |
| 非首行表头对账（500 行含前置备注） | 287.2 ms（3 轮） | **91.9 ms（1 轮）** | **提速 3.13×** |
| 高频任务 LLM 往返收敛 | 2–3 轮 | **1 轮直达**（按进程发桌面 / 无头抓完即关 / 网页无缝降级 `kept`） | **显著减少模型等待轮次** |

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
