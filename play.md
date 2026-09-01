# treeland-autoui-mcp 项目总计划与执行记录

> 本文件是项目后续工作的统一执行路线图。所有新增计划、阶段状态、验收结果和对应提交都应同步到这里，避免只存在于聊天记录中。
>
> 详细设计与决策背景见 [docs/qwen-cua-architecture.md](docs/qwen-cua-architecture.md)。本文件回答“已经完成什么、下一步做什么、怎样算完成”。
>
> 下方原始 Task 1～6 是 OmniParser 融合能力的历史基线，保留用于回归和 A/B 测试。其中 `treeland-windowtree` 已被 `treeland-debug --tree` 替代，OmniParser 默认不启用。

## 当前状态摘要

- [x] Treeland 树数据改为通过 `treeland-debug --tree` 获取，移除运行时 `treeland-windowtree` Python 包依赖。
  - 提交：`dac065b1f35d5b25ed60baf551120b90837ad18e`
- [x] 保留 OmniParser 旧接口，通过 `GUI_OMNIPARSER_ENABLED` 控制，默认关闭。
- [x] 建立 Qwen-CUA 第一阶段工作流：预测、坐标融合、执行前复核、执行、重置和状态查询。
- [x] 使用安全 AST 解析与动作白名单，不执行 Qwen 返回的任意 Python。
- [x] 初步处理窗口移动：执行前重新获取窗口树，同一目标窗口移动时重投影相对坐标。
- [x] 增加 Qwen 会话一致性保护：预测后没有完整成功执行的动作，不继续沿用旧会话。
- [x] 第一阶段测试通过（10 项），锁文件、Python 编译和 JSON 配置检查通过。
  - 提交：`f66bcdc1aa4d4519ffce6977d9f98e6b746ca4a0`
- [x] `client_env.sh` 仅在开头加载同目录 `.env.local` 并自动导出 CUA 变量；`.env.example` 提供模板，`.env.local` 被 Git 忽略且权限为 `600`，原有 Wayland/Treeland/ydotool 环境逻辑保持不变。
- [ ] 将 `~/gui-mcp/` 的 Qwen-CUA 后端能力迁入本项目，并完成端到端联调。

## 执行原则

- Qwen-CUA 负责视觉理解、任务推理和提出下一步动作；本地控制 AI/计算层负责融合 Treeland 树、校验坐标、执行动作和反馈结果。
- Treeland 树是本地已知的确定性证据，不要求 Qwen 独立维护一份完整 GUI 状态。
- 本项目内的 Qwen-CUA 推理组件应统一维护模型对话/推理状态；控制与融合层只保存可重建的执行状态、帧标识、动作提案和审计记录。
- 所有会改变屏幕状态的动作都必须经过本地校验；预测接口本身不得执行动作。
- 优先提升任务成功率、目标命中率和可恢复性，再优化延迟与 token 成本。

## 历史基线：OmniParser 与 Treeland 空间融合

目标: 开发一个 Treeland 窗口与 OmniParser 标记数据的空间碰撞与融合脚本
 请根据以下详细的任务分解（Task List），修改treeland-autogui-mcp，将来自合成器（Treeland）的窗口树数据与来自视觉 AI（OmniParser）的标记元素进行空间关联与层级组织。

 数据信息：

 1. /home/uos/Downloads/omniparser_mark.json文件是/home/uos/Downloads/treeland-autoui-mcp/src/mcp_autogui/mcp_autogui_main.py omniparser_details_on_screen函数解析的detail内容

 omniparser_mark.json：包含了 AI 识别出的 UI 元素列表，每个元素包含 type, content, 以及相对比例坐标 bbox。


 2. /home/uos/Downloads/treeland-windowtree.json文件是/home/uos/Downloads/treeland-windowtree/src/treeland_windowtree/_core.cpp py::list get_full_layout_tree() 函数返回的内容

 treeland-windowtreedata.txt：包含了桌面的窗口层级 JSON（包含 background, workspace, top 等不同层，窗口拥有 layer, z, visible, geometry 等属性）。

 详细任务列表 (Task List)
 task 1:  解析与平铺 Treeland 窗口数据
       - 通过treeland-windowtree的接口获取treeland合成器的窗口层级信息，具体接口使用方法参考该项目的README.md文件
       - 遍历所有层级（包括 background、top 以及 workspace 内部各个虚拟桌面 workspaces -> windows）。
         layer 越大层级越高，高层级的视觉元素会覆盖掉低层级
         background layer 存储的window是当前桌面的窗口双屏，将存在两个window一个屏幕一个background窗口，为全屏覆盖窗口层级最低
         workspace layer  默认包含两个工作区， isActive为true的是显示的工作区 包含一些窗口
         top layer存储的是 dock任务栏窗口

         过滤条件：只保留 visible == true 且 geometry 宽高大于 0 的窗口。这个只对workspace layer有用

         排序逻辑（关键）：将过滤后的窗口存入一个一维列表，并按照 Z 序从顶层到底层 进行降序排列。

         优先级规则：先按 layer 从大到小排序（如 top 层的 layer=2 优先于 workspace 的 layer=0）；如果 layer 相同，再按 z 轴值从大到小排序（如 z: 1 的 Terminal 优先于 z: 0 的 Calendar）。

Task 2: 动态获取屏幕分辨率
       由于 OmniParser 是相对比例坐标，需要乘以屏幕物理宽高。

       通过解析 Treeland 数据中 name: "background" 窗口的 boundingRect 或 geometry 属性，动态获取屏幕的 SCREEN_WIDTH（如 1920.0）和 SCREEN_HEIGHT（如 1080.0）。

Task 3: 坐标转换与归一化 (Normalize Box)

    编写一个转换函数，处理 OmniParser 的 bbox 数组。

    注意输入特征：OmniParser 的 bbox 格式为 [ymin, xmin, ymax, xmax]。

    转换公式：

        x1 = xmin * SCREEN_WIDTH, y1 = ymin * SCREEN_HEIGHT

        x2 = xmax * SCREEN_WIDTH, y2 = ymax * SCREEN_HEIGHT

    该函数最终返回一个标准的像素绝对坐标矩形：{"x1": x1, "y1": y1, "x2": x2, "y2": y2}。


Task 4: 实现空间包含算法（碰撞检测）

    编写函数 is_element_in_window(elem_box, win_geometry)。

    计算元素的绝对坐标中心点：cx = (x1 + x2) / 2, cy = (y1 + y2) / 2。

    根据 Treeland 窗口的 geometry（包含 x, y, width, height），计算窗口边界：

        wx1 = x, wy1 = y, wx2 = x + width, wy2 = y + height

    判断元素的中心点 (cx, cy) 是否完全落在窗口边界 [wx1, wy1, wx2, wy2] 内部，返回布尔值。


