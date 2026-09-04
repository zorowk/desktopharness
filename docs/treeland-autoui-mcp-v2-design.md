# Treeland AutoUI MCP v2：跨合成器设计提案

## 1. 目标与适用范围

本设计用于重新划分主控、MCP 确定性逻辑层和 Qwen-CUA 图形识别层的职责，使三者能够协作完成安全、可验证的桌面操作。v2 的核心协议不绑定 Treeland：任何能提供窗口树和光标位置的合成器都可以接入；Treeland 只是第一个适配器实现。

本文描述目标架构，不表示相关接口已经全部实现。现有 `qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset` 和 `qwen_cua_status` 在迁移期间继续保留。

需要解决的主要问题：

1. 主控可能误用窗口树信息，例如将某个桌面容器理解为“没有可点击控件”。
2. Qwen 的自然语言观察、动作提案和完成判断容易被混为界面事实。
3. MCP 可以确定窗口、坐标、遮挡和执行状态，但不能据此判断窗口内部控件语义和业务结果。
4. Qwen 的历史动作、自述状态、当前截图和真实执行结果可能发生分歧。
5. 模型 `DONE`、动作执行成功和任务验收通过尚未完全分离。

核心原则：

> Qwen 提出它认为正确的动作；合成器适配器只提供窗口级空间事实；主控提供任务目标、风险策略和断言；不同 Evidence Provider 只采集证据，Assertion Evaluator 根据证据判断断言，Task State Reducer 决定任务状态。

### 1.1 架构宪法：小核心、大扩展、克制通信

以下规则是 v2 的核心设计，后续实现和功能扩充不得绕过：

1. 核心协议只依赖自己的 Canonical Model，不依赖 Treeland、Deepin、Qwen、PyAutoGUI、`dde-am` 或其他具体实现。
2. 合成器提供的原始数据必须经过 Adapter 映射和过滤；只有核心定义的有限标准字段可以进入正常链路。
3. 合成器的额外字段默认丢弃。只对诊断有价值的完整原始数据保存为 `raw_artifact_ref`，不直接进入主控或模型上下文。
4. 核心运行时对象保持为 `AdapterDescriptor`、`CanonicalSnapshot`、`ActionProposal`、`PolicyDecision`、`ExecutionReceipt` 和 `AssertionResult`。TaskContract 是主控输入，EvidenceRecord 是 provider 的标准输出，Ledger 只保存这些对象的引用。
5. 所有真实动作都使用同一个单动作事务：`observe → propose → decide → execute → observe → collect evidence → evaluate`。
6. Qwen 点击、键盘输入、平台快捷键和应用启动都必须形成 `ActionProposal`，经过相同的权限、策略、执行回执和结果验证流程。
7. 组件只由薄 Orchestrator 调度，组件之间不得互相直接调用；正常通信只传决策所需的最小字段和对象引用。
8. `unknown`、字段缺失、provider 不可用和动作未执行都不能被解释为 `false`、失败或成功。
9. 新增合成器、模型、执行器、应用启动器或 Evidence Provider 时，原则上只增加 Adapter/Provider，不修改核心状态机。
10. 只有同时服务于动作安全、坐标融合或结果验证，具有稳定可测试语义，并且至少一个核心流程实际消费的事实，才允许进入 Canonical Model。
11. `based_on_snapshot` 只记录 Proposal 来源；执行有效性由控制层生成的 ProposalGuard 决定，禁止用完整 Snapshot 是否相等代替 Guard 检查。
12. `semantic_intent` 只是模型 claim；PolicyDecision 必须基于可追溯的 Semantic Resolution，不能把模型意图直接当作策略真值。

核心不是一个掌握所有桌面知识的“大脑”，而是一个小型动作事务内核：

```text
CanonicalSnapshot
  → ActionProposal
  → PolicyDecision
  → ExecutionReceipt
  → AssertionResult
```

PerceptionClaim、模型思考过程、完整 Assessment、原始树、截图和详细 trace 都属于诊断或 Context Builder 输入，不应扩大正常执行协议。

## 2. 三层职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| 主控 | 定义任务、风险、允许动作和验收条件；决定执行、确认、重试或停止 | 不计算坐标；不从窗口树猜控件语义；不把 Qwen 自述当作界面真值 |
| MCP 逻辑层 | 截图、合成器窗口事实、坐标、窗口层级、遮挡、陈旧提案、执行、状态机和验证编排 | 不判断按钮文字、桌面图标语义或任务是否在视觉上“看起来完成” |
| Qwen-CUA | 从截图理解界面，提出下一步鼠标/键盘动作，输出自己声称观察到的内容 | 不直接执行；不决定安全性；不作为最终验收器；不替代确定性坐标和遮挡计算 |

### 2.1 组件和依赖方向

```text
Controller
    │ TaskContract
    ▼
Core Orchestrator ──────────────────────────────────────────┐
    │                                                       │
    ├── CompositorAdapter  → CanonicalSnapshot              │
    ├── FrameProvider      → FrameReference                 │
    ├── ProposalProvider   → ActionProposal                 │
    ├── PolicyProvider     → PolicyDecision                 │
    ├── InputExecutor      → ExecutionReceipt               │
    ├── ApplicationLauncher→ ExecutionReceipt               │
    ├── CapabilityProvider → platform capabilities          │
    └── EvidenceProvider   → EvidenceRecord                 │
                                                            ▼
                                             AssertionEvaluator
                                                    │
                                                    ▼
                                             AssertionResult
```

组件只实现 port，不互相调用。禁止形成 `ProposalProvider → Executor`、`Executor → ProposalProvider`、`CompositorAdapter → Qwen` 或 `EvidenceProvider → TaskState` 等隐藏链路。

建议代码依赖方向：

```text
core/
  models.py
  orchestrator.py
  action_gate.py
  assertion_evaluator.py
  task_state.py
  ledger.py

ports/
  compositor.py
  frame.py
  proposal.py
  executor.py
  evidence.py
  policy.py
  platform_capability.py
  application_launcher.py

adapters/
  compositor/treeland.py
  proposal/qwen_cua.py
  executor/pyautogui.py
  platform/deepin_keybindings.py
  platform/dde_am.py
  evidence/compositor_window.py
```

依赖规则固定为：`adapters → ports/core models`、`core → ports`，核心不能 import 任何 adapter。

### 2.2 CompositorAdapter：跨合成器的空间事实边界

v2 不把 `treeland-debug --tree` 作为协议前提，而定义合成器适配器。最小可接入条件只有窗口树与光标位置；截图和输入注入可以由同一适配器或独立 provider 提供。

```text
CompositorAdapter
├── get_window_tree()        # 必需：当前顶层窗口/容器树
├── get_cursor_position()    # 必需：当前桌面坐标中的光标位置
├── get_desktop_geometry()   # 必需：输出区域、原点和缩放/坐标空间
├── hit_test(point)          # 可选：目标点的顶层命中结果
├── is_above(a, b)           # 可选：局部 stacking relation
└── occluded(window, region) # 可选：目标区域遮挡状态

FrameProvider               # 可由 CompositorAdapter 实现
└── capture_frame()

InputExecutor                # 可由 CompositorAdapter 或独立后端实现
└── inject(pointer / keyboard)
```

不同合成器的原始 tree 必须先归一化成 `CanonicalSnapshot` 与 `CanonicalWindowFact`，MCP 的融合、PolicyDecision、Evidence 和 Assertion 都只依赖此结构：

```json
{
  "schema_version": "1",
  "snapshot_id": "snapshot-42",
  "captured_at": "2026-09-04T10:00:00Z",
  "environment_version": "sha256:...",
  "coordinate_space": {
    "id": "desktop-logical",
    "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080}
  },
  "outputs": [
    {
      "output_id": "eDP-1",
      "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
      "scale": null
    }
  ],
  "cursor": {"x": 960, "y": 540},
  "windows": [
    {
      "window_id": "stable-compositor-id",
      "app_id": "optional.app.id",
      "title": "optional title",
      "geometry": {"x": 0, "y": 0, "width": 800, "height": 600},
      "visible": true,
      "active": true,
      "z_index": null,
      "workspace_id": "optional-workspace-id",
      "output_id": "eDP-1",
      "role": "normal"
    }
  ],
  "raw_artifact_ref": "artifact-42"
}
```

`environment_version` 用于观察关联、变更检测和审计，不是 ProposalGuard。它可以因任何 Snapshot 内容变化而变化，Core 禁止直接使用 `environment_version` 不相等判定 Proposal 失效。

