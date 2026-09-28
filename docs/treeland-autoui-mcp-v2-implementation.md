# AutoUI MCP v2.2 实施指南

本文是 v2.2 的实施清单和进度入口。目标是简化、紧凑、核心功能完整，延续“小核心、大扩展、克制通信”。

简化覆盖执行、状态、记录、协议和依赖。保留能力与失败语义，不保留全部现有机制；“可选化”只有在同时消除核心依赖时才算简化。

- 目标架构：[v2.2 架构设计](treeland-autoui-mcp-v2-design.md)
- 桌面验收：[手工验收指南](manual-test-guide.md)
- 使用入口：[README](../README.md)

## 当前状态与执行约定

S1、S2、S3 已在当前工作区完成：领域 schema version 为 `2`，运行描述 revision 为 `2.2`，JSON 配置 schema 为 `2`。
默认执行链已改为 ProposalValidator prepare/recheck，不再包含语义审批、任务权限或确认分支。
TaskRepository 是运行态所有者；记录默认关闭，诊断工具只在 audit 与 diagnostic 同时启用时注册。
真实 Treeland/Deepin 验收尚未完成。

项目尚未商用，允许破坏性变更：旧接口、字段、别名和兼容分支直接删除，同步修改仓库内调用方、
配置、测试和文档，不设置弃用期。历史实现由 Git 保存；已有审计文件不删除，新运行时不承担旧格式读取。

按 S0 → S1 → S2 → S3 → S4 → S5 执行。每阶段完成相关代码、调用方和回归后更新状态与证据。
不以文件数或行数下降代替功能验收，不为了拆文件新增透传层。

| 阶段 | 交付物 | 状态 |
| --- | --- | --- |
| S0 | 功能基线与真实任务记录 | 已完成自动基线；真实桌面环境阻塞 |
| S1 | 统一当前协议，删除旧兼容入口 | 已实施；自动回归通过，真实模型/桌面未测 |
| S2 | 轻量校验执行链，删除审批框架 | 已实施；自动回归通过，真实模型/桌面未测 |
| S3 | 单一运行态、精简记录及诊断解耦 | 已实施；自动回归通过，真实模型/桌面未测 |
| S4 | 默认依赖、配置、文档与示例收敛 | 待实施 |
| S5 | 全量回归、真实桌面验收及版本交付 | 待实施 |

## 必须保留的能力

- Core 不依赖 Treeland、Deepin、Qwen、PyAutoGUI 或 MCP transport；平台与模型实现仍通过 ports/adapters 接入。
- 截图、窗口观察、坐标映射、应用启动、平台快捷键、鼠标、键盘、拖拽及已有证据 provider。
- Proposal 的非空有序动作序列、执行失败短路、部分执行回执；不能强制模型每轮只输出一个动作。
- 执行前的结构、能力、坐标和目标一致性检查；观察重检与输入注入仍在同一执行临界区。
- 同一 Proposal 的重复执行保护、有界步骤和重试、reset、Qwen session 与实际执行结果同步。
- 未调用 executor 不产生执行回执；delivered 不等于 completed；done 不产生桌面输入且不能替代完成断言。
- 未知、冲突、过期证据不通过断言；状态只有一个权威来源，错误使用同一 ReasonCode 体系。
- MCP HTTP transport、显式连接边界与部署动作限制；不保留通用策略插件、语义审批和确认流程。当前没有连接鉴权实现，v2.2 前不得声称已有鉴权。

## 变更方法与提交边界

1. S0 先列出“能力 → 当前入口/测试 → 目标责任”矩阵，包含正常路径和失败恢复；记录拟删除机制由何处接替，或为何无需接替。
2. S1 统一协议并清理兼容；S2 在此基础上替换执行链；S3 再去掉失去用途的状态与记录包装；S4 收敛安装和使用入口；S5 做完整验收。
3. 每个可独立验证的改动形成一个候选提交单元，更新生产代码及仓库内调用方后再进入下一单元。此处是实施拆分，不表示本次文档更新已执行代码修改或 Git 提交。
4. 删除前检索全部调用方；一个单元结束后运行受影响测试。测试应验证保留行为，不为已经删除的对象结构重建兼容测试。
5. 新旧执行链不长期并存；回退通过对应代码版本和配置完成。已有审计文件原样保留，不自动转换或删除。
6. 记录减少的核心概念、状态分支、重复存储与调用层，并与能力矩阵对照；不能仅以测试数量或删行数量验收。

