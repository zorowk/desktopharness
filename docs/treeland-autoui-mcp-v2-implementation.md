# AutoUI MCP v2 实现与扩展指南

本文记录 `treeland-autoui-mcp-v2-design.md` 对应的已实现基线。

## 已实现边界

- `core/` 包含 canonical 协议对象、Action Gate、ProposalGuard、语义策略、Assertion Evaluator、确定性 Task State Reducer、append-only Ledger、Context Builder 和薄 Orchestrator。
- `ports/` 定义 compositor、frame、proposal、policy、executor、application launcher、platform capability 和 evidence 契约。
- `adapters/` 包含 Treeland 与严格 canonical-JSON compositor adapter、Qwen-CUA proposal adapter、PyAutoGUI frame/input adapter、Deepin capability adapter、`dde-am` launcher 和 compositor-window evidence provider。
- `facade.py` 实现紧凑的 `gui_run` 操作和诊断对象查询。

Core 不包含 Treeland、Deepin、Qwen、PyAutoGUI 或 `dde-am` 的 import 和专用分支。合成器原始树与截图保存在对象存储中；正常协议对象只携带引用。

## 事务不变量

v2 路径对单个动作执行：

```text
observe -> propose -> decide -> guard recheck -> execute
        -> observe -> collect evidence -> evaluate -> reduce state
```

- `based_on_snapshot` 只记录来源。Action Gate 从该 Snapshot 推导 ProposalGuard，并只在最新 Snapshot 上重检动作实际依赖的条件。
- Proposal 的 semantic intent 是模型或主控 claim。只有独立 adapter/provider tag 才作为策略真值；没有独立证据时采用 `unknown -> confirm`，除非 TaskContract 显式覆盖。
- `ExecutionReceipt.delivered`、Evidence、AssertionResult 和 TaskState 是不同对象。只有 Reducer 能产生 `completed`。
- Evidence Provider 只能输出注册过的 fact path。缺失、过期、仅模型声明或冲突的 evidence 不能使断言通过。
- Ledger Event 只保存对象/artifact 引用和因果 event ID，不嵌入原始树、截图或完整模型输出。

## 新增合成器

在 `adapters/compositor/` 中实现 `ports.compositor.CompositorAdapter`，并返回 `CanonicalSnapshot`。只映射 `CanonicalWindowFact` 定义的字段；其余原生字段丢弃，或将完整原始 payload 保存到 `raw_artifact_ref`。Adapter 必须声明真实 stacking model；无法提供 hit test、identity、visibility 或 cursor 时不能伪装为否定事实。

已经输出 canonical JSON 的 compositor bridge 可以直接使用 `CanonicalJsonAdapter`。它会有意忽略未知输入字段。

## 新增 Evidence 或执行后端

Evidence Provider 声明 `core.facts.STANDARD_FACT_PATHS` 的子集，并只返回 `EvidenceRecord`，不能判断任务完成。只有当新 fact 具有稳定语义且有核心消费者时，才扩展注册表。

Input/Application backend 只返回 `ExecutionReceipt`。窗口变化或业务结果不能写入回执，必须由独立 Evidence Provider 采集。

## 迁移兼容

现有 `qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset` 和 `qwen_cua_status` 接口继续保留原响应形状。`desktop_shortcut_invoke` 与 `desktop_application_launch` 已在内部创建并执行 v2 Proposal，同时保留旧返回字段。新的 `gui_run` Qwen 路径强制每个 Proposal 只有一个 canonical action。
