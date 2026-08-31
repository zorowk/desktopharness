# Qwen-CUA + Treeland 手工测试指南

本指南用于评估当前框架的四层能力：

1. Qwen 是否能从截图中理解任务并给出合法动作；
2. Qwen 坐标是否能与 Treeland window tree 正确融合；
3. 执行前的目标窗口校验和坐标重投影是否有效；
4. 真实执行结果是否正确反馈给同一 Qwen session。

## 1. 当前能力边界

- `qwen_cua_predict` 会截图、获取 Tree、请求 Qwen，再将 Qwen 动作与 Tree 融合，但不执行。
- 当前 Qwen 主要看截图；完整 Treeland tree 尚未默认加入 Qwen prompt。Tree 主要由本地计算层用于坐标融合和执行前校验。
- `qwen_cua_execute` 会重新获取 Tree；目标窗口不一致时拒绝，同一窗口移动时按窗口内相对坐标重投影。
- 当前对“点击后控件是否真正达到预期状态”还没有通用自动验证，需要人工观察或下一轮 Qwen 观察。
- OmniParser 默认关闭，本轮只测试 Qwen-CUA + Treeland 主路径。

## 2. 测试前准备

### 2.1 安全桌面

使用可恢复的测试桌面，关闭包含密码、私聊、支付、生产数据的窗口。建议只打开：

- 计算器；
- 空白文本编辑器（不保存）；
- 系统设置的非破坏性页面；
- 一个不执行命令的空白终端，仅用于窗口命中测试。

禁止测试删除、付款、授权、发送消息、保存覆盖、安装软件和执行终端命令。

### 2.2 启动和连接

终端 A：

```bash
cd ~/Downloads/work/treeland-autoui-mcp
./client_env.sh
```

应看到：

```text
StreamableHTTP session manager started
Uvicorn running on http://0.0.0.0:8000
```

终端 B：

```bash
codex mcp get treeland_autogui_mcp
```

应看到 `enabled: true`、`transport: streamable_http` 和
`url: http://127.0.0.1:8000/mcp`。

## 3. 每次预测必查字段

调用 `qwen_cua_predict` 后，暂时不要执行，先记录：

| 字段 | 检查内容 |
| --- | --- |
| `session_id` | 本任务后续步骤必须复用 |
| `frame_id` | 每次新观察都应不同 |
| `step` | 同一 session 成功执行后递增 |
| `action_text` | 动作意图是否符合任务 |
| `fused_actions.actions[].action_index` | 传给 `qwen_cua_execute` 的索引 |
| `desktop_coordinate` | 转换后的桌面全局坐标 |
| `target_window` | `appId`/`title`/`geometry` 是否属于预期窗口 |
| `window_relative_coordinate` | 是否等于全局坐标减窗口原点 |
| `validation.inside_desktop` | 坐标动作应为 `true` |
| `validation.target_window_found` | 坐标动作应为 `true` |
| `window_candidates_front_to_back` | 有重叠窗口时，第一个应为真正接收点击的窗口 |

以下任一条不满足时不执行：

- 动作意图错误；
- 目标窗口错误；
- 坐标不在桌面内；
- 存在不应被点击的遮挡窗口；
- 动作包含输入、快捷键、拖拽或其他未授权操作。

## 4. 基础测试用例

### T01：连接和状态

让 Codex 执行：

```text
只调用 treeland_autogui_mcp.qwen_cua_status，不要调用其他工具。
```

通过标准：

- `backend.ok=true`；
- `backend.configured=true`；
- `backend.backend_mode=embedded`；
- 模型名与 `.env.local` 一致；
- 没有未处理预测时 `pending_sessions=[]`。

### T02：只预测，确认无副作用

打开计算器，让 Codex 执行：

```text
只调用 qwen_cua_predict，任务是“把鼠标移到计算器数字 7 按钮中心”。
返回完整融合结果，不要调用 qwen_cua_execute。
```

通过标准：

- 鼠标和界面没有发生变化；
- 返回 `session_id` 和 `frame_id`；
- 动作为鼠标移动或其他与指令相符的安全动作；
- `target_window` 是计算器；
- 坐标落在数字 7 的可见范围内。

如果继续执行 T04，保留这个 pending 提案；否则调用
`qwen_cua_reset(session_id)` 清理待执行提案。

### T03：待执行提案保护

1. 调用一次 `qwen_cua_predict`。
2. 不执行、不 reset，立即用相同 `session_id` 和相同指令再次预测。
3. 最后调用 `qwen_cua_reset`。

通过标准：第二次预测被拒绝，提示上一个 prediction 仍在 pending；reset 后从 `qwen_cua_status` 中消失。

### T04：低风险执行

复用 T02 中尚未 reset 的预测，或重新生成相同的安全预测。检查融合结果后调用：

```text
qwen_cua_execute(session_id=<T02 返回值>, action_indexes=[0])
```

通过标准：

- `status=success`；
- `execution_results[0].status=success`；
- `backend_feedback.ok=true`；
- `session_continuable=true`；
- 鼠标落在预期位置，没有其他界面副作用。