## S0：建立功能基线

**范围**：现有 tests、`docs/manual-test-guide.md`、`docs/PROJECT_SCORECARD.md`、`src/mcp_autogui/regression.py`。

1. 运行现有自动测试，记录 commit、结果和已有失败，不沿用旧评分卡中的测试数量作为本次证据。
2. 建立固定桌面任务集：启动应用、输入中文、快捷键、拖拽、终端运行 htop 并关闭、含多个动作的提案。
3. 记录每项任务是否成功、是否误报完成、人工介入次数、模型调用数和耗时；重构后使用相同环境与目标复测。
4. 标出当前会因 unknown、content_edit、semantic_intents 阻塞的路径，作为 S2 的行为回归输入。
5. 盘点 TaskRepository、TransactionRecorder、AuditRecorder、ObjectStore、EventLedger 的读写关系；标出执行或 status 依赖审计对象的位置。
6. 核对旧 HTTP 后端与内嵌 Qwen 的能力差异；有独有核心能力时先迁入保留路径再删除后端，不能把实际能力误当兼容代码。
7. 将下列 v2.2 契约写成 S0 测试矩阵并由实现前评审确认：ProposalValidator 的参数/能力检查，空 assertions 的 delivered-unverified 终态，状态/预算映射，transport auth，以及三种 recording 模式。

**完成条件**：有可复核的自动测试基线、任务矩阵和上述五项契约的用例表。真实环境不可用时标记未测及原因，可继续 S1–S4，
但不能宣称真实验收完成。无需另建评分或 benchmark 框架。

**S0 记录（2026-09-24）**：commit `ce57a7d4f212cc1084a7e35598fba90803ac6292` 上执行完整测试，
`144 passed, 24 subtests passed`。已完成能力/测试映射、旧审批阻塞路径、运行态与审计关系、旧 HTTP 后端差异及固定任务矩阵。
当前为 TTY 会话且 `treeland-debug --json tree` 超时无输出，真实任务均标记环境阻塞；S1–S4 可继续，S5 前必须补测。

## S1：统一协议，直接删除兼容代码

**主要入口**：

- `core/transaction.py`、`core/protocol.py`、`facade.py`、`runtime_description.py`
- `qwen_backend.py`、`adapters/proposal/qwen_cua.py`、`adapters/providers.py`
- `server_config.py`、`core/audit.py`、`core/ledger.py`、`audit_cli.py`

**改动**：

1. Proposal 只保留 `actions` 序列；删除 `action` 首动作兼容视图和旧单动作输入归一化。
   同步修改 parser、provider、executor 调用、桌面事务入口及测试 fixture。
2. 删除旧 gui-mcp HTTP 兼容后端、二进制/JSON 回退及相关模式配置，保留内嵌 Qwen 与模型端点通信。
   MCP 的 HTTP transport 和模型的 HTTP API 仍是当前功能。
3. 删除旧操作别名、旧环境变量行为回退和历史审计格式转换。配置使用 JSON，密钥及桌面会话资源仍来自环境。
4. 清理仅用于兼容的测试和 fixture；将当前行为测试迁到唯一协议表示。
5. 领域 schema version 更新为 `2`，运行描述 revision 更新为 `2.2`，统一声明来源。
   JSON 配置 schema 独立版本化：发生字段删除或语义变化时同步升级配置版本与两份配置文件。
   不增加旧版本转换器；旧字段或格式给出明确输入错误。
6. 先实施并测试 transport auth 配置：默认 loopback；可信反向代理只接 loopback upstream；非 loopback 要求 bearer-token。
   将示例 `0.0.0.0` 改为 loopback，旧无鉴权暴露配置直接报错，不静默接受。

**验收**：单动作和多动作均用 actions 正常工作；多 tool-call 顺序不变；内嵌模型连接和反馈可用；
旧入口明确不受支持，无静默回退。验证 facade、Qwen、配置、入口、审计读取与工具注册相关测试。