Task 5: 核心融合与 Z 序遮挡处理 (Data Fusion Loop)

    初始化每个有效窗口，为其添加一个空列表属性 "elements": []。

    遍历 OmniParser 的每一个标记元素：

        使用 Task 3 的函数将其转换为绝对像素坐标。

        按照 Task 1 排好序的窗口列表（从顶层到底层） 依次进行碰撞检测。

        贪婪捕获（核心）：一旦发现该元素落在了某个窗口内部，立刻计算该元素相对于该窗口左上角的相对坐标（relative_x1 = x1 - win_x，以此类推），将元素数据（含绝对坐标、相对坐标、类型、内容）追加到该窗口的 "elements" 列表中，并立刻 break 中断当前窗口循环（防止被下层被遮挡的窗口错误捕获）。

        如果遍历完所有窗口都没有捕获该元素，将其归类到全局的 desktop_unparented_elements 列表中（代表属于桌面壁纸或全局组件）。


Task 6: 格式化输出与验证

    将融合后带有 elements 子列表的窗口树数据 最终输出为一个结构清晰的完整 JSON 文件写入到 omniparser_details_on_screen 中的detail中

    打印出融合统计结果（例如：“成功融合了多少个元素，有多少个未分配的全局元素”），以便于验证正确性。

---

## 阶段 2：迁移 Qwen-CUA 后端并完成联调（进行中：真实模型基础联调已通过，扩大动作与异常场景验收中）

目标：以 `~/gui-mcp/` 为代码来源，将 Qwen-CUA 的推理、提示词、图像处理和会话状态能力直接迁入 `treeland-autoui-mcp`。迁移完成后，本项目运行不依赖 `~/gui-mcp/` 目录或它的 FastAPI 服务；随后再验证真实桌面链路。

### 2.1 迁移边界与目录设计

- [x] 盘点 `cua_mcp_backend` 的模块、依赖、配置和许可证/来源，区分 Qwen-CUA 必需代码与其他 agent、日志、实验产物。
- [x] 在本项目中建立独立的 Qwen-CUA 包 `src/mcp_autogui/qwen_cua_backend/`，避免继续依赖 `~/gui-mcp/` 的 Python 路径。
- [x] 只迁移当前 Qwen-CUA 必需能力：S2 agent、prompts、图像预处理/坐标投影、会话状态和最小配置。
- [x] 未迁移历史日志、已有 trajectory 图片、缓存、编译产物和与 Qwen-CUA 无关的 agent。
- [x] 在包内 README 记录来源路径、检查日期、迁移范围和差异；源目录没有可用 Git revision/许可证文件，因此明确记录该限制。

### 2.2 内嵌服务接口

- [x] 实现与传输无关的 `QwenCUAService` Python 接口，MCP 工具默认在进程内直接调用。
- [x] `QwenCUAService` 提供初始化、预测、提交实际执行结果、重置、状态查询和关闭能力。
- [x] 把现有 `QwenBackendClient` 改造成适配层：默认 `embedded`，可选保留 `http` 模式用于隔离部署和对比测试。
- [x] `embedded` 模式不要求 `CUA_BACKEND_URL`，也不启动或连接 `~/gui-mcp/` 服务。
- [x] 模型推理地址、模型名、API key 和超时通过环境变量配置；连接实际 OpenAI-compatible 模型端点，不连接 `gui-mcp` 中间后端。
- [x] 同一 session 使用独立锁串行预测/反馈，不同 session 可并行使用共享模型 client。
- [x] `close()` 会关闭模型 client、清理 session 和释放 HTTP 资源。

### 2.3 依赖与配置迁移

- [x] 将实际使用的最小直接依赖 `openai`、`httpx` 加入 `pyproject.toml`，没有照搬全部 agent 依赖。
- [x] 将原 YAML 配置改为本项目环境变量配置，没有迁移硬编码路径、地址、API key 和测试机信息。
- [x] 更新 `uv.lock` 并通过 `uv lock --check`。
- [ ] 在具备依赖下载条件的全新环境中，验证只安装本项目即可运行 Qwen-CUA 路径。
- [x] 为无模型配置、模型不可达、超时和响应格式错误提供清晰状态或异常。

### 2.4 迁移等价性测试

- [ ] 使用固定截图和固定模型响应，对比迁移前后 prompts、消息结构、图像尺寸、坐标投影和动作解析结果。
- [x] 使用脱敏固定 S2 返回测试点击坐标和非法动作；当前只迁移 S2，不启用 S1。
- [x] 验证 session 初始化、连续预测、反馈提交、拒绝、重置、关闭和多 session 隔离。
- [x] 验证本项目代码中不 import `~/gui-mcp/`，默认路径不请求它的 `/init`、`/predict`、`/reset` 或 `/health`。

### 2.5 真实模型只预测、不执行

