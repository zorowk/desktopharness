# Treeland AutoUI MCP v2 设计提案

## 1. 目标与适用范围

本设计用于重新划分主控、MCP 确定性逻辑层和 Qwen-CUA 图形识别层的职责，使三者能够协作完成安全、可验证的桌面操作。

本文描述目标架构，不表示相关接口已经全部实现。现有 `qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset` 和 `qwen_cua_status` 在迁移期间继续保留。

需要解决的主要问题：

1. 主控可能误用 Tree 信息，例如将 `BackgroundContainer` 理解为“没有可点击控件”。
2. Qwen 的自然语言观察、动作提案和完成判断容易被混为界面事实。
3. MCP 可以确定窗口、坐标、遮挡和执行状态，但不能据此判断窗口内部控件语义和业务结果。
4. Qwen 的历史动作、自述状态、当前截图和真实执行结果可能发生分歧。
5. 模型 `DONE`、动作执行成功和任务验收通过尚未完全分离。

核心原则：

> Qwen 提出它认为正确的动作；Tree 只提供窗口级事实；主控提供任务目标、风险策略和断言；不同 Evidence Provider 只采集证据，Assertion Evaluator 根据证据判断断言，Task State Reducer 决定任务状态。

## 2. 三层职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| 主控 | 定义任务、风险、允许动作和验收条件；决定执行、确认、重试或停止 | 不计算坐标；不从 Tree 猜控件语义；不把 Qwen 自述当作界面真值 |
| MCP 逻辑层 | 截图、Tree、坐标、窗口层级、遮挡、陈旧提案、执行、状态机和验证编排 | 不判断按钮文字、桌面图标语义或任务是否在视觉上“看起来完成” |
| Qwen-CUA | 从截图理解界面，提出下一步鼠标/键盘动作，输出自己声称观察到的内容 | 不直接执行；不决定安全性；不作为最终验收器；不替代确定性坐标和遮挡计算 |

## 3. 总体流程

```text
用户/主控
  │
  │ 1. TaskContract：目标、允许动作、风险、验收条件
  ▼
MCP Observation
  │
  │ 2. ObservationFrame：当前截图 + Tree 窗口事实
  ▼
Context Builder ◀──────────────────────────── Event Ledger
  │
  │ 3. ModelContext：TaskContract 投影 + 当前截图
  │    + VerifiedStateProjection + RelevantLedgerProjection
  │    不包含原始完整 Tree 或未经标记的历史
  ▼
Qwen-CUA
  │
  │ 4. PerceptionClaim：模型声称看到了什么
  │    ActionIntent：模型下一步想达到什么目的
  │    ActionProposal：模型建议如何操作
  ▼
MCP Assessment
  │
  │ 5. 白名单、坐标、顶层容器、遮挡、陈旧性、风险检查
  ▼
主控策略
  │
  │ 6. execute / needs-confirmation / reject
  ▼
Executor
  │
  │ 7. ExecutionEffect：请求动作、实际动作和真实副作用
  ▼
Evidence Providers
  │
  │ 8. Evidence Collection：API、文件、进程、窗口、AT-SPI、OCR 等证据
  ▼
Assertion Evaluator
  │
  │ 9. Assertion Evaluation：用证据评价 TaskContract 断言
  ▼
Task State Reducer
  │
  │ 10. Task State Transition：continue / retry / completed / failed
  ▼
Event Ledger
     11. 追加不可变审计事件；下一轮由 Context Builder 重新投影
```

Qwen 的直接输入是 `ModelContext`，不是原始完整 Tree、完整 Ledger 或未经筛选的对话历史。`ObservationFrame` 和 Ledger 先由 MCP 解释、校验和投影，再交给模型。

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

主控不再用一段自然语言同时表达目标、安全边界和验收规则，而是生成结构化任务契约。TaskContract 将“物理输入授权”和“业务语义策略”分开，因为 `click`、`write` 或 `hotkey` 本身不包含风险含义；风险取决于动作作用的对象、意图和效果。

