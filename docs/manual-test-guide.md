# AutoUI MCP v2 手工验收与回归计划

本文是当前真实桌面测试的唯一操作指南。协议、工具面与完成语义以
[v2 设计](treeland-autoui-mcp-v2-design.md) 和
[v2 实现与扩展指南](treeland-autoui-mcp-v2-implementation.md) 为准。

## 1. 范围与安全前提

默认模式测试 `gui_run`、`desktop_capabilities_list`、
`desktop_shortcut_invoke`、`desktop_applications_list` 和
`desktop_application_launch`；仅在 `recording.audit=true` 且
`recording.diagnostic=true` 时测试 `gui_diagnostic`。已删除的 `qwen_cua_*` 工具和
`omniparser_*` 直连工具不属于本计划，也不会被服务注册。

在可恢复、无敏感数据的独立桌面会话中测试。不得测试支付、授权、发送消息、
删除、覆盖保存、安装软件或终端命令。每轮前恢复相同的分辨率、窗口布局、
初始页面状态和鼠标位置。

启动服务后，先确认 MCP 连接可用，再调用：

```text
gui_run(operation="describe")
```

真实 Treeland 测试前还应在同一桌面会话执行 `treeland-debug --json tree`。它必须输出非空 JSON；
否则记录为环境阻塞，不进入任务成功率或模型失败率。

通过标准：返回 `protocol_version=2`、`schema_version=2`、`schema_revision=2.2`，列出当前 compositor、provider、可用
actions，以及公开的 `run`、`status`、`reset` 和诊断 operation。若 capability 或 provider 缺失，记录为环境阻塞，不能
记为模型或执行器失败。

## 2. 每轮需要保存的证据

每个任务都使用稳定的 `task_id` 和不可变的 `task_contract`。默认响应只给出
启用诊断后才记录 `object_ref`；验收时使用
`gui_diagnostic(operation="trace", object_ref=...)` 保存所需对象和 artifact 引用。

至少记录：TaskContract、snapshot/frame 引用、proposal、validation failure（若有）、
ExecutionReceipt、Evidence、AssertionResult、TaskState、attribution 与恢复建议。
`delivered` 仅说明输入已注入；只有 `completed` 才代表所有必要断言通过。

启用 JSON `recording.audit` 时，还须保存 `ledger.csv` 与最小 JSON 对象；仅启用
`recording.diagnostic` 时保存 `artifacts/`、截图、模型原文、桌面树和完整证据。审计副本用于
事后复核，不能恢复一个正在执行的任务。`reset` 必须追加审计事件而非删除历史。

## 3. 基础事务用例

| ID | 场景 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| V2-01 | 协议发现 | `gui_run(describe)` | 显示 transport auth mode、静态 `denied_actions` 与 recording mode，且不含密钥。完整诊断模式另以 `gui_diagnostic(observe)` 返回 canonical snapshot；Treeland 原始树只以 artifact 引用存在。 |
| V2-02 | 人工提案无副作用 | `gui_diagnostic(observe/propose/prepare)`，不执行 | Proposal 含一个或多个有序 canonical action；prepare 不持久化 PreparedProposal，且未产生输入副作用。 |
| V2-03 | Qwen Proposal | `gui_diagnostic(propose)`（不传 proposal） | 一次 Qwen 输出被解析为一个 Proposal，多个 `tool_call` 保序收纳；原始输出仅出现在 `debug_ref`。 |
| V2-04 | 有效动作 | `gui_diagnostic(prepare/execute/evaluate)` | prepare/recheck 通过后产生真实回执；回执与任务状态分离。 |
| V2-05 | 普通输入直通 | 提交无独立语义证据的输入/编辑提案 | 无确认分支；通过技术校验后直接执行。 |
| V2-06 | 遮挡或目标变化 | 提案后遮挡、移动或关闭目标窗口，再执行 | recheck 返回 retryable validation failure 且没有输入注入。 |
| V2-07 | 证据不足 | 执行一个无法由 compositor 证明业务结果的动作并 `gui_diagnostic(evaluate)` | 不得 `completed`；TaskState 保持 `running`，归因不把 unknown 当失败或成功。 |
| V2-08 | 任务完成 | 使用 `active_window.app_id` 等可独立验证的 assertion | 所有 required assertions 通过后，且仅由 Reducer 给出 `completed`。 |
| V2-09 | 有界自动循环 | `gui_run(operation="run", max_iterations=...)` | 每轮遵循 Proposal 事务；动作序列执行后只观察、评估一次；校验失败、无进展、预算耗尽或终态时停止并返回原因。 |
| V2-10 | 诊断与重置 | `gui_run(status/reset)`、`gui_diagnostic(trace)` | trace 可追溯对象/因果关系；reset 后同一 task 可重新开始。 |
| V2-11 | 持久审计重启复核 | 以相同 `recording.directory` 结束一次事务后重启服务 | CSV 事件和最小对象仍可读取；诊断开启时 artifact 也可读取；不恢复运行态。 |
| V2-12 | Reset 审计保留 | 对已有事件的 task 调用 `reset` | 既有事件保留，末尾追加 `task.reset`，不重写删除历史。 |
| V2-13 | 审计保留清理 | 构造含多个 artifact 的过期对象或超容量归档 | 被清理对象及其全部 artifact 同时移除；不留下断链或孤儿。 |
| V2-14 | 便携归档 | 在 TUI 中导出 `.tar.gz`，复制到另一台机器后打开 | `manifest.json` 校验所有成员；完整归档可浏览和导出 artifact；校验失败必须拒绝打开。 |
| V2-15 | 多动作 Proposal | 让 Qwen 在一次响应中返回安全的 `pointer.move` + `pointer.scroll` | 只产生一个 Proposal/Receipt；动作按序执行，结束后观察一次；trace 含逐动作回执。若第二步失败，后续动作不得执行。 |
| V2-16 | 状态与预算 | 分别触发不可重试校验、stale、执行失败、recoverable assertion 和 unknown evidence | 只有 stale/retryable validation 与 recoverable assertion 消耗 retries；只有准备执行的 sequence 消耗 steps。 |
| V2-17 | 可选动作限制 | 在 `deployment.denied_actions` 禁止 Proposal 使用的 action type | 默认配置不受影响；启用后 validator 拒绝整个序列且没有输入注入。 |
| V2-18 | 空断言结果 | 先完整送达动作，再由 provider 返回 done | 终态为 `delivered-unverified`；done 在未送达、部分送达或不确定送达后不得进入该终态。 |
| V2-19 | 三种记录模式 | 分别关闭记录、仅开 audit、同时开 audit/diagnostic | 任务均可执行；仅审计不注册诊断且不落大对象；完整诊断可 trace；diagnostic 单独开启时报错。 |