**建议拆分**：actions 统一及调用方 → 旧后端/配置回退清理 → 历史格式分支删除及版本声明。版本先随协议修改更新；后续配置变更同步调整，S5 核对最终版本。

**S1 记录（2026-09-24）**：Proposal 统一为非空 `actions`；删除单动作字段、动作/operation alias、旧 gui-mcp HTTP 后端、行为环境回退和 schema 1 审计读取；
配置 schema、领域 schema 和运行 revision 已更新。MCP 默认 loopback，非 loopback 要求 bearer token，可信代理 upstream 仍要求 loopback。
仓库调用方、示例和测试已同步；新的审计目录为 `.autoui-audit-v2`，已有 `.autoui-audit` 保留且不读取、不转换。
自动回归为 `147 passed, 24 subtests passed`；真实 Qwen 端点与桌面任务留待 S5。

## S2：精简默认执行链

**主要入口**：

- `core/action_gate.py` → `core/proposal_validator.py`（目标文件）
- `core/orchestrator.py`、`core/task.py`、`core/task_state.py`、`core/context_builder.py`
- `core/task_repository.py`、`core/transaction_recorder.py`、`core/transaction.py`
- `ports/policy.py`、`adapters/policy/action_restriction.py`、`provider_registry.py`
- `facade.py`、`protocol_response.py`、`desktop_transactions.py`、`mcp_autogui_main.py`

**默认流程**：

```text
observe → propose(actions) → validate → recheck → execute → observe/evaluate → TaskState
```

**改动**：

1. 用轻量 ProposalValidator 替代 ActionGate，采用 `prepare(snapshot, proposal) → PreparedProposal | ValidationFailure(reason_code, retryable)`
   和 `recheck(prepared, latest_snapshot) → PreparedProposal | ValidationFailure(reason_code, retryable)` 契约，保留协议、参数、能力、坐标与目标检查。
   校验结果及每个动作需要的环境依赖只作为内部数据，不建立独立 Guard 持久化链。
   PreparedProposal 表示校验后的完整提案及动作依赖，不限制为单个原子动作；在执行锁内 recheck 后立即使用，不持久化。
2. 删除 TaskPermissions、任务级 policy_profile / policy_overrides、默认语义分类及其状态分支，
   清理 ContextBuilder、响应与 ReasonCode 中仅服务这些路径的内容。旧任务字段直接报错。
3. 默认路径不生成 PolicyDecision、SemanticTag、SemanticResolution，不因 unknown 或 content_edit 请求确认。
4. 删除 ports/policy.py、策略工厂注册及多 provider 合并；将已有 action_restriction 的有效限制语义迁到 JSON `deployment.denied_actions`。
   未设置或空列表表示不限制，未知或重复动作启动时报错；validator 直接校验整个序列，违反限制时零注入并返回明确原因。
   删除旧 `policy_providers` 字段并明确报错；describe 展示部署限制，任务不能覆盖。配置版本和示例同步更新。
5. 删除 needs-confirmation、confirm、确认记录及恢复分支，同步更新 facade、状态归约和客户端。
   不新增 trusted-local/restricted 两套模式或审批扩展框架。连接鉴权和传输访问控制按 S1 的新契约实现并持续验证。
6. 保留执行锁、重复执行保护和部分回执：序列首个失败即停止，结果不确定或部分送达不能自动重放整个序列。
   环境 stale 可在预算内重新观察提案；参数错误、能力缺失与执行失败分别给出明确原因。
7. 删除 Decision 后同步改造 Qwen pending proposal 的终结通知：未执行、已执行、部分执行都反馈真实结果，
   不伪造 Receipt，不让模型历史继续等待已结束提案。
8. 在 TaskStateReducer 集中实现文档定义的 validation/stale/execution/assertion 映射：只有 stale/retryable validation
   和 recoverable assertion failure 消耗 max_retries；prepare/recheck 失败不消耗 max_steps，接受并准备执行的 sequence 才消耗 max_steps。
9. 空 assertions 允许注册：完整送达后只在 provider 的 done 结束时归约为 delivered-unverified；done 不能替代断言成功，
   未送达、部分送达或不确定送达不得进入该终态。所有 public response、Qwen feedback、快捷键和应用启动入口同步处理。