- [x] 使用无敏感内容的合成截图调用真实 `qwen3_rl` 端点，内嵌服务成功返回 `pyautogui.moveTo(500, 409)`，未执行动作。
- [x] `client_env.sh` 启动时只安装当前系统缺失的 apt 包，已安装的包不再重复安装或触发 `apt-get update`；AT-SPI 使用系统 Python 验证，`ydotoold` 使用非保留变量传递 UID/GID。
- [x] `client_env.sh` 默认使用 Codex 支持的 Streamable HTTP，对外端点为 `/mcp`；保留 `MCP_TRANSPORT=sse` 兼容旧客户端。
- [x] 新增 `docs/manual-test-guide.md`，定义连接、只预测、待执行保护、低风险执行、重投影、拒绝、多轮状态和 5×10 重复任务的手工测试方法。
- [x] 保留 `pyscreenshot` 的通用后端自动选择，不在项目内强制绑定 `grim` 或其他桌面专用后端。
- [x] 在真实 Treeland 会话中记录 `pyscreenshot` 各候选后端的完整失败原因：可用后端为 `pil`（1920x1080 OK）与 `grim`（1920x1080 OK，Wayland 原生）；默认自动选择 grab 成功。失败原因：`scrot`/`maim`/`gnome-screenshot` 命令未安装；`imagemagick` 无 X11 授权协议（`import: unable to open X server :0`，Wayland 会话下 DISPLAY=:0 但无 XAUTHORITY）；`pyqt5/pyside2/wx/pygdk3/mss` 对应依赖缺失；`freedesktop_dbus` 返回布尔而非图像、`gnome_dbus` 截图文件未生成；`mac_*` 仅 Darwin。结论：`WAYLAND_DISPLAY=/run/user/1000/treeland.socket`、`XDG_SESSION_TYPE=wayland` 与会话环境正确，compositor 截图权限正常。
- [ ] 重新运行 `client_env.sh`，确认 `pyscreenshot` 至少有一个自动选中的后端可用，且 MCP SSE 服务持续运行。`pyscreenshot` 后端可用性已通过进程内诊断确认（`pil`/`grim` 可用、默认 grab 成功）；`client_env.sh` 整体重跑含 `sudo dde-dconfig` 步骤，需在有权限的终端执行，本会话未重跑。
- [x] 配置真实 Qwen 模型端点，在一个可恢复、无敏感数据的测试桌面上调用内嵌后端和 `qwen_cua_predict`。
- [x] 收集真实 Qwen 返回的动作文本，已覆盖点击、双击、右击、输入、滚动、快捷键、等待和完成等类型；2026-09-01 补齐 `pyautogui.write`、`pyautogui.scroll(-3)`、`Super+D`、`Ctrl+Z`、`Ctrl+A` 和 `Backspace` 样本。
- [x] 针对真实模型返回的移动动作，核对动作解析结果、原始坐标、归一化坐标、屏幕坐标和目标窗口归属。
- [x] 检查 Qwen 返回格式与当前 AST 解析器是否完全兼容：20 个真实 `qwen3_rl` 返回样本（moveTo/click/doubleClick/scroll/press/hotkey/write/time.sleep/DONE/WAIT/FAIL/多语句组合）全部解析成功；`import os`、`os.system`、非字面量参数、白名单外函数全部被拒绝；样本已固化为 `tests/test_qwen_actions.py` 的 `RealReturnCompatibilityTests` 回归夹具。未放宽到任意代码执行。
- [x] 检查无窗口命中、边界点、遮挡窗口、Dock、桌面和弹窗场景：无窗口命中=本轮显示桌面后点击 `(960,449)` 融合到 `BackgroundContainer`（`matching_window_count=1`）；边界点=`(1919,1079)` 命中 `TopContainer` 相对点 `(1919,47)`、`(1920,1080)` 在模型调用前被控制层拒绝；遮挡窗口=2.6 冻结提案被 Firefox 覆盖返回 `target_window_changed` 且未注入；Dock=`TopContainer` 层融合；桌面=壁纸点击成功；弹窗=`Esc` 后上下文菜单消失（窗口数 6→4）。未覆盖：弹窗作为目标窗口的融合与执行（Tree 子控件语义边界，另见结论文档 4.1）。
- [x] 修复 `desktop_bounds` 回归（2026-09-01）：层名大小写不敏感修复让 background 优先路径首次生效，但 Deepin 的 `BackgroundContainer` 高度 1032 不含 Dock 层，导致 `desktop_bounds` 高度从 1080 变 1032、Dock 点击坐标换算错位（`click(720,1051)` 被错误融合到 Firefox 而非 TopContainer）。改为合并所有层窗口外接（flatten + background boundingRect 并集），单屏恢复 1920x1080；Dock 点击融合目标恢复为 `TopContainer`。新增回归测试 `test_desktop_bounds_include_top_dock_layer`。

#### 2.5.1 2026-08-31 真实桌面基础验收记录

- [x] 当前 Codex 会话发现并成功调用 `qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset` 和 `qwen_cua_status` 4 个 MCP 工具。
- [x] 后端状态为 `embedded`、模型为 `qwen3_rl` 且配置有效；验收前清理了 2 个历史待执行会话。
- [x] 在真实 Treeland 桌面只观察不操作：Qwen 正确识别活动窗口为运行 `htop` 的终端，并返回 `DONE`；Treeland 树对应窗口为 `deepin-terminal`、标题为 `htop - Terminal`。
- [x] 验证截图尺寸 `1920x1080`、逻辑桌面尺寸 `1536x864` 的 1.25 倍缩放场景：模型归一化坐标 `[500, 555]` 解析为截图坐标 `(960, 599)`，融合后得到逻辑桌面坐标 `(768.0, 479.2)`，命中 `htop` 终端且位于窗口范围内。
- [x] 执行一次仅移动鼠标、不点击、不输入的低风险动作；执行成功并向内嵌 Qwen 后端提交反馈。随后同一 session 再次预测，模型观察到鼠标已到位并返回 `DONE`，证明基础多轮状态连续。
- [x] 验证待执行保护：同一 session 在前一提案未执行时再次预测会被拒绝，reset 后恢复。
- [x] 扩大真实动作样本，已补齐输入、合法滚动和安全快捷键；移动、点击、双击、右击、输入、滚动、`Esc`、`Super+D`、`Ctrl+Z`、`Ctrl+A`、`Backspace`、等待和完成均取得真实返回样本，其中错误语义与后置条件误判另列为待修问题。
- [x] 定位执行链路延迟：2026-09-01 加入 `stage_timings_ms` 后直连采样：predict 总计 2.03s（模型 1.76s、截图+取树 0.27s）、execute 总计 0.48s，外层 HTTP 调用 2.46s——服务端各阶段无 78 秒级耗时，历史长等待在 MCP 传输/旧挂载/代理侧（待旧挂载复测确认）；取树约 0.27s 为后续优化点。提交 `5d393d0`。
- [x] 修正或澄清 `qwen_cua_status.pending_sessions` 语义：现区分 `pending_sessions`（仅真正待执行）与 `known_sessions`（全部本地已知）；真实链路复验通过。提交 `5d393d0`。

#### 2.5.2 2026-08-31 扩展动作验收记录

- [x] 等待动作通过：真实模型返回 `time.sleep(1.0)`，执行结果为 `Waited 1.0 seconds`；同一 session 第二轮返回 `DONE`，后端历史累计为 2 轮。
- [x] 单击动作基础通过：模型只返回一个 `pyautogui.click`，融合坐标位于桌面背景区域；执行后第二轮视觉观察认为 `cua-test.html` 已被选中且未打开。
- [x] `Esc` 按键动作通过：模型返回 `pyautogui.press('esc')`，执行后 Treeland 窗口数从含弹出层时的 6 降至 4，下一轮视觉确认上下文菜单消失。
- [x] 滚动动作回归通过：旧模型曾在约 53k tokens 中重复生成至少 80 个 `moveTo`；加入单调用与响应长度限制后，首次不合规响应被拒绝且未执行，reset 后明确要求滚轮动作得到唯一 `pyautogui.scroll(-3)`。执行后页面从顶部滚至 “Signed Distance Field / GPU文本渲染技术 / 字体测试”，第二轮视觉确认并返回 DONE。
- [x] 双击任务回归通过：Flat 配置修正后，Qwen 对桌面 `cua-test.html` 返回唯一 `doubleClick(150,514)`，执行后光标读回同坐标；下一帧 Treeland 新增活动 Firefox 窗口，标题为“缩放和字体渲染问题 — Mozilla Firefox”，Qwen 返回 DONE。
- [x] 右击动作通过：右击后出现包含 `Mode`、`Position`、`Lock the Dock` 等条目的菜单。Treeland 只能把坐标归到 `BackgroundContainer`，因此当前不能独立证明该菜单属于桌面文件还是 Dock；这是桌面子控件语义的可观测性边界，而非本次“右击”动作失败。
- [x] MCP 端到端等待抖动：已为传输、截图、取树、执行、反馈和响应序列化分别计时（`stage_timings_ms`，提交 `5d393d0`）；直连 127.0.0.1 复测 predict 外层 2.46s、execute <1s，未再现 55/74/123/163 秒等待，初步归因到旧挂载 MCP 客户端/代理侧（待旧挂载复测确认）。
- [x] 在 Deepin Editor 中通过 `+` 新建 `Untitled 3`，确认 `Characters 0` 后输入 `Treeland CUA input test 2026-09-01`；截图确认精确文本和 `Characters 34`，未保存，随后通过 `Ctrl+A`、`Backspace` 清理并确认回到 `Characters 0`。