CanonicalWindowFact 只允许 `window_id`、`app_id`、`title`、`geometry`、`visible`、`active`、可选 `z_index`、`workspace_id`、`output_id` 和 `role`。`role` 使用 `normal`、`desktop`、`panel`、`overlay`、`lockscreen`、`dialog` 或 `unknown`。不设计可以携带任意合成器字段的通用 `extensions`。

`window_id` 需要在适配器声明的稳定范围内有效，`app_id`、标题、工作区和 role 均可缺失。适配器必须同时报告自身能力，不能把字段缺失伪装为否定事实：

```json
{
  "adapter_id": "treeland",
  "capabilities": {
    "window_tree": true,
    "cursor_position": true,
    "desktop_geometry": true,
    "stacking": {
      "model": "hit-test",
      "z_index": "best-effort",
      "is_above": false,
      "occlusion": true
    },
    "active_window": true,
    "workspace": true,
    "window_identity": "best-effort",
    "child_controls": false,
    "window_text": false
  }
}
```

`z_index` 不是跨合成器必需能力。存在时只在当前 Snapshot 内表示 best-effort 顺序，不要求跨 Snapshot 稳定，也不能让 Core 假设它构成完美全序。Adapter 的 stacking model 使用固定枚举：`total-order`、`partial-order`、`hit-test`、`topmost-only` 或 `unavailable`。

安全检查优先使用 Adapter 可提供的标准查询：

```text
hit_test(point)
topmost_window_at(point)
is_above(window_a, window_b)
occluded(window_id, region)
```

点击操作真正依赖的是目标点当前命中了哪个顶层窗口，而不是所有窗口都有全局整数 `z_index`。只有 Adapter 声明 `total-order` 时，Core 才能用 z 顺序推导任意窗口间关系；能力不足时相关结论必须为 `unknown`。

仅有窗口树和光标位置时，系统仍能可靠完成坐标转换、窗口归属、顶层/遮挡判断、陈旧提案检测、窗口出现/消失和活动窗口验证；它不能据此断言窗口内部按钮、文本、桌面图标、输入焦点或业务结果。后者必须使用截图/Qwen、AT-SPI、DOM、OCR、剪贴板、文件系统或应用 API 的独立 evidence。

Treeland 的实现是：`TreelandAdapter → CanonicalSnapshot`，其 transport 目前为 `treeland-debug --tree`。它的 `BackgroundContainer` 只是 `role=desktop` 的一个实现细节，不能进入跨合成器协议或被解释为“没有桌面图标”。

### 2.3 平台能力目录：主控知道系统规则，Qwen 处理视觉剩余部分

“打开应用”不能完全依赖 Qwen 从截图猜桌面图标或记忆快捷键。Deepin 已提供可读取的默认快捷键 schema，以及 `dde-am` 的按应用 ID 启动接口；它们应由 MCP 作为平台能力提供给主控，而不是以原始 shell 命令的形式交给模型。

当前实现提供以下迁移期工具：

| 工具 | 用途 | 安全边界 |
| --- | --- | --- |
| `desktop_capabilities_list` | 查询 Deepin 默认快捷键、启用状态、风险和控制器策略 | 不返回 schema 的原始命令/DBus trigger value |
| `desktop_shortcut_invoke` | 调用经控制器批准的低风险能力 | 仅接受稳定 `capability_id`；不接受任意按键或命令 |
| `desktop_applications_list` | 从 desktop entry 解析可发现应用的 `app_id` | 不返回 `.desktop` 的 `Exec` 字段 |
| `desktop_application_launch` | 用 `dde-am <app_id>` 启动应用并采集 compositor-window evidence | 仅接受纯应用 ID；拒绝路径、URI、选项和 `dde-am -c` |

当前系统默认 schema 的关键事实为：

| `capability_id` | 默认快捷键 | 语义 |
| --- | --- | --- |
| `desktop.launcher.toggle` | `Meta` | 切换 Launcher |
| `desktop.search.open` | `Shift+Space` | 打开 Grand Search |
| `desktop.desktop.show` | `Meta+D` | 显示桌面 |

因此主控应优先采用以下路径：

```text
已解析 app_id 的 dde-am 启动
  > 已验证的 platform shortcut（例如 Meta 打开 Launcher）
  > Qwen 对 Launcher、搜索结果、欢迎页和应用内部控件的视觉操作
```

这不会把 Qwen 排除在流程外：Qwen 仍处理没有系统 API 的视觉界面；只是“系统如何打开 Launcher”与“哪个桌面图标是编辑器”不再由它猜测。

这些工具是迁移期 adapter facade，不是核心 API：Deepin 快捷键实现 `PlatformCapabilityProvider`，`dde-am` 实现 `ApplicationLauncher`。它们执行前必须生成 `platform.invoke` 或 `application.launch` Proposal，不能旁路单动作事务。

`/usr/share/dsg/configs/org.deepin.dde.keybinding` 是默认 schema，不是用户运行时设置的证明。目录结果必须标为 `source=default-schema`；接入 DConfig 或 D-Bus 有效配置查询后，才可以标为 `runtime-verified`。目录中即使 `enabled=true`，也不代表可自动执行：锁屏、注销、关机、关闭窗口等仍由 `controller-policy` 拒绝或要求用户确认。

## 3. 总体流程

```text
用户/主控
  │
  │ 1. TaskContract：目标、允许动作、风险、验收条件
  ▼
MCP Observation
  │
  │ 2. CanonicalSnapshot + FrameReference
  ▼
ProposalProvider / Controller
  │
  │ 3. ActionProposal：一个动作、snapshot_id、意图和预期效果
  ▼
Action Gate
  │
  │ 4. Semantic Resolution + ProposalGuard + PolicyDecision
  ▼
InputExecutor / ApplicationLauncher
  │
  │ 5. 重检 Guard；允许后产生 ExecutionReceipt
  ▼
MCP Observation
  │
  │ 6. 新 CanonicalSnapshot
  ▼
Evidence Providers
  │
  │ 7. Evidence Collection：API、文件、进程、窗口、AT-SPI、OCR 等证据
  ▼
Assertion Evaluator
  │
  │ 8. AssertionResult：用证据评价 TaskContract 断言
  ▼
Task State Reducer
  │
  │ 9. Task State Transition：continue / retry / completed / failed
  ▼
Event Ledger
     10. 只追加对象引用；需要模型时由 Context Builder 构造投影
```

Qwen 只是 ProposalProvider 的一种实现。需要 Qwen 时，Context Builder 使用 CanonicalSnapshot、FrameReference、TaskContract 和 Ledger 引用构造最小 ModelContext；控制器直接发起的 `application.launch` 或 `platform.invoke` 不需要经过模型。

以下八个概念必须保持分离：

```text
看到了什么（Perception）
≠ 想做什么（Planning）
≠ 建议在哪里做什么（Grounding / Proposal）
≠ 该提案是否获准执行（Assessment / Policy）
≠ 执行器实际做了什么（Execution）
≠ 各 provider 采集到了什么（Evidence）
≠ 证据是否满足任务断言（Assertion Evaluation）
≠ 任务下一步进入什么状态（Task State Transition）
```

## 4. TaskContract：主控定义任务契约

主控不再用一段自然语言同时表达目标、安全边界和验收规则，而是生成小型结构化任务契约。正常调用只携带目标、正向授权、断言、运行限制和策略引用；完整策略定义由 `policy_profile` 管理，不在每轮消息中重复展开。

还必须区分：

```text
MCP capabilities：系统技术上能够做什么
Task action permissions：本次任务授权哪些标准动作
Policy profile：本次任务允许这些动作产生什么业务效果
```

MCP capabilities 由注册表返回，不由 TaskContract 声明。TaskContract 只授权系统能力的一个子集。

```json
{
  "task_id": "open-text-editor",
  "goal": "打开文本编辑器",
  "permissions": {
    "actions": [
      "application.launch",
      "pointer.click",
      "keyboard.text"
    ],
    "semantic_intents": [
      "open_application",
      "content_edit"
    ]
  },
  "assertions": [
    {
      "assertion_id": "editor-opened",
      "path": "active_window.app_id",
      "operator": "equals",
      "expected": "deepin-editor"
    }
  ],
  "limits": {"max_steps": 5, "max_retries": 1},
  "policy_profile": "desktop-safe-default",
  "verification_profile": "application-open"
}
```

