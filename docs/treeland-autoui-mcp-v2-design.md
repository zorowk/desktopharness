# AutoUI MCP v2.2 当前架构

本文描述仓库当前实现，而不是迁移提案。包版本为 `0.2.0`；领域与 JSON 配置
schema 为 `2`，运行时架构 revision 为 `2.2`。三者用途不同，包 SemVer 不随架构
revision 绑定。

## 核心设计哲学：简化、紧凑、功能完整

延续“小核心、大扩展、克制通信”：Core 负责通用执行编排和状态，平台、模型、输入和
证据实现通过 ports/adapters 接入；默认响应只传递必要事实。减少重复判断、包装和历史
分支，保留已有操作能力及结果验证。

本次简化不只针对安全审批，而是覆盖执行链、运行态、记录与诊断、协议兼容、配置和依赖。
不以文件数和代码行数下降代替架构改进，也不为了“少一层”而丢失现有能力。每一层都应回答：
删除后会失去什么实际能力？只有透传、重复表达或假设需求的层才应删除或合并。

保留的是桌面自动化必须具备的事实链：

```text
CanonicalSnapshot → ActionProposal → ExecutionReceipt
                  → EvidenceRecord → AssertionResult → TaskState
```

不保留的是把这条事实链重复包装为语义审批、策略决策、确认状态和默认持久化控制流。
单用户桌面的调用者已经通过提交任务表达了执行意图；系统应负责**正确执行、可验证完成、
可追溯诊断**，而不要求调用者预测动作类型或为普通输入反复确认。

系统将模型驱动的桌面自动化收敛为一条可验证的执行链：模型只提出 canonical actions，
Core 只做技术校验、注入编排、证据评估和状态归约。它不包含语义审批、逐动作确认、
任务级权限覆盖或通用 policy provider。

这里的“直通”不是跳过校验：未知动作、错误参数、桌面越界、能力缺失、目标/焦点变化和
重复执行仍会阻止注入。 `ExecutionReceipt.delivered` 只表示输入已送达，绝不等同于
任务成功；`completed` 只能由必需断言通过得到。

## 模块图

```text
 MCP / LangChain Client
          │
          ▼
 FastMCP transport ──────── ServerConfig
 (streamable HTTP / SSE)    (schema, auth, recording)
          │                         │
          └────────────┬────────────┘
                       ▼
             Composition Root
             mcp_autogui_main
              │              │
              │              └──── Desktop capability / shortcut /
              │                    application tools
              ▼                         │
       AutoUIFacade / gui_run           │
       gui_diagnostic (conditional)     │
              │                         │
              └───────────┬─────────────┘
                          ▼
 ┌──────────────── Core: platform-independent ────────────────┐
 │  CoreOrchestrator (bounded loop + execution lock)           │
 │       │            │             │              │           │
 │       ▼            ▼             ▼              ▼           │
 │ TaskRepository ProposalValidator AssertionEvaluator AuditRecorder
 │ runtime state  prepare/recheck   evidence → state  memory + optional audit
 └───────┬────────────┬─────────────┬───────────────┬─────────┘
         │            │             │               │
         ▼            ▼             ▼               ▼
  ProposalProvider CompositorPort ActionExecutor Frame/EvidenceProvider
         │            │             │               │
         ▼            ▼             ▼               ▼
    Qwen CUA     Treeland/Deepin  PyAutoGUI   PyAutoGUI / Window /
                                               AT-SPI / OmniParser

 AuditRecorder ─ ─ ─ ─ ─ ─ ─ ─ ─ ─► optional audit directory
                                      JSON objects + CSV ledger
```

实线表示正常依赖或调用；虚线表示由配置决定的入口或持久化。桌面快捷键和应用启动
工具不绕过 Core：它们通过 `CoreDesktopTransactionRunner` 构造任务和 proposal，再走同一
validator、执行锁、receipt 与状态机。

## 运行路径

```text
Client → gui_run → CoreOrchestrator
                     │
                     ├─ observe + build context
                     ├─ ProposalProvider.propose()
                     │       └─ ordered ActionProposal
                     ├─ ProposalValidator.prepare()
                     ├─ [execution lock] observe + recheck()
                     ├─ ActionExecutor.execute() × N, fail fast
                     │       └─ atomic receipts → aggregate receipt
                     ├─ EvidenceProvider.collect()
                     └─ AssertionEvaluator.reduce() → TaskState
              ← compact public state
       ← run/status response
```

`done` 不会被注入。没有 assertions 的任务，只有在先前或当前 sequence 完整送达后才可
进入 `delivered-unverified`；否则失败。带 assertions 的任务即使模型输出 `done` 也仍须
收集证据。

## 模块职责与边界

