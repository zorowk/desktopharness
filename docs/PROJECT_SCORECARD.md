# AutoUI MCP v2.2 项目评分卡

本文件定义 AutoUI MCP 的固定评估标准。它用于每次重要 release、架构调整和真实桌面
回归完成后复评；分数是工程决策辅助，不替代安全验收或任务证据。

## 评分规则

- 总分为 100。每个维度先给出 0–10 的原始分，再按权重折算：`贡献分 = 权重 × 原始分 / 10`。
- 只能根据可复核的代码、fixture、契约测试、真实运行记录和 benchmark 评分；没有证据的
  能力不得按“已验证”计分。
- 分别记录**架构质量**和**真实任务效果**。前者不能抵消后者缺失的实测数据。
- 每次评分必须记录 commit、证据、未覆盖场景和置信度；不得以主观印象替代运行结果。

| 总分 | 等级 | 含义 |
| --- | --- | --- |
| 95–100 | S | 架构和实证均成熟，可作为通用框架。 |
| 90–94 | A+ | 非常优秀，仅有局部边界或实证缺口。 |
| 85–89 | A | 架构可靠，具备明确扩展价值。 |
| 80–84 | B+ | 总体良好，但仍有明显技术债或验证缺口。 |
| 70–79 | B | 可以继续使用，但应优先补齐抽象或验证体系。 |
| 60–69 | C | 工程可用，结构容易继续腐化。 |
| <60 | D | 应优先重构，不应继续堆叠功能。 |

## 否决项

任一否决项成立时，最高评级为 **B**，无论总分为何。

1. **事实污染**：存在 `model claim → verified fact` 的直接转换，或把
   `ExecutionReceipt.delivered` 当作任务成功/`completed`。
2. **核心平台绑定**：`core/` 出现具体 compositor、桌面、模型或输入实现的 import、命令或
   行为分支，例如 Treeland、Deepin、Qwen、PyAutoGUI 或 `dde-am`。

## 评分维度

| # | 维度 | 权重 | 核心问题与满分标准 |
| --- | --- | ---: | --- |
| 1 | 架构边界清晰度 | 10 | Compositor、ProposalValidator、Executor、Evidence、Evaluator、Reducer 各只回答一个问题；没有跨层决策或 God Object。 |
| 2 | 核心独立性 | 10 | `core/` 只依赖协议与 ports，不依赖特定 compositor、桌面、模型或输入实现。建议每次执行 `rg -n -i 'treeland\|deepin\|qwen\|pyautogui\|dde-am' src/mcp_autogui/core`。 |
| 3 | Canonical Model 质量 | 8 | 字段最小、稳定、语义明确、支持 unknown，能被多种 compositor 映射；不是原生树的改名副本。 |
| 4 | 扩展性 | 8 | 新 compositor、provider、模型或启动方式主要新增 adapter 与 fixture；Core 改动趋近零。记录 Extension Core Touch Ratio。 |
| 5 | 状态与事实正确性 | 10 | `claim`、`evidence`、`assertion_result`、`execution_receipt`、`task_state` 严格分离；unknown、冲突和过期不会被伪造为结论。 |
| 6 | 执行安全与部署约束 | 10 | 注入前参数、能力、坐标、目标和 stale 重检始终存在；模型不能绕过事务；部署可显式限制 canonical action。统计误完成、错误拒绝与安全停止，不以语义审批层数计分。 |
| 7 | 可验证性与可测试性 | 10 | adapter fixture、port 契约、gate/evaluator/reducer 纯函数测试和稳定错误码测试齐全；真实环境回归补足单测盲区。 |
| 8 | 故障归因能力 | 8 | 失败能给出 stage、owner、code、evidence 状态和因果链。统计 Root Cause Resolution Rate。 |
| 9 | Agent 鲁棒性 | 8 | 对 stale snapshot、遮挡、目标变化、重复动作、过早 DONE、provider 不可用、无进展有安全恢复或停止。统计恢复成功率。 |
| 10 | 通信与上下文效率 | 6 | Context 是受控投影而非完整 history；大型树、截图和 Ledger 以引用保存。统计 tokens、截图数和 MCP payload。 |
| 11 | 工程实现质量 | 7 | 依赖方向、接口稳定性、复杂度、类型/错误处理、可读性与测试维护成本处于可控范围。 |
| 12 | 实际任务效果 | 5 | 用真实任务矩阵衡量成功率、false completion、unsafe action、步骤数、延迟和成本；没有实测不能给高分。 |