`policy_profile` 引用机械权限之外的详细语义规则，`verification_profile` 引用 provider 允许列表、优先级和冲突策略。需要覆盖默认值时才在任务中增加小型 override。迁移期可把旧的 `{"type": "active_window", ...}` 等 postcondition 编译为上述 assertion 结构。每个必要断言必须具有稳定 `assertion_id` 和可执行表达式。

动作权限采用正向授权：未授权即禁止，不再同时维护 `allowed_actions` 和 `forbidden_actions`，避免规则交叉。

语义策略的值固定为 `allow`、`confirm` 或 `deny`。一个动作可以同时命中多个语义标签，最终决策采用固定优先级：

```text
deny > confirm > allow
```

例如点击“删除全部云端数据”可能同时命中 `content_edit`、`external_side_effect` 和 `destructive`，最终结果为 `deny`。

任务契约属于主控责任，Qwen 不能自行修改机械权限、语义策略、运行限制或验收条件。

## 5. CanonicalSnapshot 与 FrameReference

每次决策前由 CompositorAdapter 生成新的不可变 `CanonicalSnapshot`。其标准结构见第 2.2 节；核心不读取合成器原始 tree。所有 Proposal 必须引用产生它时使用的 `snapshot_id`，但该引用只表示 provenance：记录 Proposal 基于哪次观察产生，不直接决定执行有效性。

Snapshot identity 不等于 Proposal validity。执行前出现新的 Snapshot 时，核心必须重新检查第 7.1 节的 ProposalGuard；只有动作所依赖的条件失效才拒绝。无关光标移动、标题刷新、时钟变化、动画或不影响目标的窗口变化不能使绝对坐标动作自动 stale。

截图不是合成器最低契约，由独立 FrameProvider 产生：

```json
{
  "frame_id": "frame-123",
  "captured_at": "2026-09-04T10:00:00Z",
  "image_ref": "image-123",
  "pixel_size": {"width": 1920, "height": 1080},
  "coordinate_mapping": {
    "from": "frame-pixel",
    "to": "desktop-logical",
    "transform_ref": "transform-123"
  }
}
```

截图可以来自合成器接口、portal、PipeWire、PyAutoGUI 或测试夹具。空间融合只能使用显式坐标变换，不能假定截图像素与桌面逻辑坐标相同。

CompositorAdapter 只提供顶层窗口/容器的几何、层级、可见性、工作区、遮挡和坐标命中事实。桌面 role 可以包含可点击图标，不能据 role 或容器类型判断图标、控件、输入焦点或内容是否存在。

## 6. ProposalProvider 与 Qwen 诊断输出

Qwen-CUA 是一个 ProposalProvider，不是核心依赖。进入核心的只有一个 `ActionProposal`；`PerceptionClaim`、详细 ActionIntent、ModelExpectation 和模型原始输出保存到 `debug_ref`，用于模型评测、归因和 Context Builder，不在正常组件通信中重复传递。

```json
{
  "schema_version": "1",
  "proposal_id": "proposal-123",
  "source": "qwen-cua",
  "based_on_snapshot": "snapshot-123",
  "action": {
    "type": "pointer.click",
    "coordinate": {
      "space": "desktop-logical",
      "x": 136,
      "y": 273
    }
  },
  "semantic_intent": "open_application",
  "expected_effect": {
    "active_app_id": "deepin-editor"
  },
  "debug_ref": "model-output-123"
}
```

核心动作类型使用有限枚举：

```text
pointer.move
pointer.click
pointer.double_click
pointer.drag
pointer.scroll
keyboard.key
keyboard.shortcut
keyboard.text
platform.invoke
application.launch
done
```

平台快捷键和 `dde-am` 启动不是旁路。它们分别形成 `platform.invoke` 和 `application.launch` Proposal，同样经过 PolicyDecision、ExecutionReceipt 和 AssertionResult。

### 6.1 PerceptionClaim

`perception_claim` 表示模型声称识别到的文字、图标、控件和界面状态。它可用于分析模型视觉能力，但不是独立界面真值。

例如：

```text
model_claim: 屏幕文字是 Owen
ground_truth: 屏幕文字是 Qwen
结论: Qwen 视觉转写错误
```

桌面图标提案未执行时：

```text
model_claim: 这是 Text Editor 图标
ground_truth: 缺失
action_effect: 未执行
结论: 无法判断识别和动作是否正确
```

自然语言观察是模型输出的一部分，可以保存和比较，但不能直接成为控件真值。

### 6.2 ActionIntent

`action_intent` 表示模型下一步想达到的语义目标，属于规划和决策。例如“打开 Text Editor”“清空计算器”或“点击加号”。

即使感知识别正确，意图仍可能错误。例如模型正确看到计算器已清空，却决定跳过数字 `7` 直接点击 `+`，应归为 `qwen_planning`，而不是感知错误。

该字段只保存简短、可审计的动作意图，不要求或暴露完整思维链。

### 6.3 ActionProposal

`action_proposal` 表示模型如何将意图落实成可执行动作，包括动作类型、坐标、按键和参数。

如果模型看对目标、意图也正确，但坐标落在相邻图标上，应归为 `qwen_grounding`。如果输出多个动作、非法参数或异常长调用，则归为 `qwen_protocol`。

### 6.4 ModelExpectation

`model_expectation` 表示模型预测动作后会发生什么，仅用于诊断和后续反馈。它不能替代 TaskContract 中的 `assertions`。

必须保持：

```text
model_expectation
≠ task_assertion
```

前者由 Qwen 提出；后者由主控的 TaskContract 定义，并由 Assertion Evaluator 使用独立 evidence 判断。

## 7. PolicyDecision：MCP 确定性裁决

Action Gate 使用 TaskContract、当前 CanonicalSnapshot 和 ActionProposal 返回最小决策对象：

```json
{
  "schema_version": "1",
  "proposal_id": "proposal-123",
  "status": "allow",
  "reason_code": "OK",
  "resolved_target": {"window_id": "window-52"},
  "guard_ref": "proposal-guard-123",
  "semantic_resolution_ref": "semantic-resolution-123",
  "debug_ref": "assessment-123"
}
```

状态固定为 `allow`、`deny`、`confirm`、`invalid` 或 `stale`。正常链路不返回完整窗口树、候选窗口、模型输出和详细规则求值；这些材料由 `debug_ref` 指向。

稳定拒绝码至少包括：`SNAPSHOT_UNAVAILABLE`、`UNSUPPORTED_ACTION`、`INVALID_COORDINATE_SPACE`、`OUTSIDE_DESKTOP`、`COORDINATE_SPACE_CHANGED`、`TARGET_NOT_FOUND`、`TARGET_DISAPPEARED`、`TARGET_IDENTITY_CHANGED`、`TARGET_GEOMETRY_INVALIDATED`、`TARGET_OCCLUDED`、`HIT_TEST_CHANGED`、`CURSOR_ORIGIN_CHANGED`、`CAPABILITY_UNAVAILABLE`、`MECHANICAL_PERMISSION_DENIED`、`SEMANTIC_POLICY_DENIED` 和 `CONFIRMATION_REQUIRED`。`STALE_SNAPSHOT` 只作为迁移期兼容总类，不能作为“两个 Snapshot 不相等”的直接结果。

`CAPABILITY_UNAVAILABLE` 或控件事实为 `unknown` 仅表示 MCP 没有相应真值，不表示：

- Qwen 没有看懂；
- 图标不存在；
- 动作错误；
- 动作必须被拒绝。

桌面 role 上的低风险点击可以执行，只要：

- 坐标位于桌面内；
- 执行前仍命中同一顶层容器；
- 目标没有被其他窗口覆盖；
- TaskContract 的动作权限允许 `pointer.click`；
- 解析出的语义标签经 policy 合并后结果为 `allow`。

Qwen 可以给出 `semantic_intent`，但它仍属于模型提案。PolicyProvider 必须根据 TaskContract、平台能力语义和用户确认解析实际策略；无法解析时使用 `unknown` 策略，不能默认为低风险。

### 7.1 ProposalGuard：执行有效性不是 Snapshot 相等性

ProposalGuard 是 Action Gate 根据 ActionProposal、CanonicalSnapshot、TaskContract 和 Adapter capability 推导的控制层记录，不能由 Qwen 提供或修改。它不是新的任务生命周期阶段，只是 PolicyDecision 授权执行时引用的前置条件集合：

