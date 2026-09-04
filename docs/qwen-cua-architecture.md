# Qwen-CUA 与 Treeland 协同架构设计（v1 历史记录）

> 当前对外接口已改为 `gui_run`；`qwen_cua_predict`、`qwen_cua_execute`、
> `qwen_cua_reset` 和 `qwen_cua_status` 均已删除。本文件保留旧两阶段接口的
> 背景、验收记录和后端设计，不可作为当前 MCP 调用手册。当前实现以
> `treeland-autoui-mcp-v2-implementation.md` 与 v2 设计为准。

状态：客户端、融合链路、内嵌后端和真实模型合成图联调已完成，等待 Treeland 桌面联调
目标：提高 AI 操作桌面的准确度、精确度和可验证性

## 1. 背景

当前 `treeland-autoui-mcp` 使用 OmniParser 分析截图。OmniParser 返回 UI 元素列表、元素边界框和标注截图，上层控制 AI 根据元素 ID 调用点击、拖拽和输入等 MCP 工具。

计划引入 `/home/uos/gui-mcp` 中的 Qwen-CUA。Qwen-CUA 与 OmniParser 的定位不同：

- OmniParser 是界面元素检测器，输出元素和边界框。
- Qwen-CUA 是 GUI 动作模型，输出下一步操作及坐标。
- Treeland window tree 提供窗口几何、层级、可见性、活动状态和工作区等确定性信息。

因此，本次演进不应只是替换 HTTP 接口。`gui-mcp` 中的 Qwen-CUA 后端代码将直接迁入 `treeland-autoui-mcp`，成为项目内部的视觉推理与会话状态组件；MCP 控制层负责采集环境，把 Qwen 动作坐标与 Treeland tree 融合，并提供确定性校验和执行能力。外层控制 AI 可以查看融合后的动作步骤并决定是否执行，但不复制 Qwen 的截图、messages 和内部推理历史。

## 2. 设计目标

1. 提高窗口选择、坐标定位和多轮操作的准确度。
2. 让项目内嵌的 Qwen-CUA 组件维护唯一的 GUI 推理会话和实际动作历史，避免外层控制 AI 复制状态。
3. 使用 Treeland tree 校验动作坐标实际命中的窗口、层级、可见性和遮挡关系。
4. 支持全屏识别后对目标窗口进行局部裁剪和二次推理，提高控件级定位精度。
5. 保留 OmniParser 旧接口用于后续基准和对比实验，但默认不启用。
6. 保证每次预测、坐标转换、执行和结果验证都可追踪。

## 3. 非目标

- Treeland tree 不负责识别窗口内部的按钮、输入框或菜单项。
- 不迁移 `gui-mcp` 中与 Qwen-CUA 无关的其他 agent、历史运行数据和缓存产物。
- 第一阶段不执行模型返回的任意 Python 字符串。
- 第一阶段不要求迁移 `gui-mcp` 的全部日志、trajectory、rollout 和 checkpoint 基础设施。

## 4. 目标架构

```text
外层控制 AI
  │ 委派任务并接收融合后的 Qwen 步骤
  ▼
treeland-autoui-mcp（本机传感器与执行器）
  ├── 捕获截图
  ├── treeland-debug --tree
  ├── 构造 screen frame
  └── 将观察和上一步实际执行结果发送给后端
        │
        ▼
treeland-autoui-mcp 内嵌 Qwen-CUA 组件（唯一推理与会话状态中心）
  ├── 理解截图并生成动作步骤
  ├── 保存真实动作与结果历史
  ├── 选择目标窗口和下一步动作
  ├── 预测预期界面变化
  └── 判断 continue / DONE / FAIL
        │
        ▼
本机 Tree Fusion、确定性校验与执行
  ├── 坐标命中的顶层窗口
  ├── layer / z / visible / active
  ├── frame 时效、坐标边界和动作白名单
  ├── 把融合结果返回控制 AI
  ├── 按控制 AI 选择执行动作
  └── 获取新截图和新 tree，反馈给同一 Qwen session
```