### T05：窗口移动后坐标重投影

1. 对一个普通可移动窗口请求“把鼠标移到窗口内某个明显控件中心”。
2. 记录预测时的 `target_window.geometry` 和 `window_relative_coordinate`。
3. 不改变窗口内容，人工把同一窗口平移 100～200 像素。
4. 调用 `qwen_cua_execute`。

通过标准：

- 没有因窗口单纯平移而拒绝；
- 鼠标落在移动后窗口的同一相对位置；
- 没有落在预测时的旧全局坐标。

注：当前返回值尚未直接暴露重投影后的实际坐标，本用例需视觉确认。这是当前可观测性缺口。

### T06：目标窗口变化时拒绝

1. 在计算器内生成一个坐标动作提案。
2. 执行前，用文本编辑器完全遮住该坐标，或关闭/切换掉目标窗口。
3. 调用 `qwen_cua_execute`。

通过标准：

- `status=refused`；
- `refusals[].reason` 为 `target_window_changed` 或 `target_window_missing`；
- 没有任何鼠标点击或键盘输入；
- `backend_feedback.ok=true` 时可在同一 session 重新预测。

### T07：多轮任务和会话一致性

在计算器中执行任务：

```text
使用屏幕工具在计算器输入 7 + 5 并显示结果。
每轮只允许调用一次 qwen_cua_predict；检查融合结果后再执行。
后续轮次必须复用完全相同的 instruction 和 session_id。
```

通过标准：

- `step` 按 1、2、3…递增；
- 每次执行都反馈到同一 session；
- 最终结果为 12，不点击其他窗口；
- Qwen 返回 `DONE` 时执行该无副作用的终止动作，或显式 reset，不留下 pending session；
- 任何一轮失败都记录在案，不手工“帮它做对”后继续计为成功。

### T08：文本输入

打开空白文本编辑器，任务为：

```text
在空白文档中输入“Treeland CUA test 2026”，不要保存文件。
```

通过标准：先正确聚焦编辑区，文本完整且只出现一次，不触发保存、关闭或其他快捷键。

### T09：滚动与恢复

打开系统设置中一个较长的非破坏性页面，任务为：

```text
向下滚动当前设置页面，直到看到页面下方的内容，不要修改任何设置。
```

通过标准：滚动发生在正确窗口，页面内容发生可见位移，没有点击开关或选项。

## 5. 重复任务集

完成 T01～T09 后，对以下 5 个任务各运行 10 次：

| 任务 | 主要能力 | 任务成功条件 |
| --- | --- | --- |
| 鼠标移到计算器数字 7 | 单步视觉定位 | 落点位于按钮内，无点击 |
| 计算器完成 7+5 | 多轮点击与状态 | 结果为 12 |
| 空白文档输入固定文本 | 聚焦与键盘输入 | 文本完整且只出现一次 |
| 设置页面只滚动 | 窗口选择和滚动 | 内容移动且设置未改变 |
| 预测后遮挡目标 | 安全拒绝 | 每次都拒绝，无误操作 |

每轮开始前恢复一致的窗口位置、尺寸、页面状态和鼠标位置。

## 6. 记录模板

```markdown
| 时间 | 任务 | 轮次 | session_id | step 数 | 预测正确 | 目标窗口正确 | 坐标命中 | 执行/拒绝正确 | 任务成功 | 失败层 | 备注 |
| --- | --- | ---: | --- | ---: | --- | --- | --- | --- | --- | --- | --- |
| 2026-08-31 | 计算器 7+5 | 1 | ... | 4 | 是 | 是 | 是 | 是 | 是 | - | - |
```

`失败层` 只选一个首要原因：

- `model_understanding`：Qwen 理解任务或界面错误；
- `model_coordinate`：动作对，但 Qwen 坐标错；
- `tree_fusion`：坐标对，但目标窗口映射错；
- `reprojection`：窗口移动后重投影错；
- `stale_validation`：应拒绝却执行，或应执行却拒绝；
- `executor`：已校验动作执行失败；
- `session_state`：步数、pending、reset 或反馈状态错误；
- `postcondition`：动作执行成功，但界面没有达到预期状态。

## 7. 核心指标

1. **任务成功率** = 完整成功任务数 / 总任务数。
2. **步骤意图正确率** = 语义正确的 Qwen 动作数 / 总预测动作数。
3. **目标窗口正确率** = `target_window` 正确的坐标动作数 / 总坐标动作数。
4. **坐标命中率** = 位于预期控件可点击区域的坐标数 / 总坐标动作数。
5. **危险误执行率** = 目标已变化但仍执行的次数 / 目标变化测试数，目标必须为 0。
6. **拒绝误报率** = 目标仍有效但被拒绝的次数 / 有效执行测试数。
7. **恢复率** = 拒绝或失败后在同一任务中最终恢复成功的次数 / 可恢复失败数。

首轮基线不设主观的高成功率目标。先完整记录 50 次重复任务，再根据失败分层决定下一阶段优先修正 Qwen prompt、坐标融合、帧一致性、重投影还是执行后验证。