```json
{
  "guard_id": "proposal-guard-123",
  "proposal_id": "proposal-123",
  "derived_from_snapshot": "snapshot-123",
  "coordinate_space": {
    "id": "desktop-logical",
    "version": "geometry-7"
  },
  "target": {
    "window_id": "window-52",
    "identity_required": true,
    "required_visible": true
  },
  "geometry": {
    "expected": {"x": 100, "y": 100, "width": 800, "height": 600},
    "policy": "point-must-remain-inside"
  },
  "hit_test": {
    "point": [136, 273],
    "required_target_window_id": "window-52"
  }
}
```

执行器接收 Proposal 和允许执行的 PolicyDecision 后，必须在产生输入副作用前用最新 CanonicalSnapshot 重检 `guard_ref`。Guard 只包含动作真实依赖的条件：

- 绝对坐标点击通常依赖坐标空间、目标身份、可见性、点是否仍在目标内以及该点的顶层命中结果；
- 相对移动或拖拽还可能依赖光标起点；
- 窗口标题仅在它参与目标身份解析时才进入 Guard；
- 无关窗口、无关标题、时钟、动画或不影响目标点的变化不进入 Guard。

Guard 失效必须返回最具体的原因码，而不是笼统比较 Snapshot hash。`based_on_snapshot` 永远保留为来源引用，即使新 Snapshot 下 Guard 仍然成立。

### 7.2 Semantic Resolution：模型意图不是策略真值

策略求值前必须先解析动作语义：

```text
ActionProposal
  → Semantic Resolution
  → Policy Evaluation
  → PolicyDecision
```

`ActionProposal.semantic_intent` 是 proposal claim，只能作为一个语义来源，不能单独升级为 policy truth。Semantic Resolution 汇总与动作对象相关的 semantic evidence：

```json
{
  "semantic_resolution_id": "semantic-resolution-123",
  "proposal_id": "proposal-123",
  "status": "resolved",
  "tags": [
    {
      "tag": "destructive",
      "source": "atspi",
      "evidence_ref": "evidence-81",
      "confidence": "deterministic"
    },
    {
      "tag": "delete_account",
      "source": "qwen-claim",
      "evidence_ref": "model-output-123",
      "confidence": "model-claim"
    }
  ]
}
```

语义来源按其自身证据质量处理：

| 来源 | 示例 | 策略地位 |
| --- | --- | --- |
| PlatformCapability | `power.shutdown` | 确定性平台语义 |
| 应用 API / DOM | `button.action=delete-account` | 高可信应用语义 |
| AT-SPI | role 与 accessible name | 独立控件语义 evidence |
| TaskContract | 当前测试步骤允许保存草稿 | 主控授权范围 |
| Qwen | “这是删除按钮” | 模型 claim |
| 只有坐标和窗口 | `click(x,y)` | `unknown` |

没有独立 semantic evidence 时，系统不可能同时做到“不相信模型”与“自动理解任意 GUI 控件风险”。默认仍为 `unknown → confirm`；明确隔离的测试环境可以通过 policy profile 对特定应用、窗口或任务范围授权 `unknown → allow`，但必须是主控授权，不能由模型自行放宽。

Semantic Resolution 可以作为 PolicyProvider 内部的标准步骤，不必扩展六个核心生命周期对象。PolicyDecision 通过 `semantic_resolution_ref` 保留可审计来源。

## 8. 执行资格与提案正确性分离

不要使用同一个 `validation` 同时表达动作是否安全和动作是否正确：

```json
{
  "execution_eligibility": {
    "passed": true,
    "reasons": []
  },
  "proposal_correctness": {
    "status": "not-assessed",
    "evidence_collection_required": true
  }
}
```

以点击桌面编辑器图标为例：

- 窗口树无法证明它是编辑器图标；
- 也不能否定模型声称的图标；
- 点击本身低风险且窗口级安全时允许执行；
- 点击后检查是否出现 `deepin-editor`。

## 9. 对外状态信封与最小通信

统一信封只用于 MCP 对外 facade，不用于内核组件之间通信。正常响应只返回状态和核心对象引用；详细 effect、evidence、attribution 和 metrics 按引用查询：

```json
{
  "protocol_version": 2,
  "operation": "propose",
  "status": "needs-execution",
  "object_ref": "proposal-123",
  "error": null,
  "retry": null,
  "debug_ref": null
}
```

只有调用方明确请求诊断时，才通过 `object_ref` 或 `debug_ref` 取得完整对象。组件之间直接传 typed object 或引用，禁止套用包含未使用字段的大信封。

建议状态：

| 状态 | 含义 |
| --- | --- |
| `ok` | 当前操作完整成功 |
| `needs-execution` | 有安全可执行提案，尚未产生实际效果 |
| `needs-confirmation` | 动作风险超过自动执行阈值 |
| `needs-evidence` | 动作已执行，但尚未取得足够的任务结果证据 |
| `needs-evaluation` | 已取得证据，但断言尚未完成评价 |
| `partial` | 部分动作或部分验收条件成功 |
| `refused` | MCP 确定性安全校验拒绝 |
| `failed` | 执行或确定性验收失败 |
| `completed` | 所有任务验收条件通过 |

Qwen 返回 `DONE` 只能触发 Evidence Collection，不能直接产生 `completed`。只有 Task State Reducer 在所有必要断言通过后才能产生 `completed`。迁移期可将旧状态 `needs-verification` 映射为 `needs-evidence`，但新协议不再把验证表示为单一 Verifier 调用。

可预期错误应包含稳定字段：

```json
{
  "error": {
    "code": "ENVIRONMENT_TARGET_CHANGED",
    "message": "The top-level target changed before execution",
    "retry": true,
    "required_action": "capture-new-frame"
  },
  "attributions": [
    {
      "event_kind": "safe-refusal",
      "stage": "environment",
      "owner": "environment",
      "code": "ENVIRONMENT_TARGET_CHANGED",
      "evidence_status": "confirmed",
      "primary": false,
      "summary": "Target window changed before execution"
    }
  ]
}
```

`error` 描述当前操作为何没有完成，`attributions` 描述事件性质和责任归因；调用方不得从 `error` 的存在直接推导组件失败。例如上例是 MCP 正确发现环境变化后的安全拒绝，不计入 MCP 错误率。

## 10. ExecutionReceipt：执行器实际做了什么

模型提案、策略批准、实际注入和业务结果必须分开。执行器只返回最小回执：

```json
{
  "schema_version": "1",
  "execution_id": "execution-123",
  "proposal_id": "proposal-123",
  "status": "delivered",
  "executed_action": {
    "type": "pointer.click",
    "coordinate": {
      "space": "desktop-logical",
      "x": 136,
      "y": 273
    }
  },
  "started_at": "...",
  "finished_at": "...",
  "error_code": null,
  "debug_ref": null
}
```

状态固定为 `delivered`、`rejected`、`failed` 或 `unknown`。`delivered` 只能证明执行后端接受并注入了动作，不能证明应用收到动作或产生了模型预期的业务效果。`window_opened`、文本变化等结果必须由 Evidence Provider 另行采集，不能写进 ExecutionReceipt 冒充执行事实。

尤其必须保持：

```text
ActionIntent: clear calculator
≠ ExecutionReceipt: click(420,610) was delivered
≠ AssertionResult: calculator_display_empty == passed
```

因此 Context Builder 不得生成含糊字段 `previous_action: clear`。它必须分别投影模型意图、ExecutionReceipt 和断言结果；正常情况下只需向 Qwen 提供后两者，历史意图仅用于诊断。

如果 Qwen 提议的坐标本身偏离目标，属于 `qwen_grounding`；如果批准坐标正确但执行器注入到其他位置，属于 `mcp_executor`。

未执行的动作应返回：

```json
{
  "proposal_id": "proposal-123",
  "status": "needs-execution"
}
```

未执行提案不能记作模型成功或失败。

## 11. Evidence、Assertion 与 Task State

系统不设置一个掌握所有真值的单体 Verifier。验证拆成三个独立阶段：

```text
Evidence Collection
  → Assertion Evaluation
  → Task State Transition
```

### 11.1 Evidence Provider

每个 provider 只报告自己取得的 evidence，不决定任务是否完成。可接入的 provider 包括：

1. 应用 API、测试退出码、数据库、文件系统和进程状态；
2. DOM、AT-SPI、D-Bus 和剪贴板；
3. CompositorAdapter 的窗口 `appId/title`、层级、位置和光标位置；
4. 独立 OCR 或人工标注；
5. Qwen 对截图的重新观察。