Qwen-CUA 后端逻辑直接作为本项目的独立 Python 包迁入，默认由 MCP 进程内的 `QwenCUAService` 调用并持有每个 GUI run 的推理状态，不要求另外启动 `gui-mcp`。可选保留 HTTP 适配模式用于进程隔离、远程部署和对比测试，但它不是默认运行依赖。实际 Qwen 模型可以通过 OpenAI-compatible 模型端点提供推理；该模型端点与原 `gui-mcp` 中间后端不是同一层。

控制/融合层只保存 `session_id`、当前 frame、待执行动作和瞬时执行结果；Qwen-CUA 组件保存 messages、必要的截图历史和推理历史。两部分即使同属一个仓库和进程，也必须通过明确接口交换“提案”和“实际执行结果”，避免状态隐式耦合。

## 5. 核心原则

### 5.1 推理与物理执行分离

Qwen-CUA 负责逻辑推理和多轮规划，但内嵌推理组件也不能直接假设建议动作已经在桌面上成功发生。单步响应产生结构化动作，控制/融合层在执行前进行确定性检查：

- 动作类型和参数；
- 模型建议的坐标；
- 坐标对应的 Treeland 窗口；
- 坐标是否落入可见的顶层窗口；
- 截图和 window tree 是否仍然有效；
- 是否需要重新截图或进行窗口局部二次推理。

融合结果先返回控制 AI。控制 AI 可以选择执行全部或部分动作，也可以拒绝并要求同一 Qwen session 根据新截图重新预测。校验失败时不得执行可疑动作。内嵌服务将提案保持为 pending，收到本地 `success` 才提交正式历史；拒绝、部分执行和错误作为结构化反馈交给同一 session。旧 HTTP 兼容模式无法接收反馈时才重置 session。

### 5.2 结构化动作

内部统一使用结构化动作，例如：

```json
{
  "type": "left_click",
  "coordinate": {"x": 742, "y": 516},
  "description": "点击控制中心中的显示选项"
}
```

执行器只允许明确的动作集合：

- 点击、双击、右键和鼠标移动；
- 拖拽和滚动；
- 按键、组合键和文本输入；
- 等待；
- `DONE` 和 `FAIL`。

禁止将模型输出直接传给 `eval`、`exec` 或不受限的 `python -c`。

### 5.3 Qwen 后端拥有唯一的真实会话状态

Qwen-CUA 的历史必须以本机反馈的“实际执行动作和执行结果”为准。动作被校验器拒绝、执行失败或被确定性逻辑调整时，后端不能继续假设原始输出已经成功执行。

推荐将内部服务接口设计成有状态的逐步会话：

```text
service.start(...)
service.step(session_id, frame, previous_execution)
service.reset(session_id)
```

每个 `step` 调用携带当前截图、Treeland 上下文和上一次实际执行结果。组件根据自己的已提交历史生成下一步，并只把控制层确认的实际动作写入正式历史。外层控制 AI 不维护 Qwen 的历史截图、responses、messages 或裁剪策略。可选 HTTP 模式只是这个 Python 接口的传输适配，不另定义状态语义。

如果第一阶段仍使用现有 `/predict`，本机至少要在下一轮发送上一步实际执行结果；动作被拒绝而后端又无法修正历史时，应重置该 run 的 Qwen session，避免状态静默分叉。

## 6. Screen frame 与坐标系统

一次观察应生成不可变的 `frame_id`，绑定以下信息：

```json
{
  "frame_id": "frame-...",
  "captured_at": "...",
  "screenshot_size": {"width": 1920, "height": 1080},
  "desktop_origin": {"x": 0, "y": 0},
  "crop": null,
  "scale": {"x": 1.0, "y": 1.0},
  "treeland_tree": {},
  "treeland_tree_captured_at": "..."
}
```

