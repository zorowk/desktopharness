# AutoUI MCP v2 实现与扩展指南

本文记录 `treeland-autoui-mcp-v2-design.md` 对应的已实现基线。

## 已实现边界

- `core/` 包含 canonical 协议对象、Action Gate、ProposalGuard、语义策略、Assertion Evaluator、确定性 Task State Reducer、append-only Ledger、Context Builder 和薄 Orchestrator；可选审计模式使用私有 JSON 对象目录、原始二进制 artifact 目录和 `ledger.csv` 持久化协议对象与事件。
- `ports/` 定义 compositor、frame、proposal、policy、executor、application launcher、platform capability 和 evidence 契约。
- `adapters/` 包含 Treeland 与严格 canonical-JSON compositor adapter、Qwen-CUA proposal adapter、PyAutoGUI frame/input adapter、Deepin capability adapter、`dde-am` launcher 和 compositor-window evidence provider。
- `facade.py` 实现紧凑的 `gui_run` 操作和诊断对象查询。
- `gui_run(operation="run")` 执行有界的自动闭环；每轮仍是一个独立的
  `observe -> propose -> decide -> execute -> evaluate` 事务，遇到确认、拒绝、
  重复无进展或任务终态即停止并返回恢复建议；证据不足时切换到
  `verification-focused` 投影，仍受任务步数预算限制。

Core 不包含 Treeland、Deepin、Qwen、PyAutoGUI 或 `dde-am` 的 import 和专用分支。合成器原始树与截图保存在对象存储中；正常协议对象只携带引用。

## 尚未完成的实现与验收

v2 事务内核和默认 Qwen + Treeland 路径已经实现；以下事项不应被表述为
“v2 已完成”：

1. **独立业务证据**：当前 compositor-window provider 只能验证窗口级事实。OmniParser
   已迁移为默认关闭、只读的 `omniparser-grounding` provider：它只提供注册的
   `control.*`/`document.text` 概率性 EvidenceRecord，并将原始响应保存在 artifact
   引用中；不会注册 `omniparser_*` 直连执行工具。`document.text` 可直接用于断言；
   `control.*` 目前要求 task contract 已带有同帧的临时 `omniparser_element_id`，因此
   不能作为通用的规划或策略 grounding。仍需接入并验证 AT-SPI、OCR、DOM
   或应用 API 等具有适当独立性和可靠性的 provider，才能可靠判定控件状态、文本和
   业务结果。
2. **跨合成器实证**：CanonicalJsonAdapter 已覆盖协议夹具；仍需至少一个非 Treeland
   合成器的真实 adapter 与同等契约/桌面测试，才能证明通用性。
3. **持久审计的真实环境验收**：设置 `GUI_AUDIT_DIR` 后，运行时将小型、结构化协议对象
   以及截图、原始树和模型输出等 artifact 写入私有 JSON 文件，并把 Ledger 追加到
   `ledger.csv`；二进制 artifact 以原始 `.bin` 文件保存于独立 `artifacts/` 目录，JSON
   仅保存相对路径、长度和 SHA-256。`reset` 只清运行态并追加 `task.reset`，不会删除
   既有 Ledger。保留期和容量清理以一个 JSON 对象及其全部 artifact 为原子单元；无法
   JSON 化的值保存说明性 stub，而不会无记录消失。目录拒绝 group/other 可访问权限，默认
   保留 7 天、总计 16 GiB（以 `GUI_AUDIT_MAX_GIB` 调整）。仍需按部署路径验证权限、容量和
   保留策略；该审计副本用于重启后复核，不能恢复执行中的任务。

启用审计后，可用与合成器无关的 `autoui-audit` 浏览存档。直接传目录会进入终端 TUI：

```text
autoui-audit /private/path/audit
```

TUI 支持任务列表 → 事件时间线 → 对象详情的逐层浏览；`↑↓` 选择、`Enter` 进入、`b`
返回、`q` 退出。顶栏显示任务、事件和完整归档容量（CSV、JSON 和 artifact）；对象详情页可展开 JSON，二进制 artifact
可按 `e` 后输入的路径导出，并拒绝覆盖已有文件。启动时会显示只读归档扫描和因果链索引
动画；任意按键可跳过。任务列表页按 `z` 可将 `ledger.csv` 与所有对象打包为 `.tar.gz`；
将该文件复制到其他机器后，直接执行 `autoui-audit copied-audit.tar.gz` 即可在临时只读目录
中打开同一份审计记录。压缩包含 `manifest.json`，打开前会校验每个成员的大小和 SHA-256，
可发现缺件或意外损坏；它不是签名或防篡改证据，存在对抗性威胁时必须在部署层增加签名或
受控导出流程。
4. **集中真实 Treeland 回归验收**：在上述实现完成后，按
   `manual-test-guide.md` 的基础事务、桌面适配器与 5×10 重复矩阵执行，产出可复核的
   成功率、拒绝率、延迟和 attribution 报告。当前单元测试不能替代此项。

实施顺序是先完成 OmniParser 迁移、独立 evidence、第二合成器和所需持久化，再进行
集中真实 Treeland 验收。每项实现完成后仍必须运行相应单元和契约测试；集中验收用于
验证这些能力在真实桌面中的联合作用。

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
- Context Builder 的 `compact`、`visual-heavy`、`recovery`、`verification-focused`
  和 `planning-reset` 是不同预算与事件投影，不是同一 history 的别名。恢复投影
  会附带最近 primary attribution；模型历史不会被当作 verified fact。
- 对外信封返回 attribution 引用和稳定恢复动作；环境变化、安全拒绝和证据不足
  与组件执行错误分开记录。

## 新增合成器

在 `adapters/compositor/` 中实现 `ports.compositor.CompositorAdapter`，并返回 `CanonicalSnapshot`。只映射 `CanonicalWindowFact` 定义的字段；其余原生字段丢弃，或将完整原始 payload 保存到 `raw_artifact_ref`。Adapter 必须声明真实 stacking model；无法提供 hit test、identity、visibility 或 cursor 时不能伪装为否定事实。

已经输出 canonical JSON 的 compositor bridge 可以直接使用 `CanonicalJsonAdapter`。它会有意忽略未知输入字段。

## 新增 Evidence 或执行后端

Evidence Provider 声明 `core.facts.STANDARD_FACT_PATHS` 的子集，并只返回 `EvidenceRecord`，不能判断任务完成。只有当新 fact 具有稳定语义且有核心消费者时，才扩展注册表。

Input/Application backend 只返回 `ExecutionReceipt`。窗口变化或业务结果不能写入回执，必须由独立 Evidence Provider 采集。

## 工具面

旧 `qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset`、`qwen_cua_status`
兼容工具已删除。唯一对外工具是 `gui_run`（全部协议操作）以及
`desktop_*` 工具；`desktop_shortcut_invoke` 与 `desktop_application_launch`
内部同样走 canonical Proposal -> PolicyDecision -> Guard -> ExecutionReceipt。
Qwen 后端只实现 `ProposalProvider`：每轮必须产出一个 canonical action，原始模型
输出仅以 `debug_ref` 保存。需要自动执行时由 `gui_run(operation="run")` 驱动；需要
人工审批或诊断时使用 `observe`、`propose`、`decide`、`execute`、`evaluate`、`trace`
等显式操作。