还必须区分：

```text
MCP capabilities：系统技术上能够做什么
Task mechanical_permissions：本次任务授权使用哪些输入机制
Task semantic_policy：本次任务允许这些输入产生什么业务效果
```

MCP capabilities 由 `describe` 返回，不由 TaskContract 声明。TaskContract 只授权系统能力的一个子集。

```json
{
  "task_id": "open-text-editor",
  "goal": "打开一个新的空白文本编辑器文档",
  "mechanical_permissions": {
    "pointer": {
      "move": true,
      "click": true,
      "double_click": true,
      "drag": false,
      "scroll": false
    },
    "keyboard": {
      "text_input": false,
      "keys": [],
      "shortcuts": []
    }
  },
  "semantic_policy": {
    "navigation": "allow",
    "open_application": "allow",
    "content_edit": "confirm",
    "settings_change": "confirm",
    "external_side_effect": "confirm",
    "destructive": "deny",
    "authentication": "deny",
    "unknown": "confirm"
  },
  "runtime_limits": {
    "max_steps": 5,
    "max_retries": 2
  },
  "required_postconditions": [
    {
      "assertion_id": "editor-opened",
      "expression": {
        "path": "active_window.app_id",
        "operator": "equals",
        "expected": "deepin-editor"
      },
      "evidence_policy": {
        "allowed_providers": ["treeland-window", "process"],
        "preferred_providers": ["treeland-window"],
        "allow_model_claim_only": false
      }
    }
  ]
}
```

用于代码验收时，可定义更严格的契约：

```json
{
  "goal": "验证提交按钮可以创建一条记录",
  "mechanical_permissions": {
    "pointer": {
      "move": true,
      "click": true,
      "double_click": false,
      "drag": false,
      "scroll": true
    },
    "keyboard": {
      "text_input": true,
      "keys": ["enter", "tab", "escape"],
      "shortcuts": []
    }
  },
  "semantic_policy": {
    "navigation": "allow",
    "content_edit": "allow",
    "external_side_effect": "confirm",
    "destructive": "deny",
    "unknown": "confirm"
  },
  "runtime_limits": {
    "max_steps": 12,
    "max_retries": 2
  },
  "required_postconditions": [
    {
      "assertion_id": "record-created",
      "expression": {"path": "api.status", "operator": "equals", "expected": 201},
      "evidence_policy": {"preferred_providers": ["application-api"]}
    },
    {
      "assertion_id": "success-visible",
      "expression": {"path": "dom.text", "operator": "contains", "expected": "创建成功"},
      "evidence_policy": {"preferred_providers": ["dom"]}
    },
    {
      "assertion_id": "test-process-passed",
      "expression": {"path": "process.exit_code", "operator": "equals", "expected": 0},
      "evidence_policy": {"preferred_providers": ["process"]}
    }
  ]
}
```

迁移期可把旧的 `{"type": "active_window", ...}` 等 postcondition 编译为上述 assertion 结构。新协议中每个必要断言必须具有稳定 `assertion_id`、可执行表达式和 evidence policy；如果没有声明 provider 优先级，则使用第 11 节的默认冲突规则。

机械权限采用正向授权：未授权即禁止，不再同时维护 `allowed_actions` 和 `forbidden_actions`，避免规则交叉。

语义策略的值固定为 `allow`、`confirm` 或 `deny`。一个动作可以同时命中多个语义标签，最终决策采用固定优先级：

```text
deny > confirm > allow
```

例如点击“删除全部云端数据”可能同时命中 `content_edit`、`external_side_effect` 和 `destructive`，最终结果为 `deny`。

任务契约属于主控责任，Qwen 不能自行修改机械权限、语义策略、运行限制或验收条件。

## 5. ObservationFrame：传感器事实

每次观察生成不可变 Frame：