**验收**：普通点击、输入、快捷键无语义确认；无效坐标、焦点变化及目标消失时零注入；
done 顺序、失败短路、重复调用、步骤/重试预算和 Qwen 反馈均有行为回归。
显式动作限制有效，默认配置无需策略；证据不充分时不得 completed；五类失败的状态、预算与零注入语义符合契约；空断言工具结果不误报 completed。

**建议拆分**：validator 替换 Gate 并更新执行入口 → 删除任务权限/确认及静态限制迁移 → 清理模型反馈与所有调用方。内部校验值只保存实际动作依赖，不要求复刻 Guard 的字段与对象结构。诊断 execute 必须调用同一执行入口并重新校验，不能复用过期的 validate 结果。

**S2 记录（2026-09-24）**：ActionGate 已由不持久化的 ProposalValidator 取代；TaskPermissions、
PolicyProvider、PolicyDecision、Guard、语义分类、confirm 和 needs-confirmation 已从生产路径删除。
`deployment.denied_actions` 取代旧策略配置；旧任务和配置字段明确报错。状态归约覆盖 validation、
stale、execution、assertion 及空断言 `delivered-unverified`，Qwen 对未执行提案接收真实终结反馈。
自动回归为 `116 tests passed`；真实 Qwen 端点、HTTP 鉴权握手和 Treeland 桌面任务仍待 S5。

## S3：收敛运行态、记录与按需诊断

**主要入口**：`core/store.py`、`core/ledger.py`、`core/audit.py`、`core/audit_recorder.py`、
`core/transaction_recorder.py`、`core/task_repository.py`、`core/task_state.py`、`core/orchestrator.py`、
`core/audit_models.py`、`audit_cli.py`、`facade.py`。

1. 以 TaskRepository 收敛为唯一运行态所有者：删除 decision/guard 及其引用索引，保留任务、提案、观察、回执、结果、去重与 provider 终结标记；逐项验证索引必要性，合并可由同一记录取得的重复字段。
   TaskStateReducer 保留纯状态归约职责，状态只由一个入口写回；执行、去重、status 不从审计存储反查运行态。
2. 启用审计时只保存必要任务信息、提案、实际回执、最终断言和关联事件；保留部分执行与失败原因。
3. 截图、原始模型输出、详细树及完整证据材料仅在诊断启用时落盘。关闭诊断后，不返回无法解析的持久化引用。
4. 删除 TransactionRecorder 中仅承担“双写/透传”的职责，由编排更新运行态，再通过一个可选记录入口输出必要事件。
   合并重复记录和序列化路径；常规错误只记录原因码、阶段、关联提案和必要明细，完整归因对象按诊断需要生成。若 recorder 删除后仍有独立职责，迁入相应所有者，不搬成同名包装。
5. 仅支持当前持久化格式。对象引用保持可用，敏感配置仍脱敏，不删除用户已有审计文件。
6. 将现有 audit 目录配置替换为 `recording.audit`、`recording.diagnostic`、directory、retention_days、max_gib；默认均关闭，diagnostic 必须依赖 audit。
   根据这两个开关注册或不注册 gui_diagnostic，并分别实现“关闭记录”“仅审计”“审计加诊断”的持久化范围和引用生命周期。运行态对象的内存寻址不要求写磁盘。
   ObjectStore/EventLedger 只保留当前记录与查询需要，不另外建设事件溯源或持久化任务恢复框架。
7. 明确 reset 清理任务、去重和模型会话的范围；进程重启不自动重放未决动作。记录失败不能触发已经送达动作的再次执行，应独立报告诊断不可用。

**验收**：关闭审计仍能完成任务；开启审计能定位失败动作和断言；诊断关闭不落盘大对象或逐动作 Guard；
诊断开启可 trace 当前记录，重复执行保护不依赖磁盘存储；关闭记录不返回 trace 引用，仅审计不注册诊断工具，reset 和进程退出后内存引用均失效。

**建议拆分**：运行态读取脱离审计 → 删除重复索引和事务透传层 → 可选持久化与精简归因 → reset/记录失败/模型反馈回归。检查关闭记录、仅审计、开启诊断三种配置的行为与引用有效性。

