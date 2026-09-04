# AutoUI MCP v2 手工验收与回归计划

本文是当前真实桌面测试的唯一操作指南。协议、工具面与完成语义以
[v2 设计](treeland-autoui-mcp-v2-design.md) 和
[v2 实现与扩展指南](treeland-autoui-mcp-v2-implementation.md) 为准。

## 1. 范围与安全前提

只测试 v2 默认路径：`gui_run`、`desktop_capabilities_list`、
`desktop_shortcut_invoke`、`desktop_applications_list` 和
`desktop_application_launch`。已删除的 `qwen_cua_*` 工具和默认关闭的
`omniparser_*` 工具不属于本计划。

在可恢复、无敏感数据的独立桌面会话中测试。不得测试支付、授权、发送消息、
删除、覆盖保存、安装软件或终端命令。每轮前恢复相同的分辨率、窗口布局、
初始页面状态和鼠标位置。

启动服务后，先确认 MCP 连接可用，再调用：

```text
gui_run(operation="describe")
```

通过标准：返回 `protocol_version=2`，列出当前 compositor、provider、可用
actions 与 `run` 操作。若 capability 或 provider 缺失，记录为环境阻塞，不能
记为模型或执行器失败。

## 2. 每轮需要保存的证据

每个任务都使用稳定的 `task_id` 和不可变的 `task_contract`。默认响应只给出
`object_ref`；验收时使用 `diagnostic=true` 或
`gui_run(operation="trace", object_ref=...)` 保存所需对象和 artifact 引用。

至少记录：TaskContract、snapshot/frame 引用、proposal、PolicyDecision、
ExecutionReceipt、Evidence、AssertionResult、TaskState、attribution 与恢复建议。
`delivered` 仅说明输入已注入；只有 `completed` 才代表所有必要断言通过。

## 3. 基础事务用例

| ID | 场景 | 操作 | 通过标准 |
| --- | --- | --- | --- |
| V2-01 | 观察与协议发现 | `describe`、`observe` | 返回 canonical snapshot；Treeland 原始树只以 artifact 引用存在。 |
| V2-02 | 人工提案无副作用 | `observe`、`propose`、`decide`，不执行 | Proposal 只有一个 canonical action；未产生输入副作用。 |
| V2-03 | Qwen 单步提案 | `propose`（不传 proposal） | Qwen 输出被解析为单个 Proposal；原始输出仅出现在 `debug_ref`。 |
| V2-04 | 允许动作 | `decide`、`execute`、`evaluate` | 先有 PolicyDecision 和 Guard；回执与任务状态分离。 |
| V2-05 | 确认动作 | 提交无独立语义证据的输入/编辑提案，再执行 | 首次返回 `needs-confirmation`；只在 `confirmed=true` 后允许继续。 |
| V2-06 | 遮挡或目标变化 | 提案后遮挡、移动或关闭目标窗口，再执行 | Guard 拒绝且没有输入注入；返回稳定错误码及 `capture-new-frame` 等恢复建议。 |
| V2-07 | 证据不足 | 执行一个无法由 compositor 证明业务结果的动作并 evaluate | 不得 `completed`；状态为 `needs-evidence`/`partial`，归因不把 unknown 当失败或成功。 |
| V2-08 | 任务完成 | 使用 `active_window.app_id` 等可独立验证的 assertion | 所有 required assertions 通过后，且仅由 Reducer 给出 `completed`。 |
| V2-09 | 有界自动循环 | `gui_run(operation="run", max_iterations=...)` | 每轮遵循单动作事务；确认、拒绝、无进展、预算耗尽或终态时停止并返回原因。 |
| V2-10 | 诊断与重置 | `status`、`trace`、`reset` | trace 可追溯对象/因果关系；reset 后同一 task 可重新开始。 |

## 4. 桌面适配器用例

| ID | 场景 | 通过标准 |
| --- | --- | --- |
| D-01 | 快捷键能力目录 | 仅公开稳定 capability ID；高风险或不可用能力不自动执行。 |
| D-02 | 快捷键调用 | `desktop_shortcut_invoke` 形成 Proposal、Decision、Guard 与 Receipt；不接受任意按键。 |
| D-03 | 应用目录与启动 | `desktop_application_launch` 仅接受已发现的纯 app ID；启动后以 compositor evidence 验证活动窗口。 |
| D-04 | 桌面、Dock、普通窗口与弹窗 | role、坐标空间和命中结果正确；不把 desktop role 误判为“无可点击内容”。 |
| D-05 | 缩放、多输出和窗口平移 | 坐标转换、Guard 重检和目标命中正确；能力不足必须返回 unknown。 |

## 5. 重复回归矩阵

对下列任务在相同环境下各执行至少 10 次。每次失败都保留证据，不允许人工补做
后计作成功。

| 任务 | 主要能力 | 成功条件 |
| --- | --- | --- |
| 安全鼠标移动到计算器数字 7 | 视觉定位与坐标映射 | 落点在目标内，无点击。 |
| 打开已知应用 | application launcher 与窗口 evidence | 目标 app 成为活动窗口。 |
| 在空白文档输入固定文本后清空 | 焦点、键盘与 assertion | 文本准确、未保存、清理完成。 |
| 设置页只滚动 | 窗口选择与无副作用操作 | 内容移动，未修改任何设置。 |
| 提案后遮挡目标 | ProposalGuard | 每次拒绝，且零误注入。 |

报告每项的分子、分母、证据等级、平均步骤数、平均延迟、拒绝数、环境阻塞数和
按 attribution stage/owner/code 分层的失败数。确认、拒绝、未执行、缺少证据和
provider 不可用不能混入模型失败率。

## 6. 当前未完成验收

- 在真实 Treeland 环境完成上述回归矩阵并形成可复核报告。
- 接入并验证独立的 AT-SPI、OCR、DOM 或应用 API Evidence Provider，以覆盖窗口
  级事实以外的业务结果。
- 使用至少一种非 Treeland 合成器完成同一份 adapter 契约和真实环境测试。
- OmniParser 如需恢复使用，必须先改造成 v2 Evidence/Grounding Provider，并接受
  本文同样的事务、安全与归因验收；旧 `omniparser_*` 直连执行路径不计入 v2 成果。