每个模型坐标都要保留坐标来源和转换过程：

```text
模型输入坐标
  → 原始截图坐标
  → 目标屏幕坐标
  → 桌面全局坐标
  → 窗口内相对坐标
```

执行前应检查：

- frame 是否超过允许时效；
- 目标窗口是否仍存在；
- 窗口 geometry 是否变化；
- 当前坐标命中的顶层窗口是否仍与预测时一致；
- 多屏桌面的原点和缩放是否一致。

窗口移动后，如果目标窗口身份仍可确认，可以根据保存的窗口内相对坐标重新投影；无法确认时必须重新观察。

## 7. Treeland 坐标上下文

对每个包含坐标的动作提议，生成如下增强信息：

```json
{
  "proposal": {
    "type": "left_click",
    "coordinate": {"x": 742, "y": 516}
  },
  "coordinate_context": {
    "window_id": 2,
    "appId": "dde-control-center",
    "title": "控制中心",
    "geometry": {"x": 320, "y": 120, "width": 1280, "height": 820},
    "window_relative_coordinate": {"x": 422, "y": 396},
    "visible": true,
    "active": true,
    "covered": false,
    "layer": 1,
    "z": 3
  }
}
```

Treeland tree 可以提高以下方面的可靠性：

- 验证坐标实际命中的窗口；
- 检测错误应用、桌面或遮挡窗口；
- 根据 layer 和 z 推断最终接收点击的窗口；
- 在窗口移动后重新计算全局坐标；
- 验证窗口激活、可见性和工作区状态；
- 辅助多屏坐标映射。

Treeland tree 不能判断窗口内部具体控件，因此控件级定位仍依赖 Qwen-CUA、OmniParser、OCR、无障碍树或其他视觉感知能力。

### 7.1 提供给 Qwen 的上下文

不应把未经处理的完整 tree 无限制塞入 prompt。应生成稳定、紧凑、带坐标说明的 `desktop_context`：

```json
{
  "frame_id": "frame-001",
  "screen": {"width": 1920, "height": 1080, "coordinate_space": "desktop_pixels"},
  "cursor": {"x": 800, "y": 500},
  "windows_front_to_back": [
    {
      "id": 2,
      "appId": "dde-control-center",
      "title": "控制中心",
      "geometry": {"x": 320, "y": 120, "width": 1280, "height": 820},
      "active": true,
      "visible": true,
      "layer": 1,
      "z": 3
    }
  ]
}
```

Qwen 负责语义推理，例如选择目标应用、判断是否需要激活窗口、规划下一步和判断任务是否完成。程序代码负责精确计算，例如点命中测试、遮挡关系、坐标变换、边界检查和窗口移动后的重投影。不要要求模型替代这些可确定计算。

Qwen 的结构化输出建议同时包含 `target_window_id`、动作、坐标和预期结果。本机校验发现 `target_window_id` 与坐标实际命中的顶层窗口不一致时，应把结构化错误反馈给 Qwen，而不是执行可疑动作。

## 8. 窗口局部二次推理

为了提高窗口内部控件的坐标精度，建议提供两阶段定位：

1. Qwen-CUA 查看全屏，确定目标窗口或大致区域。
2. 根据 Treeland geometry 裁剪目标窗口。
3. 将高分辨率窗口截图再次发送给 Qwen-CUA。
4. 获得窗口局部坐标。
5. 将局部坐标转换成桌面全局坐标。
6. 执行前再次确认窗口 geometry。

```text
desktop_x = current_window_x + local_x
desktop_y = current_window_y + local_y
```

如果裁剪图经过缩放，必须先将模型坐标还原到原始窗口坐标。窗口边框、标题栏和内容区偏移也需要纳入转换元数据。

## 9. 对外 MCP 与内嵌服务协议

### 9.1 第一阶段主接口 `qwen_cua_predict`