## 固定指标与复评输入

每次 release 至少记录：

- 单元、fixture、契约和真实桌面测试的数量与结果；错误码覆盖和关键状态转换覆盖。
- 任务成功率、False Completion Rate、人工介入率、平均步骤、延迟、模型调用、token/截图/MCP
  payload 成本。
- Root Cause Resolution Rate、Repeated Action Detection、No Progress Detection 和 Recovery
  Success Rate。
- 至少一个新增 adapter/provider 的 Extension Core Touch Ratio：
  `新增功能所改 core LOC / 新增功能总 LOC`。
- 所有未完成或无法在当前环境测量的项目，明确标记为“未测量”，不得以零失败代替。

## 当前 S0 证据

v2.2 重构前 S0 基线对应 commit `ce57a7d4f212cc1084a7e35598fba90803ac6292`，
2026-09-24，完整自动测试为
`144 passed, 24 subtests passed`。当前会话是 TTY，`treeland-debug --json tree` 无输出并超时，
因此真实桌面成功率、误报完成、人工介入、模型调用数和耗时仍未测量。

运行时代码已进入 v2.2 重构；S0 只建立基线，没有用新增测试数量重新计算架构分数。
以下 83.7 分保留为历史评分，不能当作 v2.2 实施完成后的评分。

## 上次完整评分（历史）

### 评估快照

| 项目 | 当前证据 |
| --- | --- |
| 评估对象 | v2.1，commit `faea848`，2026-09-15（历史快照） |
| 自动化验证 | `uv run --with pytest pytest -q`：114 passed，24 subtests passed |
| 静态边界 | `tests/test_core_boundaries.py` 覆盖 Core/adapters、desktop transaction、Facade 分派与运行描述边界 |
| 真实环境 | P5 未完成；没有可复核的真实 Treeland 任务报告 |
| 置信度 | 架构、领域事实和单测评分高；真实任务、性能和恢复指标低 |

### 否决项检查

| 否决项 | 结果 | 证据 |
| --- | --- | --- |
| 事实污染 | 未触发 | 测试覆盖“拒绝或 stale 不产生 Receipt”以及“delivered 不直接完成任务” |
| 核心平台绑定 | 未触发 | `core/` 未发现 Treeland、Deepin、Qwen、PyAutoGUI 或 `dde-am`；AST 边界测试禁止 Core 导入 adapters |

### 分项得分