| 模块 | 职责 | 不负责 |
| --- | --- | --- |
| `mcp_autogui_main` | 读取已选组件、组装运行时、注册 MCP tools 和线程池 | 执行桌面逻辑或保存任务状态 |
| `AutoUIFacade` | 将 `describe/run/status/reset` 转为紧凑公共响应 | 公开控制器内部对象或绕过 Core |
| `CoreOrchestrator` | 观察、提案、校验、执行、证据与有界循环；持有跨任务执行锁 | 平台 API、模型协议或 transport |
| `TaskRepository` | 当前进程内任务 contract/state、snapshot、proposal、receipt、结果与去重 | 审计持久化或重启恢复 |
| `ProposalValidator` | canonical action 的结构、参数、能力、坐标、目标依赖和执行前重检 | 推断动作语义或授权用户意图 |
| `AssertionEvaluator` | 基于当前 evidence/snapshot 归约断言结果 | 注入输入或规划下一动作 |
| `AuditRecorder` | 保留运行期对象/事件，按 recording 配置投影并写出审计 | 作为状态权威或使输入回滚 |
| ports | Core 与模型、桌面、输入、画面、证据的稳定接口 | 彼此直接调用 |
| adapters | Qwen、Treeland/Deepin、PyAutoGUI、AT-SPI、OmniParser 等具体实现 | 修改 Core 状态机 |

## 执行、校验与状态

`ProposalValidator.prepare(snapshot, proposal)` 返回 `PreparedProposal` 或
`ValidationFailure(reason_code, retryable)`。PreparedProposal 只活在执行临界区，包含
canonical action 与它实际依赖的坐标空间、命中目标、焦点窗口和执行器能力。
`recheck` 以最新 observation 重查这些依赖；任何依赖变化都会零注入返回 retryable failure。

一个 proposal 是非空有序 action 序列。Core 对每个 action 调 executor，收到失败或不确定
送达立即停止剩余 action，并写入逐动作 receipt。终态 receipt 缓存在 TaskRepository，同一
proposal 再次 execute 时直接返回原 receipt，避免重放。

| 事件 | 状态与预算 |
| --- | --- |
| 不可重试 validation failure | `failed`；零注入，不消耗 retry |
| stale / retryable validation failure | `retrying`；消耗一次 retry，预算耗尽后 `failed` |
| executor failed、partial 或 uncertain | `failed`；不自动重放 sequence |
| 断言明确失败且可恢复 | `retrying`；消耗一次 retry |
| 断言明确失败且不可恢复 | `failed` |
| 断言 unknown/conflict | 保持 `running`，等待下一轮 |
| 所有必需断言通过 | `completed` |
| 空断言且已完整送达、模型结束 | `delivered-unverified` |

接受并准备执行的 sequence 才消耗 `max_steps`；`prepare`/`recheck` 失败与纯 `done` 都不
消耗 step。 `max_steps` 或 `max_retries` 耗尽且尚未达到终态时失败。

## 配置、连接与部署限制

`ServerConfig` 是唯一 JSON 配置解析点，要求 `schema_version: 2`。它选择 transport、桌面
backend、proposal/evidence providers、静态动作限制和 recording。 `RuntimeDescription` 只暴露
过滤后的有效配置，绝不暴露 bearer token 或其值。

| transport.auth.mode | 约束 |
| --- | --- |
| `loopback`（默认） | host 必须是 `127.0.0.1` 或 `::1` |
| `trusted-proxy` | MCP upstream 仍必须绑定 loopback；TLS/外部鉴权由可信代理承担 |
| `bearer-token` | 从非空 `token_env` 读取 token，由 FastMCP verifier 在 facade 前验证 |

`deployment.denied_actions` 是唯一保留的部署级动作限制。它是静态配置，由 validator
以 `ACTION_RESTRICTED` 拒绝，任务本身无法覆盖。它不是语义策略系统。

## recording 与诊断

运行态始终在内存中；TaskRepository 是唯一的任务状态权威。AuditRecorder 使用独立的
runtime store/ledger，记录失败不会让已送达的 action 变为失败。

| `recording.audit` | `recording.diagnostic` | 行为 |
| --- | --- | --- |
| false | false | 只保留进程内运行态，不注册 `gui_diagnostic` |
| true | false | 持久化最小任务、proposal、receipt、断言与事件投影；不保存详细诊断材料 |
| true | true | 额外持久化诊断对象/工件，并注册 `gui_diagnostic` |

随包配置 `config/mcp-autoui.json` 采用 `true` / `true`：桌面验收需要只在该模式注册的
observe 与 screenshot 证据。三种模式的组合都可运行，`false` / `false` 时服务只暴露
`gui_run`。

`diagnostic=true` 且 `audit=false` 是配置错误。审计文件由 JSON object store 与 CSV ledger
保存，受目录、保留天数和大小上限控制；reset 会清除该任务的内存引用，不会让运行态从审计
文件恢复或重放。

## 公开接口

普通客户端只使用 `gui_run`：

```text
describe → run → status → reset
```

`gui_diagnostic` 仅在诊断模式注册，可分阶段调用 `observe/propose/prepare/execute/evaluate/trace`。
它用于排障，不是第二条业务执行链。服务还提供由 desktop backend 支持的 capabilities、shortcut、
applications 与 application launch tools；它们仍共享同一个 Core transaction runner。

## 已删除的控制流

当前运行时不再有 `ActionGate`、`PolicyDecision`、`SemanticResolution`、`needs-confirmation`、
`confirm`、任务 `permissions`、`policy_profile` 或 `policy_overrides`。旧 schema/旧 HTTP backend
兼容也不在当前主路径；历史行为应通过 Git 历史查阅，而非在运行时回退。