```json
{
  "frame_id": "frame-123",
  "captured_at": "...",
  "screenshot": {
    "sha256": "...",
    "width": 1920,
    "height": 1080
  },
  "desktop": {
    "bounds": {"x": 0, "y": 0, "width": 1920, "height": 1080}
  },
  "top_level_containers": [
    {
      "app_id": "",
      "title": "",
      "container": "BackgroundContainer",
      "geometry": {},
      "layer": -2,
      "z": 0,
      "visible": true
    }
  ]
}
```

协议必须显式声明 Tree 的能力边界：

```json
{
  "tree_capabilities": {
    "window_geometry": true,
    "stacking_order": true,
    "visibility": true,
    "workspace": true,
    "child_controls": false,
    "desktop_icons": false,
    "window_text": false,
    "control_semantics": false
  }
}
```

Tree 只用于顶层窗口/容器的几何、层级、可见性、工作区、遮挡和坐标命中。`BackgroundContainer` 可以包含可点击桌面图标，不能据容器类型判断图标或控件是否存在。

## 6. Qwen 输出的概念拆分

Qwen 的单轮输出在逻辑上必须拆成 `PerceptionClaim`、`ActionIntent`、`ActionProposal` 和 `ModelExpectation`。实现上可以继续使用一个 JSON 对象，但字段含义和故障责任必须独立。

```json
{
  "proposal_id": "proposal-123",
  "frame_id": "frame-123",
  "model": "qwen3_rl",
  "perception_claim": {
    "observation_text": "I can see a Text Editor icon",
    "objects": [
      {
        "label": "Text Editor icon",
        "region": [120, 250, 150, 290]
      }
    ]
  },
  "action_intent": {
    "goal": "open Text Editor",
    "target": "Text Editor desktop icon"
  },
  "action_proposal": {
    "type": "click",
    "screenshot_coordinate": [136, 273]
  },
  "model_expectation": {
    "active_window_app_id": "deepin-editor"
  },
  "completion_claim": false,
  "raw_output": "..."
}
```

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

`model_expectation` 表示模型预测动作后会发生什么，仅用于诊断和后续反馈。它不能替代 TaskContract 中的 `required_postconditions`。

必须保持：

```text
model_expectation
≠ required_postcondition
```

前者由 Qwen 提出；后者由主控的 TaskContract 定义，并由 Assertion Evaluator 使用独立 evidence 判断。

## 7. ActionAssessment：MCP 确定性裁决

MCP 对动作提案返回独立评估：

```json
{
  "assessment": {
    "status": "eligible",
    "mechanical_permission": {
      "passed": true,
      "capability": "pointer.click"
    },
    "coordinate_inside_desktop": true,
    "top_level_target": {
      "container": "BackgroundContainer"
    },
    "target_stable": true,
    "frame_fresh": true,
    "semantic_policy": {
      "model_claimed_tags": ["open_application"],
      "resolved_tags": ["open_application"],
      "decision": "allow",
      "decision_source": ["task-contract"]
    }
  },
  "control_truth": {
    "status": "unavailable",
    "reason": "Treeland tree has no child-control semantics"
  }
}
```

`control_truth.status=unavailable` 仅表示 MCP 没有控件级真值，不表示：

- Qwen 没有看懂；
- 图标不存在；
- 动作错误；
- 动作必须被拒绝。

`BackgroundContainer` 上的低风险点击可以执行，只要：

- 坐标位于桌面内；
- 执行前仍命中同一顶层容器；
- 目标没有被其他窗口覆盖；
- TaskContract 的机械权限允许 `pointer.click`；
- 解析出的语义标签经 policy 合并后结果为 `allow`。

Qwen 可以在 `ActionIntent` 中给出语义标签，但它们仍属于模型自述。主控/MCP 的 `semantic_assessment` 必须明确区分 `model_claimed_tags` 和依据 TaskContract、界面真值或用户确认解析出的 `resolved_tags`。无法解析时使用 `unknown` 策略，不能默认为低风险。

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