```text
qwen_cua_predict(
    instruction,
    session_id="",
    reset=false,
    expected_active_app_id="",
    application_wait_timeout_s=3.0
)
```

该接口完成一次截图、一次 Qwen 预测和一次 Tree Fusion，但不执行动作。返回：

- `session_id` 和 `frame_id`；
- Qwen 原始观察和动作说明；
- 经过白名单解析的动作；
- 动作的桌面坐标、目标窗口和窗口内相对坐标；
- Treeland 命中和窗口一致性校验结果；
- 当前截图。

### 9.2 第一阶段执行接口 `qwen_cua_execute`

```text
qwen_cua_execute(session_id, action_indexes=null)
```

执行前重新获取 Treeland tree。如果坐标当前命中的窗口身份与预测时不同，则拒绝动作；窗口仍是同一个但位置发生变化时，使用窗口内相对坐标重新投影。`action_indexes` 允许控制 AI 选择全部或部分融合动作。

默认 embedded 服务在预测时只保存 pending proposal。`qwen_cua_execute` 将实际执行结果以 `success`、`partial`、`rejected` 或 `error` 反馈给服务，其中只有 `success` 提交为已完成历史，其余状态作为下一轮纠错上下文。旧 HTTP 兼容后端仍采用保守策略：无法提交真实反馈时，部分执行、拒绝或失败会触发下一轮 reset。

如果 session 设置了 `expected_active_app_id`，执行后会在限定时间内轮询轻量 Tree，结束时采集一次最终截图和 Tree，并用活动窗口 `appId` 形成轻量 `task_validation`。匹配时返回 `task_completed=true`；错误应用、没有可观察变化或模型提前返回 `DONE` 时返回 `partial`，将预期值、实际值和失败原因反馈给同一 session。该机制只验证窗口级完成条件，不推断桌面图标或窗口内部控件语义。

### 9.3 会话和诊断接口

以下接口用于调试、实验和人工诊断，不是默认主链路：

- `qwen_cua_reset`：重置后端 session 并丢弃本机待执行 frame；
- `qwen_cua_status`：返回后端健康状态和本机待处理 session；
- 后续可增加 `gui_trace_get`，返回 Qwen 输出、实际动作、校验结果和操作后观察。

### 9.4 后续任务级闭环

两阶段接口稳定后，可以增加：

```text
gui_execute(instruction, max_steps, execute=true)
```

它在内部重复预测、融合、校验、执行和重新观察，作为 Qwen-CUA 自动执行基线。两阶段接口仍保留给控制 AI 审核和对比实验。

### 9.5 Qwen 内嵌服务会话接口

```text
QwenCUAService.start(...)
QwenCUAService.step(session_id, frame, previous_execution)
QwenCUAService.reset(session_id)
QwenCUAService.status(session_id)
QwenCUAService.close()
```

`step` 请求示例：

```json
{
  "instruction": "打开显示设置并调整缩放",
  "frame": {
    "frame_id": "frame-002",
    "screenshot": "...",
    "desktop_context": {}
  },
  "previous_execution": {
    "requested_action": {},
    "executed_action": {},
    "status": "success",
    "validation": {}
  }
}
```

后端响应示例：

```json
{
  "session_id": "session-123",
  "status": "continue",
  "observation": "控制中心已经打开，当前位于首页",
  "target_window_id": 2,
  "actions": [
    {"type": "left_click", "coordinate": {"x": 742, "y": 516}}
  ],
  "expected_result": "进入显示设置页面"
}
```

内嵌 Qwen-CUA 组件保存任务、视觉历史、Treeland 上下文摘要、模型响应、实际动作、执行结果和当前 step。控制/融合层只保存当前 run 的引用和短生命周期 frame。

### 9.6 Treeland 窗口工具

可以保留并去除 OmniParser 命名：

- `gui_click_window_region`；
- `gui_drag_window_region`；
- `gui_scroll`；
- `gui_write`；
- `gui_input_key`；
- `gui_wait`。