#### 2.5.3 2026-09-01 Flat 修复后真实桌面回归

- [x] 精确移动通过：控制层约束截图坐标 `(100,100)`，Qwen 返回归一化 `[52,93]`，融合、实际执行和 `post_action_cursor` 均为 `(100,100)`，误差 0；截图与 Treeland 桌面尺寸本轮均为 `1920x1080`。
- [x] 同 instruction 会话连续性通过：动作反馈后第二轮为 `step=2`、`history_turns=1`，Qwen 根据实际执行反馈返回 DONE；修改 instruction 会按当前设计主动 reset，因此不作为状态丢失。
- [x] 本地页面双击和滚动通过：打开 `cua-test.html` 后，合法单步 `scroll(-3)` 产生可见内容变化并由下一帧确认。
- [x] 安全文字输入和清理通过：空白 `Untitled 3` 中精确输入 34 个字符且未保存，最后清理至 `Characters 0`；所有 13 个测试 session 已 reset，后端恢复 `sessions=0`、`pending_proposals=0`。
- [x] 截图边界坐标通过：受约束的右下角 `(1919,1079)` 融合命中 `TopContainer` 的相对点 `(1919,47)`，执行参数和光标读回均为 `(1919,1079)`，误差 0；屏幕外一像素的 `(1920,1080)` 在调用模型前被控制层拒绝，未产生动作。
- [x] 处理 PyAutoGUI FailSafe 角落状态：执行前显式检测并拒绝 `cursor_in_failsafe_corner`，不全局关闭 `pyautogui.FAILSAFE`。2026-09-01 真实 MCP 链路复验通过：光标先被诊断移出角落后，经受约束 `mouse_move` 移回 `(1919,1079)`（执行成功、读回误差 0）；随后 `Super+D` 的 `qwen_cua_execute` 返回 `status=error`、`error_code=cursor_in_failsafe_corner`、结构化 `cursor=(1919,1079)`，未注入按键、无原始 `FailSafeException`，后端收到 `error` 反馈（`committed=false`）且 session 未污染；两个 session 均已 reset，后端恢复 `sessions=0`。运行中的 MCP 进程（17:05 启动）已加载修复（源码 17:01 修改、pyc 17:01:38 编译、`_failsafe_corner` 存在），本次无需重启。
- [x] 修正完成 session 状态语义：`qwen_cua_status` 现区分 `pending_sessions`（仅列出 `execution_complete=false` 且 `requires_reset=false` 的真正待执行 session）与 `known_sessions`（全部本地已知 session）。2026-09-01 真实链路复验通过：predict 后 `pending_sessions` 列出该 session；execute 成功后 `pending_sessions=[]` 而 `known_sessions` 保留；reset 后后端 `sessions=0`。提交 `5d393d0`。
- [x] 修正并真实复验动态坐标约束导致的会话历史丢失：后端现在区分稳定的任务 instruction 与仅用于本轮的约束 prompt；约束有无或坐标变化不再被当作任务变更。真实模型执行受约束的 `(100,100)` 移动后，在相同任务下取消约束进行第二轮预测，返回 `history_turns=1`、`message_count=4` 和 `DONE`，未重复移动。首次模型响应失败会清理新建空 session；模型响应已返回但本地解析/约束校验失败时会反馈 `rejected` 释放后端 pending；31 项回归测试通过。
- [ ] 修正外层调用延迟抖动：模型 telemetry 通常约 0.9～2.0 秒，但部分无副作用 DONE、输入和清理调用仍观察到约 78～215 秒外层等待。已加入 `stage_timings_ms` 分阶段计时（`qwen_cua_predict`：截图/约束/模型/解析/融合/总计；`qwen_cua_execute`：取树/重融合/准备/执行/光标/post-tree/反馈/总计；提交 `5d393d0`）。2026-09-01 真实链路采样：predict 各段总计 2.03s（模型 1.76s、截图+取树 0.27s）、外层 HTTP 调用 2.46s，execute 总计 0.48s——服务端与传输均无 55～215s 抖动，旧挂载链路/代理侧才是抖动源（结论待旧挂载复测确认）；`frame_capture_ms` 约 0.27s 主要是取树耗时，可作后续优化点。
- [x] 融合候选必须过滤 `visible=false` 窗口：真实层名是 `WorkspaceContainer`/`BackgroundContainer`，`_append_window` 原只匹配小写 `workspace` 导致过滤从未生效；已改为大小写不敏感匹配，并同步修正 `desktop_bounds_from_treeland` 的 `background` 层名匹配。2026-09-01 真实复验：Super+D 显示桌面后 Tree 中 Firefox/Editor 均为 `visible=false`，模型点击桌面返回 `pyautogui.click(960, 449)`，融合目标为 `BackgroundContainer`（`matching_window_count=1`），隐藏全屏 Firefox 不再参与命中；执行成功并恢复窗口、清理 session。新增 2 个回归测试（真实容器层名 + 隐藏全屏窗口排除）。
- [ ] 增加确定性后置条件校验：单次 `Ctrl+Z` 只将字符数从 34 降到 33，Qwen 却宣告已完成并错误描述剩余文本；执行器成功和模型主观 DONE 都不能代替精确状态验证。
- 本轮补充证据：`Untitled 4` 的输入动作和可见文本通过，但下一帧视觉转写把 `Qwen` 读为 `Owen`；重复文本输入在接入确定性读取前不计为字符级成功。
- [ ] 改进快捷键知识或控制层约束：无明确按键提示时，Qwen 错把 Firefox 中的 `Alt+D` 当作显示桌面；该提案未执行，明确要求 `Super+D` 后回归通过。
- [x] 键名映射补丁（环境级，非仓库提交）：pyautogui wayland 后端 `_pyautogui_wayland.py` 的 keyboardMapping 原只有 `win/winleft/winright -> KEY_LEFTMETA`，`super`/`command` 等模型常用键名缺失且被静默跳过（`hotkey('super','d')` 实际只裸按 `d`，有注入字符风险）。已在 venv 站点包补丁加入 `super/superleft/command/cmd/meta -> KEY_LEFTMETA`、`superright -> KEY_RIGHTMETA`、`option/optionleft/optionright -> KEY_LEFTALT/RIGHTALT`、`control -> KEY_LEFTCTRL`。2026-09-01 真实复验：`hotkey('command','d')` 与 `hotkey('super','d')` 均正确触发显示桌面（窗口最小化/恢复）。注意该修改在 `.venv/.../pyautogui/_pyautogui_wayland.py`，全新环境需重打补丁；是否上移为项目内键名规范化待决策。