- Tree 无法证明它是编辑器图标；
- 也不能否定模型声称的图标；
- 点击本身低风险且窗口级安全时允许执行；
- 点击后检查是否出现 `deepin-editor`。

## 9. 统一状态信封

借鉴本地 skills 的 compact facade，所有 MCP 操作统一返回：

```json
{
  "protocol_version": 2,
  "status": "ok",
  "operation": "propose",
  "data": {},
  "effects": [],
  "evidence": [],
  "assertion_evaluation": {},
  "task_state": {},
  "attributions": [],
  "error": null,
  "retry": null,
  "metrics": {}
}
```

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

## 10. ExecutionEffect：实际执行与真实副作用

模型提案、批准动作、实际注入动作和真实副作用必须分开：

```json
{
  "execution_effect": {
    "requested_action": {
      "type": "click",
      "coordinate": [136, 273]
    },
    "approved_action": {
      "type": "click",
      "coordinate": [136, 273]
    },
    "executed_action": {
      "type": "click",
      "coordinate": [136, 273]
    },
    "status": "executor-reported-success",
    "delivery_status": "unknown",
    "effects": [
      {
        "type": "mouse_click",
        "coordinate": [136, 273],
        "executed": true,
        "timestamp": "..."
      }
    ]
  }
}
```

当前输入接口返回成功，只能证明执行器已调用输入动作，不能证明应用收到动作或产生了模型预期的业务效果。`window_opened`、文本变化等后置结果必须由 Evidence Provider 另行采集，不能写进 `ExecutionEffect` 冒充执行事实。

尤其必须保持：

```text
ActionIntent: clear calculator
≠ ExecutionEffect: click(420,610) was issued
≠ AssertionResult: calculator_display_empty == passed
```

因此 Context Builder 不得生成含糊字段 `previous_action: clear`。它必须分别投影模型意图、实际执行动作和断言结果；正常情况下只需向 Qwen 提供后两者，历史意图仅用于诊断。

如果 Qwen 提议的坐标本身偏离目标，属于 `qwen_grounding`；如果批准坐标正确但执行器注入到其他位置，属于 `mcp_executor`。

未执行的动作应返回：