上述顺序是默认可信度参考，不是对所有断言都适用的固定全局排序。`verification_profile` 为断言声明允许的 provider、优先级和冲突规则。例如文件内容断言应优先使用文件系统，而不是窗口标题。

Provider 只能声明由核心注册表定义的标准 fact path：

```json
{
  "provider_id": "compositor-window",
  "provides": [
    "active_window.app_id",
    "active_window.window_id",
    "window.geometry",
    "cursor.position"
  ]
}
```

系统可以自动发现、注册和按断言选择 provider，但字段语义映射必须由 Adapter 明确定义。原始 provider 数据不能自动扩展 fact path；无法映射的字段丢弃或仅保存为 `raw_artifact_ref`。

统一 Evidence 结构：

```json
{
  "schema_version": "1",
  "evidence_id": "evidence-42",
  "provider": "compositor-window",
  "collected_at": "2026-09-02T10:20:31Z",
  "subject": {
    "display_id": 0,
    "window_id": 184,
    "snapshot_id": "snapshot-123"
  },
  "facts": {
    "active_app_id": "deepin-editor",
    "window_title": "Editor"
  },
  "quality": {
    "method": "deterministic-api",
    "confidence": "deterministic"
  },
  "freshness": {
    "valid_at_collection": true,
    "expires_on_environment_change": true
  },
  "provenance": {
    "operation_id": "collect-42",
    "raw_artifact_ref": "window-snapshot-42"
  }
}
```

Evidence 必须携带来源、采集时间、作用对象、关联 frame、质量、时效和原始材料引用。`confidence` 建议使用稳定枚举：

```text
deterministic
derived
probabilistic
model-claim
human-annotation
```

Qwen 重新观察产生的是 `model-claim` evidence。它可以辅助定位、恢复或发现矛盾，但不能单独决定关键任务成功，也不能覆盖仍然有效的确定性 evidence。

### 11.2 Assertion Evaluator

Assertion Evaluator 不采集界面事实，也不调用 Qwen 猜结果。它只使用 TaskContract 中的断言和已收集 evidence，输出可审计的评价：

```json
{
  "assertion_id": "editor-opened",
  "expression": {
    "path": "active_app_id",
    "operator": "equals",
    "expected": "deepin-editor"
  },
  "status": "passed",
  "evidence_refs": ["evidence-42"],
  "evaluated_at": "2026-09-02T10:20:32Z",
  "reason": "Active window appId matches required value"
}
```

断言状态使用：

```text
passed
failed
unknown
conflict
```

`unknown` 表示证据缺失、过期或不适用于该断言；`conflict` 表示多个仍有效且适用的来源互相矛盾。两者都不能被当作通过。

证据解析遵循：

1. 先检查 evidence 是否与断言对象和作用域匹配；
2. 再检查 frame、窗口身份和环境变化造成的时效失效；
3. 使用 TaskContract 为该断言指定的 provider 优先级；
4. 没有显式规则时，确定性 evidence 优先于派生、概率和模型自述；
5. 无法安全消解的冲突返回 `conflict`，不得多数投票或静默选择；
6. 每个结果必须保留实际使用和被排除的 evidence 引用及原因。

### 11.3 Task State Reducer

Task State Reducer 根据必要断言、运行限制和当前任务状态执行确定性状态转换：

```text
所有必要断言 passed                         → completed
可恢复断言 failed，且仍有重试预算            → retry
证据 unknown/conflict，且允许继续收集         → needs-evidence
不可恢复断言 failed 或预算耗尽                → failed
仍有后续步骤                                 → continue
```

Qwen 的 `DONE`、动作成功和单个断言通过都不能直接修改任务为 `completed`。

### 11.4 模型预期、证据和验收条件不得混用

必须保持：

```text
model_expectation
≠ task_assertion
≠ evidence
≠ assertion_result
≠ task_state
```

`model_expectation` 用于评价模型是否正确预测动作效果；`task_assertion` 是主控定义的验收规则；`evidence` 是 provider 取得的材料；`assertion_result` 是 evaluator 的判断；`task_state` 是 reducer 产生的状态。

字符识别样例中，剪贴板可以提供确定性文字 evidence，而 Qwen 的读取只作为诊断证据：

```json
{
  "evidence": [
    {
      "evidence_id": "clipboard-1",
      "provider": "clipboard",
      "facts": {"text": "Qwen"},
      "quality": {"confidence": "deterministic"}
    },
    {
      "evidence_id": "qwen-claim-1",
      "provider": "qwen-reobservation",
      "facts": {"text": "Owen"},
      "quality": {"confidence": "model-claim"}
    }
  ],
  "assertion_evaluation": {
    "assertion_id": "text-is-qwen",
    "status": "passed",
    "evidence_refs": ["clipboard-1"],
    "excluded_evidence": [
      {
        "evidence_id": "qwen-claim-1",
        "reason": "diagnostic model claim cannot override deterministic text source"
      }
    ]
  }
}
```

## 12. 故障分类与责任归属

| 阶段 | 典型问题 | 故障代码/责任 |
| --- | --- | --- |
| PerceptionClaim | 看错文字、图标、控件或状态，并有独立真值反证 | `qwen_perception` |
| ActionIntent | 感知足够正确，但下一步决策错误、跳步或重复 | `qwen_planning` |
| ActionProposal | 意图正确但坐标偏离目标 | `qwen_grounding` |
| ActionProposal | 多动作、非法参数、截断或异常长输出 | `qwen_protocol` |
| PolicyDecision | 应拒绝却允许，或错误拒绝 | `mcp_validation` |
| ExecutionReceipt | 实际注入动作与批准动作不一致 | `mcp_executor` |
| EvidenceCollection | provider 采集失败、返回错误事实或错误接受过期证据 | `evidence_provider` |
| AssertionEvaluation | evidence 正确，但断言被错误判为通过或失败 | `assertion_evaluator` |
| TaskStateTransition | 断言结果正确，但任务状态发生非法转换 | `task_state_reducer` |
| Outcome | 动作准确执行，但应用没有产生预期响应 | `environment_response` 或继续调查上游策略 |
| CompletionClaim | 结果未满足却返回 `DONE` | `qwen_completion` |
| 任意阶段 | 缺少独立真值或动作未执行 | `insufficient_evidence` |

“点偏了”必须继续区分：

```text
Qwen 提议坐标偏离目标 → qwen_grounding
MCP 未按批准坐标注入 → mcp_executor
动作准确注入但应用未响应 → environment_response / outcome failure
```

### 12.1 Text Editor 未执行样本

```text
PerceptionClaim:
  模型声称看到 Text Editor 图标

ActionIntent:
  打开 Text Editor

ActionProposal:
  click(136,273)

PolicyDecision:
  坐标命中 desktop role，窗口级可执行

ExecutionReceipt:
  无，主控未执行

EvidenceCollection:
  无

AssertionEvaluation:
  无

结论:
  无法判断 perception、planning 和 grounding 是否正确
```

### 12.2 Calculator 打开成 Music 样本

该样本可确定：批准的点击动作由 MCP 按坐标准确执行，但结果窗口为 `deepin-music`，不满足 `deepin-calculator` 的 task assertion。

如果有独立 Dock 图标标注，可进一步区分是 `qwen_perception` 还是 `qwen_grounding`；没有该真值时，只能确认 Qwen 的动作提案未达到预期结果，不能臆测其内部感知原因。

### 12.3 Attribution Protocol

`responsibility` 不使用自由字符串。每个可归因事件分别记录发生阶段、责任组件、稳定错误码、证据等级和事件性质：

```json
{
  "attribution": {
    "event_kind": "error",
    "stage": "perception",
    "owner": "qwen",
    "code": "MODEL_PERCEPTION_MISMATCH",
    "evidence_status": "confirmed",
    "primary": true,
    "summary": "Qwen read 'Qwen' as 'Owen'",
    "evidence_refs": [
      "frame-123",
      "clipboard-ground-truth-123"
    ]
  }
}
```

`stage`、`owner` 和 `code` 必须分开，不能使用 `model-perception` 之类同时编码阶段和责任人的单一字段。这样可以分别统计某类阶段错误和某个组件的责任比例。

### 12.4 稳定枚举

#### Stage

```text
perception
planning
grounding
protocol
guard-evaluation
execution
environment
outcome
evidence-collection
assertion-evaluation
state-transition
policy
```