**S3 记录（2026-09-24）**：Proposal、snapshot、receipt、assertion 和去重均由 TaskRepository
持有，执行与 status 不再从审计对象反查；TaskState 统一通过编排器的单一 transition 入口写回。
删除 TransactionRecorder，模型反馈与回执记录回到编排器。配置迁移为 `recording.audit` /
`recording.diagnostic`，两者默认关闭，diagnostic 必须依赖 audit。仅审计模式只持久化任务、
Proposal、实际回执、断言、状态转换及精简失败归因；完整诊断才保存截图、模型原文、桌面树、
证据和 trace 对象。reset 清理任务运行态、去重、模型会话及内存引用，但保留审计历史；持久化失败
不会改变真实执行回执或触发重放。自动回归为 `125 tests passed`；真实记录目录、Qwen 与 Treeland
桌面验收仍待 S5。

## S4：依赖、配置与使用入口收敛

**范围**：`pyproject.toml`、`config/`、`client_env.sh`、`langchain_settings/`、
中英文 README、`src/mcp_autogui/langchain/`、部署脚本及手工验收文档。

1. 核对 import 后，把 LangChain/Google 等示例客户端依赖移到 optional extras，删除无实际调用的依赖。
   核心服务在未安装 extras 时能启动；示例能力在安装 extras 后可用。
2. 中英文 README 使用同一 JSON 配置、当前任务格式和 MCP 工具边界，删除权限覆盖及旧环境配置示例。
3. 同步部署、smoke、客户端和手工指南，确保正常任务只需使用 gui_run 生命周期接口。
4. gui_run 只保留 describe / run / status / reset；describe 显示 transport auth 模式、静态动作限制和 recording 模式，不泄漏秘密。
   gui_diagnostic 使用 describe / observe / propose / prepare / execute / evaluate / trace；
   删除 decide、confirm 和旧别名，诊断执行仍经过同一校验与执行链。
5. 更新评分卡与验收要求，移除“unknown 默认确认”等旧目标；优先记录实际成功率、误报完成、
   人工介入和成本，避免以新增抽象层数评价架构。

**验收**：最小安装可启动服务；示例可运行；代码、配置、describe、README 和部署脚本一致；
无需要用户填写 permissions 或 policy_overrides 才能执行普通任务的示例。

## S5：回归与交付

1. 运行全量自动测试，覆盖 Core 边界、协议、provider、桌面事务、证据验证、Qwen、审计与配置。
2. 运行 smoke，按更新后的手工指南完成 S0 任务集；对照记录成功率、人工介入、模型调用数和耗时。
3. 验证异常场景：目标变化、越界、无效参数、不支持的动作、部分执行、重复调用、过早 done、空 assertions、证据不足、provider 不可用，以及 retry/step 预算耗尽。
4. 检查删除项：生产代码与当前示例没有旧兼容后端、动作首项别名、任务权限字段、通用策略框架或确认分支。
   不因搜索到历史说明而恢复兼容代码。
5. 在发布前明确包 SemVer 是否与架构版本绑定：若绑定，包版本为 `2.2.0`；若不绑定，选择符合现有发布序列的版本并在 README 与 changelog 说明。
   领域 schema `2`、运行 revision `2.2` 与 JSON 配置版本仍必须一致；文档记录必要的破坏性变化，不提供旧协议迁移层。

**完成条件**：自动回归通过、真实任务有记录、核心功能无退化、普通操作无逐步权限审查、
显式动作限制与诊断仍可用、文档和配置可直接使用；S0 能力矩阵逐项对照，无第二套执行链和状态权威来源。
任何未测项明确列出，不标记为通过。

## 验证命令与进度记录

按改动范围运行相关测试，每阶段结束更新此文档的状态；S5 再运行全量测试。
纯文档修改只检查差异和链接，不冒充运行时验证。

```bash
uv run --with pytest pytest -q
uv run autoui-smoke --config config/mcp-autoui.json
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

每阶段记录：完成 commit、主要删除/变更、验证命令与结果、真实环境未测项。
环境缺失与实现失败分别记录；旧 P0–P20 的实施过程通过 Git 历史查阅，不继续混入 v2.2 待办。