```json
{
  "proposal": {"action": "click"},
  "effects": [],
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
3. Treeland 窗口 `appId/title`、层级和位置；
4. 独立 OCR 或人工标注；
5. Qwen 对截图的重新观察。

上述顺序是默认可信度参考，不是对所有断言都适用的固定全局排序。TaskContract 应为关键断言声明允许的 provider 和优先级。例如文件内容断言应优先使用文件系统，而不是窗口标题。

统一 Evidence 结构：

```json
{
  "evidence_id": "evidence-42",
  "provider": "treeland-window",
  "collected_at": "2026-09-02T10:20:31Z",
  "subject": {
    "display_id": 0,
    "window_id": 184,
    "frame_id": "frame-123"
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
≠ required_postcondition
≠ evidence
≠ assertion_result
≠ task_state
```

`model_expectation` 用于评价模型是否正确预测动作效果；`required_postcondition` 是主控定义的验收规则；`evidence` 是 provider 取得的材料；`assertion_result` 是 evaluator 的判断；`task_state` 是 reducer 产生的状态。

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
| ActionAssessment | 应拒绝却允许，或错误拒绝 | `mcp_validation` |
| ExecutionEffect | 实际注入动作与批准动作不一致 | `mcp_executor` |
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

ActionAssessment:
  坐标命中 BackgroundContainer，窗口级可执行

ExecutionEffect:
  无，主控未执行

EvidenceCollection:
  无

AssertionEvaluation:
  无

结论:
  无法判断 perception、planning 和 grounding 是否正确
```

### 12.2 Calculator 打开成 Music 样本

该样本可确定：批准的点击动作由 MCP 按坐标准确执行，但结果窗口为 `deepin-music`，不满足 `deepin-calculator` 的 required postcondition。

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
assessment
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
mcp-assessment
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
MCP_ASSESSMENT_FALSE_ALLOW
MCP_ASSESSMENT_FALSE_REJECT
MCP_REPROJECTION_ERROR
EXECUTOR_ACTION_MISMATCH
EXECUTOR_ACTION_FAILED
```

#### 环境与结果

```text
ENVIRONMENT_TARGET_CHANGED
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
  mcp-assessment                    0.6%
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
frame captured
model claim recorded
proposal generated
assessment passed
proposal rejected
execution requested
execution succeeded / failed
evidence collected
assertion passed / failed / unknown / conflict
task state transitioned
attribution recorded
session reset
```

Ledger 是“系统运行中实际记录过什么”的权威历史，不表示其中每个 payload 都是界面真值。每个事件必须标记自己的认识论类型：

```text
verified_fact       独立 evidence 支持并通过断言的事实
model_claim         模型声称看到或认为的内容
action_intent       模型希望达到的下一步效果
action_proposal     模型提出的动作
assessment_result   MCP 的确定性评估
execution_effect    执行器实际注入的动作与副作用
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
  "event_type": "assertion_result",
  "epistemic_type": "verified_fact",
  "caused_by": ["event-106", "event-107"],
  "frame_id": "frame-123",
  "payload": {
    "assertion_id": "editor-opened",
    "status": "passed"
  },
  "artifact_refs": ["evidence-42"]
}
```

`sequence` 在单个 task 内单调递增；`caused_by` 记录因果输入，而不是只依赖相邻顺序猜测。已追加事件不原地修改，后续纠错通过新的 superseding event 表达。截图、原始模型输出和 provider 原始响应可以单独存储，但必须由 `artifact_refs` 可追溯。

因此必须保持：

```text
Ledger = 完整、不可变、可审计的运行记录
Context = 根据当前决策目的对 Ledger 的受控投影
```

不能使用 `Context = history`，也不能因事件进入 Ledger 就把其中的 `model_claim` 升级为 `verified_fact`。

### 13.3 Context Builder：Ledger 的策略化投影

Qwen 每轮只能接收统一的 `ModelContext`，不能由调用方临时拼接原始 Tree、自由文本 history 或未标记的 `previous_feedback`：

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
        "source": "treeland-tree",
        "evidence_ref": "evidence-window-52",
        "freshness": "current"
      }
    ]
  },
  "recent_execution_effect": {
    "executed_action": {
      "type": "click",
      "coordinate": [420, 610]
    },
    "status": "executor-reported-success",
    "delivery_status": "unknown",
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
+ 最近真实 ExecutionEffect
+ 独立 AssertionResult
+ 当前任务与策略约束
→ Qwen 下一动作
```

其中 `estimated_target_app` 只表示 MCP 根据现有 Tree 几何和层级推导的顶层候选，不表示合成器已经证明输入实际交付给该应用。

一个事实只有同时满足以下条件才能进入 `verified_state_projection`：

1. 来自被允许的 Evidence Provider，并通过 MCP 的来源、作用域、时效和冲突规则；由任务断言推导时还须被 Assertion Evaluator 接受；
2. 对当前 frame、窗口身份和环境状态仍然有效；
3. 会影响当前步骤的下一动作或恢复决策；
4. 不包含从窗口级事实跳跃推导出的控件或业务语义；
5. 保留 source、evidence/event 引用和 freshness。

以下内容不得进入 `verified_state_projection`：

- 原始完整 Tree 或完整 Ledger；
- 已过期或已被后续事件取代的事实；
- Qwen 过去的 `model_claim`；
- 未经证据支持的控件名称、按钮作用或业务结果；
- 将 `ActionIntent` 改写成已经发生的动作效果，例如 `previous_action: clear`。

如果“显示非空”只有 Qwen 看截图后的自述，该声明只保存在 Ledger 供诊断，断言保持为 `unknown`，不得放入 `verified_state_projection`，也不能生成 `assertion_feedback.status = failed`。只有 clipboard、AT-SPI、DOM、独立 OCR、人工标注或任务允许的其他 evidence 支持时，才可产生正式失败断言。

Context Builder 再根据任务阶段、失败状态、token 预算和视觉预算选择投影策略。至少支持：

```text
compact               当前 frame、TaskContract、最近 effect 和未完成断言
visual-heavy          更多近期截图和窗口变化，减少无关文本
recovery              最近实际动作、失败断言、可靠 evidence 和恢复边界
verification-focused  相关 evidence、冲突来源和待满足断言
planning-reset        保留任务事实与约束，丢弃可能造成路径依赖的模型废话
```

典型 recovery context 只需要：

- 最新有效截图及其 frame 身份；
- 最近批准动作和实际 `ExecutionEffect`；
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

语义标签是集合而不是互斥枚举。多标签按 `deny > confirm > allow` 合并。Qwen `DONE` 不产生输入副作用，只触发 Evidence Collection 和后续断言评价。

最终执行资格为：

```text
MCP capabilities
∩ Task mechanical_permissions
∩ Semantic policy decision
∩ Deterministic window/coordinate validation
= execution eligibility
```

Tree 容器类型本身不是风险等级，同一种 `click(x,y)` 可以是低风险导航，也可以触发删除、支付或外部发送。

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
assess
execute
verify
status
reset
trace
```

常规主控只需使用：

```text
propose → execute → verify
```

这里的 `verify` 只是迁移期 facade 名称，内部必须展开为 `collect_evidence → evaluate_assertions → reduce_task_state`，不能重新实现成一个拥有所有真值的单体 Verifier。`propose` 内部必须先构造统一 `ModelContext`，不能把调用方的自由文本反馈直接拼接进 Qwen history。

只有诊断时才展开截图、模型 raw output、完整 Tree 和事件 trace。

`describe` 返回操作 schema、风险要求、可能的 effects、Evidence Provider 能力、Assertion Evaluator 能力和 Tree capability，避免主控依赖隐含约定。

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
| Calculator 打开成 Music | 对比 ModelExpectation、实际 `appId` 和 required postcondition；有图标真值后再细分 perception/grounding |
| 清空后跳过步骤 | System Task State 不推进，Qwen 不能绕过未通过 assertion |
| `Ctrl+Z` 只删一个字符却 `DONE` | `DONE` 触发文本 evidence 采集；非空断言失败，Task State 不得完成 |
| 多动作或超长输出 | Proposal schema 只允许单动作，并在解析前限制长度 |
| 错误快捷键 | 平台白名单和 TaskContract 双重限制 |
| Background 图标 | Tree 仅记录背景容器；低风险点击后用实际窗口结果验证 |
| 窗口被遮挡 | 执行前重新融合并拒绝，保留现有机制 |

## 18. 实施顺序

### Phase 1：协议与命名，不改变执行行为

- 增加统一 envelope；
- 将 `observation` 重命名或兼容映射为 `perception_claim.observation_text`；
- 明确 `ActionIntent`、`ActionProposal` 和 `ModelExpectation`；
- 分离 `execution_eligibility` 与 `proposal_correctness`；
- 返回 Tree capability 声明；
- 保留旧字段兼容。

### Phase 2：TaskContract、Evidence 与 Assertion

- 增加 mechanical permissions、semantic policy、runtime limits 和 required postconditions；
- 实现多语义标签与 `deny > confirm > allow` 决策；
- 定义统一 Evidence schema、provider registry、时效和冲突规则；
- 实现窗口 `appId/title`、光标、截图变化等基础 Evidence Provider；
- 实现 Assertion Evaluator 和确定性的 Task State Reducer；
- Qwen `DONE` 必须经过 Evidence Collection、Assertion Evaluation 和 Task State Transition。

### Phase 3：事件账本与上下文构造

- 保存 proposal、assessment、effect、evidence、assertion result 和 state transition；
- 固定统一 `ModelContext` schema，并让 `propose` 只通过 Context Builder 调用 Qwen；
- 为每个失败、拒绝、环境变化和证据不足事件写入稳定 attribution；
- 支持 primary cause 与 contributing factors；
- 区分 verified facts、model claims 和其他事件类型；
- 实现 compact、visual-heavy、recovery 等 Context Builder 策略；
- 分别限制截图、文本 token 和事件数量；
- 先评测 Context 投影策略，再将不同视觉历史长度作为兼容性基线。

### Phase 4：自动闭环

- 实现 `gui_run`；
- 支持预定义状态机验收；
- 加入无进展、重复动作、错误恢复和最大步骤保护。

### Phase 5：增强控件真值

按需接入独立 evidence provider：

- AT-SPI；
- OCR；
- DOM；
- 应用 API；
- OmniParser 对照。

这些 provider 独立于 Treeland Tree，不能将控件语义混入窗口树事实。

## 19. 验收条件

v2 至少满足：

1. 主控不会因为 `BackgroundContainer` 自动拒绝桌面图标点击。
2. 所有模型自然语言观察明确标记为 `model_claim`。
3. Qwen `DONE` 无法直接产生任务成功。
4. Tree 只参与窗口级确定性校验。
5. 每次真实副作用都记录在 `effects`。
6. 每个失败都有责任层、稳定错误码和可执行重试建议。
7. pending、遮挡、窗口移动和执行失败都有契约测试。
8. 预定义 GUI 验收完全依赖独立断言决定通过与否。
9. 未执行且没有独立真值的模型提案不计成功或失败。
10. 现有接口在迁移期保持兼容，能够按阶段回退。
11. 能分别归因 perception、planning、grounding、protocol、assessment、execution、environment、outcome、evidence-collection、assertion-evaluation、state-transition 和 policy 事件。
12. `model_expectation` 与 TaskContract 的 `required_postcondition` 不得互相替代。
13. MCP capabilities 与 Task mechanical permissions 明确分离。
14. 动作机械类型不直接决定风险；风险由 semantic policy 解析。
15. 多语义标签使用稳定的 `deny > confirm > allow` 优先级，`unknown` 不得静默自动执行。
16. Attribution 的 `stage`、`owner`、`code`、`event_kind` 和 `evidence_status` 使用稳定枚举。
17. 安全拒绝、环境变化和正常策略拒绝不得计入组件错误率。
18. 每个失败任务最多有一个 primary attribution，并可保留多个 contributing factors。
19. Benchmark 按维度使用匹配的有效分母，同时报告未归因与证据不足样本。
20. Evidence Provider 只产生带来源、作用域、时效和质量信息的 evidence，不直接决定任务成功。
21. Assertion Evaluator 只使用 TaskContract 断言和引用的 evidence，`unknown` 或 `conflict` 不得视为通过。
22. 只有 Task State Reducer 可以产生 `completed`，且所有必要断言必须通过。
23. Event Ledger 保存完整审计历史，并明确区分 `verified_fact`、`model_claim`、`execution_effect`、`evidence` 等事件类型。
24. Qwen Context 必须是 Ledger 的可追溯投影；每个上下文项可定位到原始 event id。
25. Context Builder 分别管理视觉、文本和事件预算，支持至少 compact 与 recovery 两种策略。
26. Qwen 每轮直接输入必须是统一 `ModelContext`，至少包含当前 screenshot、TaskContract 投影、VerifiedStateProjection、最近 ExecutionEffect、Assertion feedback 和当前约束。
27. 原始完整 Tree、完整 Ledger、自由文本 history 和未标记 `previous_feedback` 不得直接进入 Qwen 上下文。
28. `ActionIntent`、`ExecutionEffect` 和 `AssertionResult` 不得互相替代；`previous_action: clear` 之类混合语义字段不得出现在新协议。
29. 只有独立 evidence 支持时，`display_not_empty` 等内容断言才能作为正式 assertion feedback；仅有 Qwen 自述时只在 Ledger 保留 `model_claim`，对应断言保持 `unknown`。