#### Owner

```text
qwen
action-gate
executor
environment
evidence-provider
assertion-evaluator
task-state-reducer
controller-policy
unknown
```

#### Event kind

```text
error
safe-refusal
policy-decision
external-change
incomplete
insufficient-evidence
```

#### Evidence status

```text
confirmed
inferred
insufficient
```

只有 `confirmed` 进入正式错误率。`inferred` 单独统计；`insufficient` 不扣任何组件的能力分。

### 12.5 稳定错误码

#### Qwen

```text
MODEL_PERCEPTION_MISMATCH
MODEL_PLANNING_INVALID
MODEL_GROUNDING_MISS
MODEL_PROTOCOL_INVALID
MODEL_COMPLETION_FALSE_POSITIVE
MODEL_EXPECTATION_MISMATCH
```

#### MCP 与执行器

```text
POLICY_DECISION_FALSE_ALLOW
POLICY_DECISION_FALSE_REJECT
PROPOSAL_GUARD_EVALUATION_ERROR
MCP_REPROJECTION_ERROR
EXECUTOR_ACTION_MISMATCH
EXECUTOR_ACTION_FAILED
```

#### 环境与结果

```text
ENVIRONMENT_TARGET_CHANGED
COORDINATE_SPACE_CHANGED
TARGET_DISAPPEARED
TARGET_IDENTITY_CHANGED
TARGET_GEOMETRY_INVALIDATED
TARGET_OCCLUDED
HIT_TEST_CHANGED
CURSOR_ORIGIN_CHANGED
ENVIRONMENT_APPLICATION_NO_RESPONSE
OUTCOME_POSTCONDITION_FAILED
```

#### Evidence、Assertion 与 Task State

```text
EVIDENCE_COLLECTION_FAILED
EVIDENCE_PROVIDER_FALSE_FACT
EVIDENCE_PROVIDER_STALE_FACT
ASSERTION_EVALUATOR_FALSE_PASS
ASSERTION_EVALUATOR_FALSE_FAIL
ASSERTION_EVIDENCE_CONFLICT_UNRESOLVED
TASK_STATE_INVALID_TRANSITION
```

#### 主控策略

```text
POLICY_DENIED
CONTROLLER_POLICY_FALSE_ALLOW
CONTROLLER_POLICY_FALSE_REJECT
CONTROLLER_TASK_CONTRACT_INVALID
```

#### 证据不足

```text
INSUFFICIENT_GROUND_TRUTH
PROPOSAL_NOT_EXECUTED
ROOT_CAUSE_UNRESOLVED
```

错误码一旦发布应保持语义稳定；需要细化时增加新码，不改变既有错误码含义。

### 12.6 非错误事件不能计入失败率

窗口被覆盖后 MCP 返回 `target_window_changed`，应记录为环境变化和安全拒绝成功：

```json
{
  "event_kind": "safe-refusal",
  "stage": "environment",
  "owner": "environment",
  "code": "ENVIRONMENT_TARGET_CHANGED"
}
```

风险策略正常阻止删除操作时：

```json
{
  "event_kind": "policy-decision",
  "stage": "policy",
  "owner": "controller-policy",
  "code": "POLICY_DENIED"
}
```

两者都不是系统错误。只有本应允许却拒绝时，才记录 `MCP_ASSESSMENT_FALSE_REJECT` 或 `CONTROLLER_POLICY_FALSE_REJECT`。

### 12.7 Primary cause 与 contributing factors

一个任务可以有多个故障事件，但任务失败统计只记录一个 primary cause：

```json
{
  "primary_attribution": {
    "code": "MODEL_PLANNING_INVALID"
  },
  "contributing_attributions": [
    {
      "code": "MODEL_COMPLETION_FALSE_POSITIVE"
    }
  ]
}
```

例如一次 `Ctrl+Z` 只删除一个字符，模型随后错误返回 `DONE`：规划错误可以是 primary cause，错误完成判断是 contributing factor。任务失败率只计一次，贡献因素用于根因分析，避免重复扣分。

### 12.8 当前样本归因

| 样本 | Stage | Owner | Code / 说明 |
| --- | --- | --- | --- |
| `Qwen` 读成 `Owen` | perception | qwen | 有独立文字真值时为 `MODEL_PERCEPTION_MISMATCH` |
| Calculator 最终打开 Music | outcome/grounding | qwen 或 unknown | 先记 `MODEL_EXPECTATION_MISMATCH`；有 Dock 图标真值后再判断 perception/grounding |
| 清空后跳过输入 `7` | planning | qwen | `MODEL_PLANNING_INVALID` |
| 一次 `Ctrl+Z` 被认为可清空整段文本 | planning | qwen | `MODEL_PLANNING_INVALID` |
| 文本未清空却返回 `DONE` | outcome | qwen | `MODEL_COMPLETION_FALSE_POSITIVE` |
| 快捷键选择错误 | planning | qwen | `MODEL_PLANNING_INVALID` |
| Background 图标提案未执行 | policy | controller-policy | `PROPOSAL_NOT_EXECUTED`；如确认属于错误阻止，再记 `CONTROLLER_POLICY_FALSE_REJECT` |
| File Manager 覆盖目标窗口 | environment | environment | `ENVIRONMENT_TARGET_CHANGED`，属于 safe refusal |
| 批准坐标与实际注入坐标不一致 | execution | executor | `EXECUTOR_ACTION_MISMATCH` |
| Evidence Provider 返回错误窗口事实 | evidence-collection | evidence-provider | `EVIDENCE_PROVIDER_FALSE_FACT` |
| Assertion Evaluator 错误报告通过 | assertion-evaluation | assertion-evaluator | `ASSERTION_EVALUATOR_FALSE_PASS` |
| 断言未全部通过却进入 completed | state-transition | task-state-reducer | `TASK_STATE_INVALID_TRANSITION` |

### 12.9 Benchmark 分母与统计

不同错误率使用与其证据条件匹配的分母：

- 感知错误率：有独立视觉真值的感知样本；
- 规划错误率：当前状态已被独立验证的决策样本；
- Grounding 错误率：有控件区域标注或确定结果的定位样本；
- Executor 错误率：实际执行动作数；
- Evidence Provider 错误率：有独立参考真值的 evidence 采集次数；
- Assertion Evaluator 错误率：输入 evidence 和断言预期结果均已固定的评价次数；
- Task State Reducer 错误率：断言集合与预期状态转换均已固定的转换次数；
- 任务失败率：全部有效任务，按 primary attribution 统计。

报告同时区分错误与非错误控制事件：

```text
任务成功率                         81.2%

失败任务 primary attribution:
  qwen/perception                  12.3%
  qwen/planning                     8.4%
  qwen/grounding                    4.7%
  action-gate                       0.6%
  executor                          1.1%
  evidence-provider                0.2%
  assertion-evaluator              0.1%
  task-state-reducer               0.1%

非错误控制事件:
  environment target changed        2.2%
  policy denied                     1.8%
  safe refusal success             99.7%
```

除比例外，每项必须报告分子、分母、证据等级和未归因样本数，避免不同适用范围的指标被直接比较。

## 13. 会话、历史与任务状态

当前本地实现和论文描述存在重要差异：

- Qwen-CUA 论文 scaffold 描述最多保留 20 张活跃截图；
- 本项目当前 `CUA_MAX_HISTORY_TURNS` 默认值为 4。

因此不能直接使用论文中的历史容量解释本地 `qwen3_rl` 表现。但 v2 不应把轮数作为核心上下文机制：`4/10/20` 只是一种投影策略的容量参数，更重要的是从完整 Ledger 中选择哪些事件进入当前上下文。

### 13.1 System Task State

由 MCP 维护确定性任务状态，Qwen 不能修改：

```json
{
  "task_state": {
    "step": 2,
    "verified_facts": [
      "calculator_window_active"
    ],
    "failed_assertions": [
      "calculator_display_cleared"
    ],
    "completed_assertions": []
  }
}
```

只有通过 MCP 来源、作用域、时效和冲突规则的 evidence fact 才能进入 `verified_facts`；由任务断言推导出的事实还必须经过 Assertion Evaluator，并由 Task State Reducer 接受。Qwen 过去的自然语言观察不能进入该集合。

### 13.2 Event Ledger：权威运行记录

所有事件都追加到不可变、可审计的 Event Ledger：