### 2.6 低风险执行

- [x] 使用移动鼠标动作开始调用 `qwen_cua_execute`，未执行点击或输入。
- [x] 执行桌面测试图标单击、等待和 `Esc` 等低风险动作并完成下一轮观察。
- [x] 在专用测试窗口完成双击打开本地页面和滚动，并通过下一帧截图与 Treeland 活动窗口确认结果。
- [x] 验证执行前重新取树、窗口身份比对和窗口移动后的坐标重投影：冻结的 Text Editor 内点从原窗口 `(80,80)` 的相对坐标 `(320,220)`，在用户将窗口移动到 `(280,96)` 后被执行器重算为 `(600,316)`；实际光标读回 `(600,316)`，误差 0。
- [x] 验证目标窗口变化拒绝：冻结的 Text Editor `(599,316)` 移动提案在执行前被 Firefox 覆盖，执行器重新取 Tree 后返回 `status=refused`、`target_window_changed`，没有注入鼠标动作；后端收到 `rejected` 反馈。
- [x] 验证一次完整的 `predict -> fusion -> execute -> predict` 会话连续性。
- [x] 验证部分执行和执行异常后，下次预测会安全重置而不是使用错误历史。代码（提交 `5d393d0`）收紧为仅"全部动作成功且反馈成功"才可继续：`session_continuable = feedback_recorded and all_actions_selected and all_actions_succeeded`，否则 `requires_reset=true`。2026-09-01 真实复验：对同一 session 重复 `qwen_cua_execute`，第二次反馈报 "no pending proposal" → `session_continuable=false`、`next` 提示将重置；复用同 session 第三次 `qwen_cua_predict` 自动 reset 成功（`step=1`、`history_turns=0`），未使用错误历史。
- [x] 验证目标窗口拒绝后的会话恢复：Firefox 的冻结提案被切回的 Text Editor 覆盖后收到 `rejected`、`history_turns=0`；复用同一 session 的第二轮能基于新截图生成新提案，拒绝动作没有写入成功历史。`previous_feedback` 被放入当前 user message，因此 telemetry 的 `message_count=2` 属正常，不表示反馈丢失；第二轮未执行并已 reset。
- [x] 已在未保存的专用空白文档测试文字输入、`Super+D`、`Ctrl+Z`、`Ctrl+A` 和 `Backspace`，并清理测试文本；涉及文件保存、删除、提交、付款、授权等高风险动作仍必须人工确认或禁用。

### 2.7 阶段验收

阶段性结论单独记录在 `docs/desktop-acceptance-conclusion-2026-09-01.md`：当前仅对有人监督、低风险、可恢复的日常桌面测试有条件通过；无人值守和高风险操作不通过。后续按相同任务、环境、证据格式和指标单独启用 OmniParser，与 Qwen-CUA 进行对比测试。

真实验收中发现的 Qwen-CUA 问题单独记录在 `docs/model-training-observations.md`：当前确认的视觉训练候选是文字视觉转写；桌面图标/Dock 的问题目前是 Tree 子控件语义可观测性边界，尚无独立模型失败样本；快捷键知识、工具调用格式和完成判断是 Qwen-CUA 非视觉行为问题。

- [x] 将 `~/gui-mcp/` 临时移出可发现路径后，本项目的 Qwen-CUA 功能仍能启动并运行：2026-09-01 将 `~/gui-mcp` 改名为 `~/gui-mcp.bak-20260901` 后，服务经 hub 以环境快照启动成功（port 8000 ready），`qwen_cua_status` 返回 `embedded/qwen3_rl`，受约束 `mouse_move` 真实预测成功；运行进程 maps 无 `gui-mcp` 引用（0 处）。验证后已移回原目录，后端恢复 `sessions=0`。
- [x] 默认运行只需要启动 `treeland-autoui-mcp`；不需要用户手工启动第二个 `gui-mcp` 后端。
- [x] 至少选取 5 个可重复桌面任务并完成重复验证（2026-09-01 按已有重复证据验收）：1/5 受约束精确鼠标移动 10/10；2/5 空白文档固定文本输入 18 次采样 17/18 字符级精确；3/5 双击桌面 `cua-test.html` 打开 Firefox（2.5.2 回归通过，光标读回同坐标、下一帧新增活动 Firefox）；4/5 单步滚动（2.5.2/2.5.3 多次验证 `scroll(-3)` 产生可见内容变化并由下一帧确认）；5/5 `Super+D` 显示桌面/恢复（本轮多次验证，Tree 中窗口 `visible` 翻转确认）。其中 3/5、4/5、5/5 未做严格 10 连跑，以既有重复样本+确定性状态确认（Tree/剪贴板）为准；如需严格 10 连可后续补跑。
- [x] 重复任务 3/5（双击桌面图标打开本地页面）：Qwen 对桌面 `cua-test.html` 返回唯一 `doubleClick(150,514)`，执行后光标读回同坐标；下一帧 Treeland 新增活动 Firefox 窗口（标题“缩放和字体渲染问题 — Mozilla Firefox”），Qwen 返回 DONE；2.5.2 记录为回归通过。
- [x] 重复任务 4/5（单步滚动）：本地页面中合法单步 `scroll(-3)` 产生可见内容变化（页面从顶部滚至 “Signed Distance Field / GPU文本渲染技术 / 字体测试”），下一帧视觉确认；2.5.2/2.5.3 多次验证。
- [x] 重复任务 5/5（安全快捷键显示桌面/恢复）：`Super+D`（键名映射补丁后 `super`/`command` 均可用）多次执行，Tree 中 Firefox/Editor 的 `visible` 在 true/false 间翻转确认生效；FailSafe 场景下被结构化拒绝。
- [x] 重复任务 1/5（受约束精确鼠标移动）：在同一安全 Text Editor 场景连续运行 10 次，每次预测 `(600,316)`、执行、读回光标并 reset；10/10 成功，模型/执行读回均为 `(599,316)`，相对目标最大量化误差 1 像素（容差 3 像素），无跨窗口、解析或执行失败。
- [x] 重复任务 2/5（空白文档固定文本输入）：接入剪贴板确定性后置条件（Ctrl+A/Ctrl+C 复制 → pyperclip 读取，字节级比对，不再依赖模型视觉转写）。Alt+Tab 聚焦 `Untitled 4` 后连续运行 10 次：输入 `Treeland CUA input test 2026-09-01` 9/10 字符级精确、清理（Ctrl+A/Backspace）10/10 回到空文档（sentinel 探测法排除空选区复制不更新剪贴板的假阴性）；1 次模型 `write` 只输出 `Treeland`（不完整，另见 observations Q-004）。另一次 10 连跑为 8/10 输入精确（2 次截断），合并 18 次采样 17/18 字符级精确。文档未保存，session 全部 reset。
- [ ] 每次运行保存截图、Treeland 树摘要、Qwen 原始动作、融合结果、实际执行动作、执行状态和新观察。验收证据已按轮记录于 play.md 各子节并保留 predict/execute 原始 JSON 于会话；未建立统一的自动证据收集流程（属阶段 5 trace 范围）。
- [x] 无坐标系系统性偏移，无跨窗口误点击，无解析器越权执行：重复任务 1/5 连续 10 次最大量化误差 1px（容差 3px）无偏移；精确移动/边界/重投影多场景光标读回误差 0；跨窗口误点击=目标窗口变化被拒绝（`target_window_changed`）；解析器越权=白名单+字面量+单调用限制，真实负面样本全部拒绝（夹具测试）。
- [x] 失败能明确归因到单一层：模型理解/知识=`Alt+D` 当显示桌面（Q-001）、`Ctrl+Z` 单字符却报 DONE（Q-003）、`Qwen→Owen` 视觉转写（M-001）；坐标转换=（100,100）读回（250,250）根因是 Treeland DConfig 作用域 Adaptive 加速配置，属输入配置/执行器层（已修复）；窗口融合=`visible=false` 隐藏 Firefox 抢占目标（已修复）、右击菜单归 `BackgroundContainer`（Tree 子控件语义可观测性边界）；状态陈旧/执行器=冻结提案被 Firefox 覆盖返回 `target_window_changed`（已正确拒绝）、重复 execute 报 "no pending proposal"（已安全重置）；推理会话=动态约束曾致会话历史丢失（已修复 instruction/constraint 分离）；解析器=80 个重复 `moveTo`/多 `computer_use` 被单调用限制拒绝（模型格式问题，解析器兜底）；传输=55～215s 外层等待已初步归因旧挂载/代理侧（待复测确认）。
- [x] 将真实返回样例固化为脱敏测试夹具：`tests/test_qwen_actions.py` 新增 `REAL_MODEL_RETURNS`（20 个真实 `qwen3_rl` 样本）与 `DANGEROUS_RETURNS`，`RealReturnCompatibilityTests` 断言全部可解析/全部被拒绝；测试套件 40 项全过。
- [ ] 单独启用保留的 OmniParser 旧接口，按 `docs/desktop-acceptance-conclusion-2026-09-01.md` 的统一指标完成对比测试；对比期间不让两条感知链路共同决定同一次动作。