## 4. 桌面适配器用例

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| D-01 | 快捷键能力目录 | 仅公开稳定 capability ID；高风险或不可用能力不自动执行。 |
| D-02 | 快捷键调用 | `desktop_shortcut_invoke` 形成 Proposal、校验与 Receipt；不接受任意按键。 |
| D-03 | 应用目录与启动 | `desktop_application_launch` 仅接受已发现的纯 app ID；启动后以 compositor evidence 验证活动窗口。 |
| D-04 | 桌面、Dock、普通窗口与弹窗 | role、坐标空间和命中结果正确；不把 desktop role 误判为“无可点击内容”。 |
| D-05 | 缩放、多输出和窗口平移 | 坐标转换、依赖 recheck 和目标命中正确；能力不足必须明确失败。 |

## 5. 重复回归矩阵

对下列任务在相同环境下各执行至少 10 次。每次失败都保留证据，不允许人工补做
后计作成功。

### 坐标基准规则

凡是验收目标位于可移动窗口内，**不得**把某一轮得到的桌面绝对坐标复用于下一轮。
每轮必须先保存该轮的 canonical snapshot，按目标窗口在该 snapshot 中的 geometry
计算目标点或目标区域，并把计算结果写入该轮不可变的 `task_contract`。目标是区域时，
对 `cursor.position` 使用 `within_rect`，其 expected 值为 `{x, y, width, height}`；窗口发生平移、
缩放、切换输出或重新创建后，必须重新观察；不能沿用旧 contract。`window_relative_point()`
可用于回归驱动按窗口相对位置生成 desktop-logical 坐标。

| 任务 | 主要能力 | 成功条件 |
| --- | --- | --- |
| 安全鼠标移动到计算器数字 7 | 视觉定位与坐标映射 | 以该轮计算器 geometry 计算数字 7 的目标区域；落点在目标内，无点击。 |
| 打开已知应用 | application launcher 与窗口 evidence | 目标 app 成为活动窗口。 |
| 在空白文档输入固定文本后清空 | 焦点、键盘与 assertion | 文本准确、未保存、清理完成。 |
| 设置页只滚动 | 窗口选择与无副作用操作 | 内容移动，未修改任何设置。 |
| 提案后遮挡目标 | ProposalValidator recheck | 每次拒绝，且零误注入。 |

报告每项的分子、分母、证据等级、平均步骤数、平均延迟、拒绝数、环境阻塞数和
按 attribution stage/owner/code 分层的失败数。校验拒绝、未执行、缺少证据和
provider 不可用不能混入模型失败率。

## 6. 当前未完成验收

- 在真实 Treeland 环境完成上述回归矩阵并形成可复核报告。
- 在真实 Treeland 会话验证已接入的 AT-SPI 与 OmniParser Evidence Provider：记录其控件/
  文档 evidence 的准确性、歧义处理、延迟与归因。DOM、OCR 或应用 API 只在目标业务需要时
  另行接入。
- 使用至少一种非 Treeland 合成器完成同一份 adapter 契约和真实环境测试。