```text
snapshot.created
model_diagnostic.recorded
proposal.created
decision.created
execution.completed
evidence.collected
assertion.evaluated
task.transitioned
attribution.recorded
```

Ledger 是“系统运行中实际记录过什么”的权威历史，不表示其中每个 payload 都是界面真值。每个事件必须标记自己的认识论类型：

```text
verified_fact       独立 evidence 支持并通过断言的事实
model_claim         模型声称看到或认为的内容
action_intent       模型希望达到的下一步效果
action_proposal     ProposalProvider 提出的动作
policy_decision     Action Gate 的确定性裁决
execution_receipt   执行器实际接受和注入的动作
evidence            provider 采集的材料
assertion_result    evaluator 对断言的评价
state_transition    reducer 产生的任务状态变化
attribution         失败、拒绝、环境变化或证据不足的归因
```

统一 Ledger Event 至少包含：

```json
{
  "event_id": "event-108",
  "task_id": "open-text-editor",
  "sequence": 108,
  "occurred_at": "2026-09-02T10:20:32Z",
  "event_type": "assertion.evaluated",
  "epistemic_type": "assertion_result",
  "caused_by": ["event-106", "event-107"],
  "snapshot_id": "snapshot-123",
  "object_ref": "assertion-result-42",
  "artifact_refs": ["evidence-42"],
  "debug_ref": null
}
```

`sequence` 在单个 task 内单调递增；`caused_by` 记录因果输入，而不是只依赖相邻顺序猜测。Ledger 只保存核心对象和 artifact 的引用，不重复嵌入大对象。已追加事件不原地修改，后续纠错通过新的 superseding event 表达。

AssertionResult 不能在产生时直接标为 `verified_fact`。只有 Task State Reducer 接受适用、有效且无冲突的 evidence 和 AssertionResult 后，才能追加独立事件：

```json
{
  "event_type": "verified_fact.accepted",
  "epistemic_type": "verified_fact",
  "object_ref": "verified-fact-42",
  "caused_by": ["evidence-42", "assertion-result-42"]
}
```

必须保持 `EvidenceRecord ≠ AssertionResult ≠ VerifiedFact`；`failed`、`unknown` 或 `conflict` 的 AssertionResult 不能产生 verified fact。

因此必须保持：

```text
Ledger = 完整、不可变、可审计的运行记录
Context = 根据当前决策目的对 Ledger 的受控投影
```

不能使用 `Context = history`，也不能因事件进入 Ledger 就把其中的 `model_claim` 升级为 `verified_fact`。

### 13.3 Context Builder：Ledger 的策略化投影

Qwen 每轮只能接收统一的 `ModelContext`，不能由调用方临时拼接合成器原始窗口树、自由文本 history 或未标记的 `previous_feedback`：

```json
{
  "model_context_id": "context-52",
  "frame": {
    "frame_id": "frame-123",
    "screenshot_ref": "screenshot-123"
  },
  "task_projection": {
    "goal": "在计算器中输入 7+8",
    "current_step": "输入数字 7",
    "pending_assertions": ["digit-7-entered"]
  },
  "verified_state_projection": {
    "facts": [
      {
        "path": "active_window.app_id",
        "value": "deepin-calculator",
        "source": "compositor-window-tree",
        "evidence_ref": "evidence-window-52",
        "freshness": "current"
      }
    ]
  },
  "recent_execution_receipt": {
    "executed_action": {
      "type": "click",
      "coordinate": [420, 610]
    },
    "status": "delivered",
    "estimated_target_app": "deepin-calculator",
    "ledger_event_ref": "event-106"
  },
  "assertion_feedback": [
    {
      "assertion_id": "calculator-display-empty",
      "status": "failed",
      "evidence_refs": ["evidence-clipboard-51"]
    }
  ],
  "constraints": {
    "single_action_only": true,
    "remaining_steps": 4,
    "destructive": "deny"
  }
}
```

这个契约表达的正是：

```text
当前 screenshot
+ 与当前步骤有关的 verified facts
+ 最近真实 ExecutionReceipt
+ 独立 AssertionResult
+ 当前任务与策略约束
→ Qwen 下一动作
```

其中 `estimated_target_app` 只表示 MCP 根据现有窗口树几何和层级推导的顶层候选，不表示合成器已经证明输入实际交付给该应用。

一个事实只有同时满足以下条件才能进入 `verified_state_projection`：

1. 来自被允许的 Evidence Provider，并通过 MCP 的来源、作用域、时效和冲突规则；由任务断言推导时还须被 Assertion Evaluator 接受；
2. 对当前 frame、窗口身份和环境状态仍然有效；
3. 会影响当前步骤的下一动作或恢复决策；
4. 不包含从窗口级事实跳跃推导出的控件或业务语义；
5. 保留 source、evidence/event 引用和 freshness。

以下内容不得进入 `verified_state_projection`：

- 合成器原始完整窗口树或完整 Ledger；
- 已过期或已被后续事件取代的事实；
- Qwen 过去的 `model_claim`；
- 未经证据支持的控件名称、按钮作用或业务结果；
- 将 `ActionIntent` 改写成已经发生的动作效果，例如 `previous_action: clear`。

如果“显示非空”只有 Qwen 看截图后的自述，该声明只保存在 Ledger 供诊断，断言保持为 `unknown`，不得放入 `verified_state_projection`，也不能生成 `assertion_feedback.status = failed`。只有 clipboard、AT-SPI、DOM、独立 OCR、人工标注或任务允许的其他 evidence 支持时，才可产生正式失败断言。

Context Builder 再根据任务阶段、失败状态、token 预算和视觉预算选择投影策略。至少支持：

```text
compact               当前 frame、TaskContract、最近 receipt 和未完成断言
visual-heavy          更多近期截图和窗口变化，减少无关文本
recovery              最近实际动作、失败断言、可靠 evidence 和恢复边界
verification-focused  相关 evidence、冲突来源和待满足断言
planning-reset        保留任务事实与约束，丢弃可能造成路径依赖的模型废话
```

典型 recovery context 只需要：

- 最新有效截图及其 frame 身份；
- 最近批准动作和实际 `ExecutionReceipt`；
- 未通过、未知或冲突的 assertion；
- 最新且仍有效的高可信 evidence；
- TaskContract、剩余预算和允许的恢复范围；
- 已确认事实和最近一次 primary attribution。

Context 中每项必须保留 Ledger event id、类型和来源，使 Qwen 输出能够追溯到原始记录。对截图数量、文本 token 和事件数量分别限额；不能用单一 `CUA_MAX_HISTORY_TURNS` 同时控制三者。

当前实现只有 `success` 正式加入模型历史，其他状态主要通过 `previous_feedback` 暂存。v2 应先保证 Ledger 完整，再比较不同 Context Builder 策略；4、10、20 轮历史对比降为兼容性基线，而不是最终架构目标。

## 14. 固定验收流程与开放任务

### 14.1 预定义代码验收

预定义验收使用确定性状态机：

```text
打开应用
  → 验证 appId
输入数据
  → 验证实际文本
点击提交
  → 验证 API/DOM/日志
结束
```

Qwen 负责每个状态内的视觉定位和候选动作，不能绕过未通过的 assertion。

### 14.2 开放式桌面任务

开放任务允许 Qwen 自主选择下一步，但仍需阶段 checkpoint：

```text
Qwen 自选下一步
  → MCP 安全校验
  → 执行
  → 观察
  → 外部检查阶段目标
```

如果连续两次无进展、动作重复或模型观察与验证事实冲突，则按策略执行：

```text
retry → reobserve → reset model history → ask controller
```

## 15. 风险策略

风险判断分为机械授权和语义策略两个正交维度。

### 15.1 机械权限

机械权限只描述输入通道，不描述业务风险：

| 机械能力 | 示例 |
| --- | --- |
| `pointer.move` | 移动鼠标 |
| `pointer.click` | 左键点击 |
| `pointer.drag` | 拖拽 |
| `pointer.scroll` | 滚动 |
| `keyboard.text_input` | 输入文本 |
| `keyboard.keys` | 单键输入 |
| `keyboard.shortcuts` | 组合键 |

快捷键还需通过平台白名单，不能只信任模型知识。

### 15.2 语义策略

| 语义标签 | 建议默认策略 |
| --- | --- |
| `navigation` | `allow` |
| `open_application` | `allow`，执行后验证窗口身份 |
| `content_edit` | `confirm`，测试环境可显式改为 `allow` |
| `settings_change` | `confirm` |
| `external_side_effect` | `confirm` |
| `destructive` | `deny` |
| `authentication` | `deny` 或强确认 |
| `unknown` | `confirm` |