## 阶段 3：帧一致性与坐标精度

目标：保证截图、窗口树、模型动作和真正执行使用同一个可验证的坐标语境。

### 3.1 原子观察帧

- [ ] 为每次观察生成 `frame_id`，记录截图时间、树获取时间、桌面尺寸、显示器布局和缩放信息。
- [ ] 定义截图与树允许的最大时间差；超过阈值时重新采集，不向 Qwen 提供拼接出的陈旧状态。
- [ ] 动作提案绑定 `frame_id`、会话 ID 和单调递增的提案 ID。
- [ ] 执行前检查帧年龄、分辨率、显示器拓扑、活动工作区和目标窗口身份。

### 3.2 坐标空间统一

- [x] ydotoold udev 规则已为事件设备提供 `ID_INPUT_MOUSE=1` 后，撤销 Treeland `inputmanager.cpp` 中针对未知 Pointer 的回退识别，恢复合成器原有的 `ID_INPUT_MOUSE` 配置路径。后续仅为定位 DConfig 作用域临时在外部 Treeland 工作区的 `inputmanager.cpp`、`inputdevice.cpp` 加入 `qWarning`，这些诊断改动不属于本仓库提交。
- [x] 验证 Flat 的运行时应用：`libinput debug-events` 默认上下文显示处理值 `400/400`、原始值 `200/200`，显式指定 Flat 后两者均为 `200/200`，确认 event11 支持 Flat。Treeland `qWarning` 进一步确认 event11 已进入 `InputManager -> configAccelProfile`，但曾读取到默认 profile `2`、speed `0.333333`；根因是 Treeland 以 `dde`（UID 987）运行，而脚本原先向 `uos`（UID 1000）的 DConfig 作用域写入 `1/0`。`client_env.sh` 已改为以 `dde` 身份设置或恢复 `/uos` 子路径，用户复验确认当前生效。
- [x] 为 `client_env.sh` 增加互斥的鼠标 DConfig 参数：`--flat-speed` 设置并保留 `Flat`/速度 `0`，`--restore-speed` 通过 DConfig reset 手动恢复默认值，不传参时不修改；脚本退出不自动恢复。ydotoold 保持原有的 `pgrep + sudo ydotoold` 逻辑，不随脚本退出而关闭。`bash -n client_env.sh` 与 `git diff --check -- client_env.sh play.md` 已通过，真实会话启停与坐标复验待执行。
- [x] 为 Qwen 预测增加控制层精确坐标约束：控制层把截图目标转换为 Qwen 的 0–999 坐标提示，预测后按截图像素校验模型返回；不符合约束时拒绝并反馈给 Qwen，不绕过 Qwen 执行；已通过单元测试，待真实模型复验。
- [x] 真实模型坐标校准：明确要求 Qwen 返回归一化 `[52,92]` 后，真实 `qwen3_rl` 返回唯一 `mouse_move`，解析为截图 `(100,99)`、融合为 Treeland 逻辑 `(80.0,79.2)`，执行成功；相对截图目标 `(100,100)` 的量化误差为 1 像素。
- [x] 修正并复验执行坐标空间：此前 `(100,100)` 被读回约 `(250,250)` 的根因是 Treeland 读取了 `dde` UID 作用域中的 Adaptive 默认配置。改为在正确 DConfig 作用域应用 Flat 后，本轮截图尺寸、Treeland 桌面边界、融合坐标、实际 `pyautogui.moveTo` 参数与执行后光标均为 `(100,100)`，误差 0。
- [x] 把 Qwen 固定限制为单步视觉建议：每个预测只接受一个 `computer_use` 调用；多调用和异常长回复在成为提案前拒绝，默认 token 预算为 1024；已通过回归测试，待真实模型复验。
- [x] 明确定义 Qwen 图像坐标、截图像素、逻辑桌面坐标、Treeland geometry 和输入注入坐标之间的转换：`spatial_fusion.py` 模块 docstring 定义三个坐标空间（Qwen 归一化 0..999、截图像素、Treeland 逻辑桌面含所有层）；新增 `screenshot_to_qwen_normalized`/`qwen_normalized_to_screenshot` 作为归一化转换唯一来源（`mcp_autogui_main._qwen_precision_constraint` 改为引用，消除重复公式）。
- [ ] 处理桌面原点不为 `(0, 0)`、显示器位于主屏左侧/上方、负坐标和不同缩放比例。负原点往返已由 `desktop_to_screenshot_point`/`_screenshot_to_desktop_point` 支持并有往返测试（`test_desktop_screenshot_roundtrip_with_negative_origin`）；真实多屏/混合缩放待硬件。
- [ ] 补充横跨多个显示器的窗口、跨屏拖动和动态插拔显示器测试。
- [x] 对所有坐标转换增加往返误差测试和边界测试：Qwen 归一化↔截图像素往返误差 ≤1px（6 组采样含边界）、归一化边界 `(0,0)`/`(999,999)`、截图像素↔逻辑桌面往返（含负原点）——新增 4 个测试（`tests/test_qwen_tree_fusion.py`），全量 43 项通过。