窗口区域工具应在每次操作时读取或校验最新 Treeland tree，不依赖 OmniParser 融合结果。

## 10. OmniParser 兼容策略

OmniParser 代码作为实验性旧能力保留，但默认关闭：

```text
GUI_OMNIPARSER_ENABLED=0
OMNI_PARSER_SERVER=...
```

要求：

- 禁用时，服务启动不要求 `OMNI_PARSER_SERVER`；
- 禁用时，不把 OmniParser 放入默认操作链路；
- 旧的 `omniparser_*` 接口可以按配置注册；
- 保持旧结果格式，便于复现实验；
- 新架构不得依赖全局 `detail` 或 `fused_detail` 状态。

后续也可以将 OmniParser 作为可选的 grounding provider，与 Qwen-CUA 坐标进行交叉验证，而不只是保留旧接口。

## 11. 安全与配置

1. Qwen 模型端点地址、API key 和 TLS 配置全部来自环境变量；默认不配置或连接 `gui-mcp` 后端地址。
2. 不复制 `gui-mcp` 中硬编码的地址或凭据。
3. TLS 证书校验默认开启，仅允许通过显式配置关闭。
4. 模型动作经过结构化解析和白名单校验。
5. 对过期 frame、窗口身份变化和坐标越界默认拒绝执行。
6. 登录、密码、验证码、隐私授权和敏感确认应暂停并交给用户。
7. 日志记录动作和坐标，但不得记录密码、API key 或剪贴板敏感内容。

## 12. 对比实验矩阵

| 模式 | 用途 |
|---|---|
| OmniParser + 控制 AI | 当前能力基线 |
| Qwen-CUA + screenshot | 纯视觉 Qwen-CUA 基线 |
| Qwen-CUA + raw Treeland tree | 验证模型直接读取原始 tree 的效果 |
| Qwen-CUA + compact desktop context | 衡量规范化窗口上下文的增益 |
| Qwen + Treeland 确定性校验 | 衡量窗口命中检查和错误反馈的增益 |
| Qwen + Treeland + 窗口裁剪 | 衡量局部二次推理带来的增益 |
| Qwen + Treeland + OmniParser | 衡量多感知源交叉验证的增益 |

建议至少记录：

- 任务成功率；
- 首次动作正确率；
- 坐标命中目标窗口的比例；
- 控件点击成功率；
- 平均重试次数；
- 平均执行步数；
- 误操作次数；
- 推理和执行延迟；
- 确定性校验拒绝 Qwen 动作的比例；
- Qwen 收到结构化错误后自主修正成功的比例。

测试集应固定初始桌面状态、分辨率、窗口布局和任务描述，并保存每一步截图、tree、模型输出、原始提议、实际动作和结果，确保不同模式可复现比较。

## 13. 分阶段实施建议

具体任务、完成状态和验收清单统一维护在 `play.md`，本节只保留架构阶段边界。

### 阶段一：客户端与融合链路（已完成）

- OmniParser 默认关闭但保留旧接口。
- 已实现 `qwen_cua_predict`、`qwen_cua_execute`、安全动作解析与 Treeland 坐标融合。
- `QwenBackendClient` 已成为 embedded/HTTP 模式选择适配层。

### 阶段二：将 Qwen-CUA 后端迁入本项目（代码迁移已完成）

- 已从 `~/gui-mcp/cua_mcp_backend` 提取并重构 Qwen S2 的最小必需能力，没有迁移其他 agent、历史日志、trajectory 图片和缓存。
- 已建立独立 `qwen_cua_backend` 包和 `QwenCUAService` Python 接口。
- 默认使用进程内 `embedded` 模式；HTTP 仅作为可选隔离/远程适配模式。
- 默认路径不再依赖 `CUA_BACKEND_URL` 和 `~/gui-mcp/` 运行实例。
- 固定模型响应、图像尺寸、坐标转换和会话状态测试已建立；迁移前后完整样例对照仍待补充。
- 下一步连接实际 Qwen 模型端点进行只预测与低风险执行联调。