语义标签是集合而不是互斥枚举。PolicyProvider 只评价第 7.2 节 Semantic Resolution 输出的标签，不能直接把 Qwen 的 `semantic_intent` 当作已解析语义。每个标签必须保留 source、evidence reference 和 confidence；多标签按 `deny > confirm > allow` 合并。Qwen `DONE` 不产生输入副作用，只触发 Evidence Collection 和后续断言评价。

最终执行资格为：

```text
MCP capabilities
∩ Task action permissions
∩ PolicyDecision
∩ Deterministic window/coordinate validation
= execution eligibility
```

合成器窗口 role 本身不是风险等级，同一种 `click(x,y)` 可以是低风险导航，也可以触发删除、支付或外部发送。

## 16. 对外接口

迁移期保留现有工具，并增加 compact facade：

```text
gui_run(operation, ...)
```

支持：

```text
describe
observe
propose
decide
execute
evaluate
status
reset
trace
```

常规主控只需使用：

```text
propose → execute → verify
```

迁移期可保留 `assess` 和 `verify` 别名；`verify` 内部必须展开为 `collect_evidence → evaluate_assertions → reduce_task_state`，不能重新实现成一个拥有所有真值的单体 Verifier。`propose` 调用模型前必须先构造统一 ModelContext，不能把调用方的自由文本反馈直接拼接进 Qwen history。

只有诊断时才展开截图、模型 raw output、合成器原始窗口树和事件 trace。

`describe` 返回核心 schema 版本、已注册 Adapter/Provider、标准 fact path、风险 profile 和可用动作，避免主控依赖隐含约定。

其中 capabilities 与任务授权分开返回：

```json
{
  "capabilities": {
    "pointer": true,
    "keyboard": true,
    "window_geometry": true,
    "child_control_semantics": false
  }
}
```

## 17. 已发现问题在 v2 中的处理

| 已发现问题 | v2 处理方式 |
| --- | --- |
| `Qwen` 读成 `Owen` | 保存 `model_claim`，与独立文本真值比较，归因 `qwen_perception` |
| Calculator 打开成 Music | 对比 ModelExpectation、实际 `appId` 和 task assertion；有图标真值后再细分 perception/grounding |
| 清空后跳过步骤 | System Task State 不推进，Qwen 不能绕过未通过 assertion |
| `Ctrl+Z` 只删一个字符却 `DONE` | `DONE` 触发文本 evidence 采集；非空断言失败，Task State 不得完成 |
| 多动作或超长输出 | Proposal schema 只允许单动作，并在解析前限制长度 |
| 错误快捷键 | 平台白名单和 TaskContract 双重限制 |
| 桌面图标 | 窗口树仅记录桌面 role；低风险点击后用实际窗口结果验证 |
| 窗口被遮挡 | 执行前重新融合并拒绝，保留现有机制 |

## 18. 实施顺序

### Phase 1：建立 Canonical Model，不改变执行行为

- 定义 `AdapterDescriptor`、`CanonicalSnapshot` 和 `CanonicalWindowFact`；
- 实现 `CompositorAdapter` port；
- 用 `TreelandAdapter` 包装现有 `treeland-debug --tree`；
- 将现有空间融合改为只读取 CanonicalSnapshot；
- 将 `z_index` 降为可选 best-effort，并声明 stacking model 与 hit-test/occlusion 能力；
- 保存原始 Tree 为 `raw_artifact_ref`，停止向核心传播 Treeland 专有字段；
- 建立跨合成器 fixture 契约测试。

### Phase 2：建立单动作事务内核

- 定义 `ActionProposal`、`PolicyDecision` 和 `ExecutionReceipt`；
- 所有 Proposal 用 `based_on_snapshot` 记录来源并声明显式坐标空间；
- 实现控制层 ProposalGuard，并按动作依赖条件检查有效性；
- 实现 Semantic Resolution，区分模型 claim 与独立 semantic evidence；
- 统一桌面边界、窗口目标、遮挡、语义和权限检查；
- 将详细 Assessment 移到 `debug_ref`；
- Qwen 每轮只产生一个 ActionProposal；
- 保留旧 `qwen_cua_predict/execute` facade 作为兼容层。

### Phase 3：拆出可替换组件

- Qwen-CUA 实现 `ProposalProvider`；
- PyAutoGUI 实现 `InputExecutor`；
- `dde-am` 实现 `ApplicationLauncher`；
- Deepin 快捷键目录实现 `PlatformCapabilityProvider`；
- 平台快捷键和应用启动也生成 Proposal，不得旁路事务；
- 核心代码不得 import 具体 adapter。

### Phase 4：收紧 Evidence、Ledger 和上下文通信

- Evidence Provider 只声明和返回注册表中的标准 fact path；
- 实现 Assertion Evaluator 和确定性的 Task State Reducer；
- Ledger 只追加核心对象引用和 artifact 引用；
- 固定 compact/recovery ModelContext 投影；
- 正常响应只返回最小对象或引用，合成器原始窗口树、截图、模型输出和 trace 仅通过诊断接口读取；
- 接入 AT-SPI、OCR、DOM、应用 API 等独立 provider 时不得扩大合成器窗口事实。

自动闭环 `gui_run` 应在四个阶段完成并通过契约测试后实现，不得先于小核心稳定。

## 19. 验收条件

v2 至少满足：

1. Core 中不出现 `treeland-debug`、`dde-am`、Deepin、Qwen 或 PyAutoGUI 专有逻辑。
2. 合成器原始字段不能直接进入 TaskContract、ActionProposal、PolicyDecision 或 Assertion。
3. 不同合成器 fixture 可以归一化成语义一致的 CanonicalSnapshot。
4. 合成器多余字段不会进入 Canonical Model；完整原始数据只能由 `raw_artifact_ref` 读取。
5. Adapter 不支持或本轮未取得的事实返回 `unknown`，不能当作 `false`、失败或通过。
6. CompositorAdapter 只提供窗口、光标、桌面几何和坐标空间事实，不提供控件或业务语义。
7. 主控不会因为桌面 role 或合成器特有容器名自动拒绝桌面图标点击。
8. 每个 ActionProposal 只包含一个动作，并用 `based_on_snapshot` 记录 provenance，同时声明坐标空间。
9. 新 Snapshot 出现后必须重检 ProposalGuard；只有动作依赖条件失效才拒绝，不能因 Snapshot 整体不同自动 stale。
10. Qwen 点击、键盘操作、平台快捷键和应用启动都经过同一 PolicyDecision、ExecutionReceipt 和 AssertionResult 流程。
11. ExecutionReceipt 的 `delivered` 与任务成功严格分离。
12. Qwen `DONE`、模型预期和模型自述无法直接产生 `completed` 或 verified fact。
13. Evidence Provider 只产生注册表中的标准事实，不直接决定任务状态；Semantic Resolution 必须区分独立 semantic evidence 与模型 claim。
14. Assertion Evaluator 对 `unknown` 或 `conflict` 不得判定通过；只有 Task State Reducer 可以产生 `completed`。
15. 正常组件通信不传合成器原始完整窗口树、完整 Ledger、完整模型输出或大而空的统一 envelope。
16. Ledger 只保存不可变事件、核心对象引用和 artifact 引用；`assertion.evaluated` 使用 `assertion_result` 类型，只有 Reducer 接受后才能产生独立 `verified_fact` 事件。
17. 新增合成器只增加 Adapter，新增验证来源只增加 Evidence Provider，新增启动方式只增加 ApplicationLauncher。
18. 所有跨组件核心对象带 `schema_version`、稳定 ID、来源和必要引用。
19. 每个失败都有稳定错误码、责任组件和可执行恢复建议；安全拒绝与环境变化不计入组件错误率。
20. 现有接口在迁移期保持兼容，并可按阶段回退。
21. `z_index` 是可选、当前 Snapshot 内 best-effort 字段；Core 的点击安全不能要求所有 Adapter 提供全局完美全序。
22. Adapter 提供 `hit-test`、`partial-order`、`total-order`、`topmost-only` 或 `unavailable` 中明确的 stacking capability，能力不足时返回 `unknown`。
23. `ExecutionReceipt.delivered` 始终只表示执行后端接受并注入动作，不能被输入回执或后续 evidence 扩大为应用处理或业务成功。