| # | 维度 | 原始分 | 贡献分 | 依据与限制 |
| --- | --- | ---: | ---: | --- |
| 1 | 架构边界清晰度 | 9.5 | 9.5 | Proposal、Decision、Receipt、Evidence、Assertion、TaskState 各有权威对象；Repository、Recorder、desktop transaction 和 Facade 职责已经分开。`CoreOrchestrator` 仍有 802 行，但主要承担顺序编排，不因行数单独扣成 God Object。 |
| 2 | 核心独立性 | 10.0 | 10.0 | Core 只依赖领域模块和 ports；具体 compositor、模型、输入与桌面工具均位于 adapters/backend。否决项扫描和架构测试同时提供证据。 |
| 3 | Canonical Model 质量 | 8.0 | 6.4 | 窗口、坐标空间、capability、frame 和 unknown 语义明确，原始树只保留引用；仍缺第二个真实 compositor 证明模型既充分又最小。 |
| 4 | 扩展性 | 8.5 | 6.8 | CompositorPort、DesktopBackend、provider registry 与 RuntimeDescription 使扩展位置清晰；尚未记录真实扩展的 Extension Core Touch Ratio。 |
| 5 | 状态与事实正确性 | 9.5 | 9.5 | 未执行即无 Receipt、delivered 不等于 completed、冲突和 unknown 不通过，均有自动化测试；TaskState 是公开状态权威。 |
| 6 | 安全与策略模型 | 8.5 | 8.5 | 机械权限、语义策略、确认、Guard 重检和平台事务入口已建立；缺少真实 false allow、false reject 与 safe refusal 数据。 |
| 7 | 可验证性与可测试性 | 8.5 | 8.5 | 114 项测试和 24 个 subtests 覆盖 Core、Facade、配置、provider、审计、桌面能力和 Qwen 解析；没有覆盖率报告、完整错误码矩阵或真实桌面回归。 |
| 8 | 故障归因能力 | 8.0 | 6.4 | ReasonCode、Ledger、Attribution、stage/owner 与恢复建议边界清楚；Root Cause Resolution Rate 未测量。 |
| 9 | Agent 鲁棒性 | 7.5 | 6.0 | stale target、冲突证据、provider 不可用、重复确认、预算和 no-progress 路径有实现或测试；真实模型误识别和窗口抖动恢复率未知。 |
| 10 | 通信与上下文效率 | 8.5 | 5.1 | 默认响应使用 TaskState 和 object reference，诊断单独展开；ContextBuilder 有 compact/recovery 投影。token、截图、payload 和延迟尚未测量。 |
| 11 | 工程实现质量 | 8.5 | 6.0 | 命名、依赖方向、配置入口、不可变运行描述和文档入口清晰，全量测试快速稳定；缺少覆盖率/静态类型/格式化基线，默认依赖仍包含可选 LangChain/Google 依赖。 |
| 12 | 实际任务效果 | 2.0 | 1.0 | P5 未完成，没有任务成功率、False Completion Rate、Unsafe Action Rate、步骤数、延迟或成本记录，不能由单测推断。 |

### 汇总

| 评分面 | 得分 | 解释 |
| --- | ---: | --- |
| 架构与工程（维度 1–11） | 82.7 / 95（87.1%） | 已达到 A 级工程结构，但仍缺第二平台和量化质量基线 |
| 真实任务效果（维度 12） | 1.0 / 5（20.0%） | 只有实现与测试替身证据，没有真实任务矩阵 |
| **综合评分** | **83.7 / 100（B+）** | 架构成熟度不能抵消真实验收缺失 |

### 未测量指标

- 真实任务成功率、False Completion Rate、Unsafe Action Rate、平均步骤、延迟和成本。
- Root Cause Resolution Rate、Repeated Action Detection、No Progress Detection 和 Recovery Success Rate。
- token、截图数、MCP payload，以及新增 backend/provider 的 Extension Core Touch Ratio。
- 第二 compositor、真实 AT-SPI、OmniParser 和持久化审计目录的端到端结果。

### 改进优先级

1. **完成 P5**：按 `manual-test-guide.md` 执行 V2-01～V2-10，保存任务结果、trace 和 artifact 引用。
2. **建立真实指标表**：至少记录成功率、false completion、unsafe action、步骤数、延迟和恢复结果。
3. **验证第二平台**：使用非 Treeland compositor 执行相同 port 契约和最小真实任务集。
4. **补工程基线**：增加覆盖率、静态类型或 lint 报告，并将可选 LangChain/Google 依赖移出默认安装集合。

结论：当前项目已经实现“**小核心、大扩展、克制通信**”的主要架构目标，模块名称和阅读入口也较清楚。
项目的主要风险不再是架构失控，而是缺少真实桌面、模型、证据 provider 和性能数据。因此当前评级为
**B+**；完成 P5 并得到稳定的安全与任务指标后，才有充分证据进入 A 或 A+。