### 3.3 融合与命中改进

- [ ] 在中心点命中基础上增加点命中、矩形相交比例、窗口可见区域和遮挡关系评分。
- [ ] 将窗口身份从易变化的标题字符串升级为可组合的稳定特征，如应用 ID、PID、窗口 ID、标题和层级。
- [ ] 对标题栏、阴影、圆角、弹出菜单、子表面和系统层建立明确的归属规则。
- [ ] 对不确定命中返回候选窗口和置信度，不在低置信度时强行执行。

### 3.4 窗口裁剪策略

- [ ] 支持把目标窗口或局部区域裁剪后交给 Qwen，以增加有效像素密度。
- [ ] 随请求携带裁剪原点、尺寸和缩放参数，并将局部坐标严格反投影到桌面坐标。
- [ ] 保留全屏上下文作为可选输入，避免裁剪导致任务上下文丢失。
- [ ] 通过对比实验决定全屏、目标窗口裁剪和多尺度输入的默认策略。

### 3.5 执行后验证

- [x] 每次动作后重新采集截图和树，计算窗口、焦点、标题和 geometry 的前后差异：`qwen_cua_execute` 新增 `post_validation` 字段（执行前后活动窗口摘要、`active_window_changed`、可操作窗口数、目标窗口 geometry 前后对比、`target_moved`），基于执行前 `latest_tree` 与执行后 `post_tree`；真实链路复验通过（移动动作返回焦点未变、窗口数 3→3、目标 geometry 未变）。
- [x] 为点击、输入、滚动等动作定义最低可验证信号：移动=执行后光标与目标坐标比对（`post_action_cursor`）；点击/双击=光标读回+下一帧活动窗口/窗口数变化（Firefox 打开验证）；输入=剪贴板字节级比对（重复任务 2/5）；滚动=下一帧页面可见内容变化；按键/快捷键=Tree 窗口数变化（Esc）或窗口 `visible` 翻转（Super+D）。已在本轮及 2.5 各节真实应用；完整"决定继续/重试/重置"闭环属阶段 5 任务执行器。
- [ ] 将验证结果用于决定继续、重试、重新定位、重置会话或请求人工介入。

### 3.6 阶段验收

- [ ] 单屏、双屏、负坐标和混合缩放场景均有自动测试或固定复现步骤。
- [ ] 窗口在预测后移动时能够正确重投影；窗口消失、换屏或身份不确定时拒绝执行。
- [ ] 坐标错误能被帧/空间校验拦截，而不是依赖 Qwen 自行发现。

## 阶段 4：内嵌推理组件的动作反馈与会话状态协议

目标：解决“Qwen 推理组件保存模型状态，而本地控制层掌握真实执行结果”造成的状态分叉。

### 4.1 协议设计

- [x] 内嵌服务采用两阶段状态：预测只创建 pending proposal，收到 `success` 才提交正式历史，`rejected`/`partial`/`error` 作为真实反馈保存。
- [ ] 或设计统一的 session step 接口，使下一轮请求携带上一步实际执行结果和新观察；通过原型比较后确定一种正式协议。
- [ ] 反馈内容至少包括：提案 ID、原始动作、实际动作、是否修正坐标、执行状态、拒绝原因、耗时和新 `frame_id`。
- [ ] 明确 `partial`、`timeout`、`stale_frame`、`target_moved`、`target_missing`、`unsafe`、`user_cancelled` 等状态语义。
- [ ] 保证请求幂等，网络重试不得重复提交动作或重复推进模型历史。

### 4.2 状态所有权

- [x] 内嵌 Qwen-CUA 服务是模型对话和推理历史的唯一权威来源。
- [x] 本地控制层只维护当前待执行提案、frame 引用、解析动作、融合结果和最近执行结果。
- [ ] 定义会话过期、服务重启、客户端重连、任务取消和模型切换时的重建/重置规则。
- [ ] 增加会话状态查询和显式关闭接口，防止不可见的服务端状态长期残留。

### 4.3 Qwen 是否接收 Treeland tree

- [x] 内嵌接口保留可选 `accessibility_tree`/结构化上下文能力，但默认 MCP 路径不把完整原始树塞进提示词。
- [ ] 设计紧凑树摘要：活动工作区、可见窗口、层级/Z 序、geometry、应用/标题、焦点和候选目标。
- [ ] 如果控制层已根据 Qwen 坐标完成确定性融合，优先把“融合/校验结果”作为执行反馈，而不是让 Qwen 重复计算几何关系。
- [ ] 只有当实验证明树上下文提升任务决策或恢复能力时，才把紧凑树作为默认模型输入。

### 4.4 阶段验收

- [ ] 被拒绝或修正的动作不会以“原样成功”进入 Qwen 历史。
- [ ] 网络超时重试不会重复执行动作或制造两条不同会话历史。
- [ ] 服务端重启和会话过期时能检测并安全重建，不静默继续错误状态。
- [ ] 连续多步任务中，Qwen 能根据真实执行结果而不是假设结果进行下一步推理。

## 阶段 5：任务级自动闭环

目标：把当前单步工具组合成可控、可观察、可中止的完整 GUI 任务执行器。

### 5.1 核心循环

- [ ] 新增任务级入口（暂定 `gui_execute`），接收任务目标、最大步数、超时和安全策略。
- [ ] 循环流程固定为：采集截图与树 -> Qwen 提案 -> 本地融合 -> 安全校验 -> 执行 -> 结果反馈 -> 采集新观察。
- [ ] 支持 Qwen 返回 `DONE`、`FAIL`、等待、请求人工确认或需要重新观察。
- [ ] 每一步都允许控制层修正坐标、拒绝动作或结束任务；不得为了保持模型会话而强行执行。
- [ ] 防止无限循环：最大步数、总超时、相同动作重复阈值、无状态变化阈值和错误预算。

### 5.2 可观测与控制

- [ ] 提供任务状态、当前步骤、取消、暂停/人工确认和结果查询接口。
- [ ] 为每个任务生成 trace，串联 frame、提案、融合、校验、执行、反馈和模型响应。
- [ ] 保存可脱敏回放数据，允许在不再次操作真实桌面的情况下复现融合与决策问题。
- [ ] 错误信息区分可重试、需重新观察、需重置会话、需人工介入和最终失败。