### 阶段三：提高坐标精度与状态一致性

- 实现目标窗口裁剪和坐标反投影。
- 增加 frame 时效、窗口移动检测、动作前后状态 diff 和多屏映射测试。
- 让内嵌 Qwen-CUA 历史显式记录控制层反馈的实际动作和结果。

### 阶段四：任务闭环、实验与增强

- 实现任务级 `gui_execute` 自动循环基线。
- 将紧凑的 `desktop_context` 加入 Qwen prompt，测试模型直接使用 tree 的增益。
- 建立对比实验数据集和指标采集，可选启用 OmniParser 交叉验证。
- 评估多次 rollout、坐标投票、原始 tree、紧凑上下文和窗口裁剪的实际收益。

## 14. Qwen 使用 Treeland tree 的能力假设

Qwen-VL/CUA 类模型具备同时理解图像、文本和结构化 JSON 的基础能力，也具备视觉定位和 GUI 动作推理能力，因此可以把 Treeland tree 作为截图之外的辅助观察。但这是一项需要实验验证的工程假设，不应直接视为当前模型已经稳定具备的能力。

当前 `/home/uos/gui-mcp` 后端虽然能够在请求中接收 `accessibility_tree`，但前端 `BackendClient.predict()` 没有发送该字段，`CUAAgent` 构造 S2 messages 时也只加入了截图、instruction 和动作历史。因此，当前实现实际上不会利用 tree，必须同时修改请求构造和 Qwen 消息构造逻辑。

预期 Qwen 比较适合利用 tree 完成：

- 根据 `appId`、title、active 和 visible 选择目标窗口；
- 结合截图判断哪个窗口符合任务语义；
- 规划激活、切换、移动窗口等窗口级动作；
- 根据结构化校验错误重新选择动作；
- 使用目标窗口 geometry 辅助局部视觉定位。

不应完全依赖 Qwen 完成：

- 精确点命中和遮挡计算；
- 多次缩放、裁剪后的坐标换算；
- 多屏负坐标和缩放比例转换；
- 判断 frame 是否过期；
- 白名单、安全边界和参数合法性检查。

这些任务由确定性代码计算，再把结果作为事实交给 Qwen。模型主要负责语义理解和策略决策。

输入 tree 时应遵守：

1. 只发送当前可见、可操作和与任务相关的窗口摘要。
2. 明确数组顺序是前到后还是后到前。
3. 明确坐标单位、原点、屏幕范围和 crop/scale。
4. 为窗口分配当前 frame 内稳定的短 ID。
5. 避免把重复、不可见或无面积节点塞进 prompt。
6. 对长 title 和无关字段做裁剪，控制 token 数量。
7. 同时保留截图；tree 是补充事实，不是视觉信息的替代品。

实施前先用固定任务集验证四种输入：纯截图、截图加原始 tree、截图加紧凑上下文、截图加紧凑上下文和确定性校验。只有数据证明 tree 能稳定提高成功率后，才扩大其在 prompt 中的占比。若当前 `qwen3_rl` 对新字段利用不稳定，再考虑 prompt 微调、少量示例或针对该输入格式继续训练。

## 15. 待确认事项

1. 内嵌 `QwenCUAService` 使用 `propose/commit/reject`，还是统一的 `step(previous_execution)` 语义；HTTP 适配层跟随同一接口。
2. 首版是否需要支持多屏，还是先限定单个目标屏幕。
3. OmniParser 旧工具在禁用时是完全不注册，还是注册后返回“能力未启用”。
4. 是否保留当前工具名称以兼容现有调用方，或提供一段过渡期别名。
5. 实验数据和截图保存周期，以及其中可能包含的隐私信息处理规则。
6. Qwen prompt 中保留多少轮 tree，上轮 tree 是否只保存 diff 或摘要。
