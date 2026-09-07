# AutoUI MCP v2 项目评分卡

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
| 1 | 架构边界清晰度 | 10 | Compositor、Proposal、Policy、Executor、Evidence、Evaluator、Reducer 各只回答一个问题；没有跨层决策或 God Object。 |
| 2 | 核心独立性 | 10 | `core/` 只依赖协议与 ports，不依赖特定 compositor、桌面、模型或输入实现。建议每次执行 `rg -n -i 'treeland\|deepin\|qwen\|pyautogui\|dde-am' src/mcp_autogui/core`。 |
| 3 | Canonical Model 质量 | 8 | 字段最小、稳定、语义明确、支持 unknown，能被多种 compositor 映射；不是原生树的改名副本。 |
| 4 | 扩展性 | 8 | 新 compositor、provider、模型或启动方式主要新增 adapter 与 fixture；Core 改动趋近零。记录 Extension Core Touch Ratio。 |
| 5 | 状态与事实正确性 | 10 | `claim`、`evidence`、`assertion_result`、`execution_receipt`、`task_state` 严格分离；unknown、冲突和过期不会被伪造为结论。 |
| 6 | 安全与策略模型 | 10 | 机械权限、任务授权和语义风险分层；unknown 默认确认；模型不能扩大权限；平台动作不旁路事务。统计 false allow、false reject 与 safe refusal。 |
| 7 | 可验证性与可测试性 | 10 | adapter fixture、port 契约、gate/evaluator/reducer 纯函数测试和稳定错误码测试齐全；真实环境回归补足单测盲区。 |
| 8 | 故障归因能力 | 8 | 失败能给出 stage、owner、code、evidence 状态和因果链。统计 Root Cause Resolution Rate。 |
| 9 | Agent 鲁棒性 | 8 | 对 stale snapshot、遮挡、目标变化、重复动作、过早 DONE、provider 不可用、无进展有安全恢复或停止。统计恢复成功率。 |
| 10 | 通信与上下文效率 | 6 | Context 是受控投影而非完整 history；大型树、截图和 Ledger 以引用保存。统计 tokens、截图数和 MCP payload。 |
| 11 | 工程实现质量 | 7 | 依赖方向、接口稳定性、复杂度、类型/错误处理、可读性与测试维护成本处于可控范围。 |
| 12 | 实际任务效果 | 5 | 用真实任务矩阵衡量成功率、false completion、unsafe action、步骤数、延迟和成本；没有实测不能给高分。 |

## 固定指标与复评输入

每次 release 至少记录：

- 单元、fixture、契约和真实桌面测试的数量与结果；错误码覆盖和关键状态转换覆盖。
- 任务成功率、False Completion Rate、Unsafe Action Rate、平均步骤、延迟、token/截图/MCP
  payload 成本。
- Root Cause Resolution Rate、Repeated Action Detection、No Progress Detection 和 Recovery
  Success Rate。
- 至少一个新增 adapter/provider 的 Extension Core Touch Ratio：
  `新增功能所改 core LOC / 新增功能总 LOC`。
- 所有未完成或无法在当前环境测量的项目，明确标记为“未测量”，不得以零失败代替。

## 当前基线评分

**评估对象：** 当前 v2 基线（2026-09-07）
**证据：** 84 项 Python 单测通过；v2 实现与手工验收文档；当前代码静态依赖检查。
**置信度：** 架构/单测维度中等至高；真实任务效果低。尚未完成真实 Treeland、AT-SPI、
OmniParser、审计目录与第二 compositor 验收。

| # | 维度 | 原始分 | 贡献分 | 依据与限制 |
| --- | --- | ---: | ---: | --- |
| 1 | 架构边界清晰度 | 9.0 | 9.0 | Ports、adapters、Core transaction 与 evidence/evaluator/reducer 分层明确；`CoreOrchestrator` 仍较大，需持续防止编排逻辑膨胀。 |
| 2 | 核心独立性 | 10.0 | 10.0 | `core/` 与 `ports/` 不含具体 adapter、桌面、模型或输入实现标签；模型归因使用通用 `model` owner。 |
| 3 | Canonical Model 质量 | 8.0 | 6.4 | 有最小窗口、坐标、capability 与 unknown 语义；尚无第二真实 compositor 来验证字段充分性与最小性。 |
| 4 | 扩展性 | 8.0 | 6.4 | Backend registry、port 和 provider 机制已具备；尚未用第二真实 backend 或新增 provider 的改动比证明。 |
| 5 | 状态与事实正确性 | 9.0 | 9.0 | 事务、Evidence、AssertionResult、Receipt 和 Reducer 已分离并有单测；真实 provider 冲突/过期场景仍待验收。 |
| 6 | 安全与策略模型 | 8.5 | 8.5 | 权限、语义策略、确认与 Guard 存在且平台工具走 Proposal；尚无真实误允许/误拒绝统计。 |
| 7 | 可验证性与可测试性 | 7.0 | 7.0 | 84 项单测与 canonical fixture 覆盖关键组件；未提供覆盖率、完整错误码矩阵、第二 compositor 和真实桌面回归。 |
| 8 | 故障归因能力 | 8.0 | 6.4 | Ledger、stage/owner/code、证据状态与恢复动作已实现；尚未计算 Root Cause Resolution Rate。 |
| 9 | Agent 鲁棒性 | 7.0 | 5.6 | stale/Guard/无进展等机制存在；真实窗口变化、provider 抖动和模型误识别下的恢复率未知。 |
| 10 | 通信与上下文效率 | 8.0 | 4.8 | Context Builder 使用投影，原始树/截图/模型输出以 artifact 引用保存；尚无 tokens、payload、延迟实测。 |
| 11 | 工程实现质量 | 7.0 | 4.9 | 依赖方向良好、工作区干净、回归通过；`core/orchestrator.py` 与 MCP composition root 仍偏大，应在后续重构中控制复杂度。 |
| 12 | 实际任务效果 | 2.0 | 1.0 | 尚未执行真实 Treeland 任务矩阵；不能由单测推断成功率、成本、速度或 false completion。 |

**总分：79.0 / 100（B）**

结论：当前是“**架构质量显著高于实证成熟度**”的状态。没有触发两个否决项：Core 不含
具体运行时依赖，且状态/事实分离路径存在并受测试保护。但尚未取得真实桌面矩阵和质量指标，
因此不能按 A 级或更高评级。完成 `manual-test-guide.md` 的 Treeland 回归、provider 质量
验证和审计验收后，应以真实数据重新评分，尤其复核第 7、8、9、12 维度。