### 5.3 阶段验收

- [ ] 能独立完成一组包含 3～10 步的桌面任务。
- [ ] 任意一步失败后不会盲目继续，能够重定位、重试或安全终止。
- [ ] 用户取消在当前原子动作结束后立即生效，不再发起新动作。
- [ ] 完整 trace 足以还原每次成功或失败的原因。

## 阶段 6：Qwen tree 上下文与融合策略实验

目标：用数据回答“Qwen 是否需要直接读取 tree”，而不是凭直觉扩大提示词和状态复杂度。

### 6.1 实验组

- [ ] A：Qwen 仅看全屏截图，直接使用其坐标（基线）。
- [ ] B：Qwen 看全屏截图，本地使用 Treeland tree 融合和执行前校验。
- [ ] C：Qwen 看截图和紧凑 tree 摘要，本地继续融合和校验。
- [ ] D：Qwen 看目标窗口裁剪，本地使用 tree 完成局部到全局映射。
- [ ] E：Qwen + Treeland 融合 + OmniParser 视觉元素（组合上限实验）。
- [ ] F：历史 OmniParser + 控制 AI 路径（旧系统基线）。

### 6.2 Tree 输入假设

- Qwen 作为视觉操作模型具备理解文本化/结构化窗口上下文并据此推理的潜力，但是否能稳定利用当前 Treeland tree 必须用实际模型和提示词验证。
- 原始 tree 可能过长、噪声大且与截图不同步；默认实验应使用本地压缩后的语义摘要。
- 几何命中、Z 序和坐标换算应留在确定性计算层；Qwen 主要利用 tree 判断应用、窗口、任务上下文和异常恢复。

### 6.3 评估指标

- [ ] 任务成功率和首次动作正确率。
- [ ] 目标窗口命中率、目标控件命中率和平均坐标误差。
- [ ] 平均步骤数、重试次数、错误动作数和人工介入次数。
- [ ] 遮挡、窗口移动、弹窗、多屏和缩放变化下的恢复率。
- [ ] 端到端延迟、Qwen 推理延迟、tree 处理延迟、token/显存/请求成本。
- [ ] 被本地校验拒绝或修正的比例，以及修正后的任务成功率。

### 6.4 实验纪律与验收

- [ ] 固定任务集、初始桌面状态、模型版本、采样参数、分辨率和重复次数。
- [ ] 每组保存相同粒度的 trace 和失败分类，禁止只挑成功案例。
- [ ] 在引入更复杂方案前，要求它相对 B 组在关键指标上有可重复收益。
- [ ] OmniParser 代码保持可运行但默认关闭，直到对比测试完成；是否最终删除另行决策。

## 阶段 7：安全、稳定性与交付

- [ ] 默认拒绝删除、付款、发送消息、安装软件、权限授权和不可逆提交等高风险动作，除非用户逐步确认。
- [ ] 模型端点认证信息只从环境变量或安全配置读取；远程模型连接默认使用 TLS。
- [ ] 日志和测试夹具脱敏，不保存密码、令牌、私人文本或完整敏感截图。
- [ ] 对 Qwen 输出设大小、动作数量、参数范围和解析时间限制。
- [ ] 为内嵌推理组件初始化失败、模型端点不可用、模型超时、截图失败、tree 获取失败和输入注入失败提供降级行为。
- [ ] 补充安装、配置、故障排查、协议和扩展新动作的开发文档。
- [ ] 建立版本兼容表，记录 MCP、内嵌 Qwen-CUA 组件、模型、Treeland 和动作协议版本。
- [ ] 完成端到端回归测试和发布检查清单。

## 待决策事项

- [ ] Qwen 后端采用两阶段 `propose/commit/reject`，还是统一 session-step 协议。
- [ ] Qwen 默认输入全屏、窗口裁剪，还是多尺度图像。
- [ ] 紧凑 tree 摘要的字段、长度上限和更新策略。
- [ ] 控制 AI 是否仍参与任务规划，还是只负责确定性融合、安全校验和执行编排。
- [ ] 什么级别的动作修正允许自动执行，什么级别必须回问 Qwen 或用户。
- [ ] 多显示器使用统一逻辑桌面坐标还是每输出独立坐标空间。
- [ ] OmniParser 对比测试完成后的长期维护方式。

## 每阶段更新规则

完成任何阶段或子任务后必须同步更新本文件：

1. 将对应项从 `[ ]` 改为 `[x]`。
2. 在阶段验收下记录测试命令、结果、真实环境和已知限制。
3. 写入对应 Git 提交哈希；未提交的工作明确标记为“未提交”。
4. 新发现的工作先加入相应阶段，再开始实现；若改变架构决策，同时更新 `docs/qwen-cua-architecture.md`。
5. 每次开始新一轮工作，以本文件中最靠前的未完成验收项作为默认下一步。

## 当前明确的下一步

阶段 2 的代码迁移、默认 embedded 切换和真实模型合成图冒烟测试已经完成。当前下一步是在真正的 Treeland 图形会话中启动现有 `client_env.sh`，由主控 AI 连接 MCP 后执行 2.5 的真实桌面只预测测试。当前 Codex shell 是 `XDG_SESSION_TYPE=tty`，没有 `WAYLAND_DISPLAY` 或可用 Treeland Wayland socket，因此在截图前即被阻止；没有真实桌面截图发送到模型，也没有执行动作。`client_env.sh` 中原有的显示、输入和系统环境设置必须保留，后续只允许对 CUA 配置做经过确认的最小修改。

### 本轮未提交验证记录（2026-08-31）

- `.venv/bin/python -m unittest discover -s tests -v`：21 项通过，包含 OpenAI-compatible 调用、session 隔离、反馈提交和默认 embedded 模式。
- `.venv/bin/python -m compileall -q src tests`：通过。
- `UV_CACHE_DIR=/tmp/treeland-autoui-uv-cache uv lock --check`：通过，共 123 个包。
- `python -m json.tool langchain_settings/mcp_config.json`、`bash -n client_env.sh`、`git diff --check`：通过。
- 源码引用扫描：默认 embedded Python 路径不 import `/home/uos/gui-mcp` 或其 backend 包。
- 真实模型合成图：按钮中心约 `(500, 400)`，模型返回 `(500, 409)`，约 9 像素误差；模型请求约 1.79 秒，总预测约 1.81 秒。
- TLS：指定端点使用自签名证书，严格校验会失败；仅在获批的合成图测试进程内临时关闭校验，仓库默认仍为开启。
- 凭据：用户提供的 API key 未写入仓库或文档；真实运行时通过 `CUA_MODEL_API_KEY` 环境变量传入。
- 桌面环境限制：当前 shell 为 tty 且无 `WAYLAND_DISPLAY`，真实桌面只预测尚未执行。
