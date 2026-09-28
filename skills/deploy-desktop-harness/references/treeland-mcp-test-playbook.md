# Treeland MCP 模拟人工测试剧本

这是给**主控 AI**执行的剧本，不是 Python 测试程序。主控 AI 必须完整阅读本文件，
在 Multica 控制环境中通过已部署的 AutoUI MCP 顺序执行全部用例。目标机只由
`client_env.sh` 启动 MCP 服务；主控 AI 不得 SSH 到目标机执行窗口命令，也不得绕开
MCP 调用 `treeland-debug` 控制窗口。

## 运行边界

1. 先使用部署 skill 启动目标机的 `client_env.sh` 并完成 `gui_run(describe)`、截图和
   Treeland 窗口树预检。
2. 之后主控 AI 只调用 `desktop_applications_list`、`desktop_application_launch`、
   `desktop_capabilities_list`、`desktop_shortcut_invoke`、`gui_run`，以及在已开启诊断时
   调用 `gui_diagnostic`。所有鼠标和键盘动作均须由 `gui_run` 的模型提案执行。
3. 按本文顺序逐项执行。每项均要先启动指定测试台，再调用一次
   `gui_run(operation="run", task_contract=...)`；不手工拼接或注入 action proposal。
4. `completed` 才是通过。`failed` 是失败；应用缺失、硬件不足、MCP/provider 不可用或
   不能建立可观察前置条件是 `BLOCKED`，不是通过，也不算 Treeland 产品失败。
5. 无论通过、失败或阻塞，都记录：用例 ID、实际 task state、MCP 响应、失败原因；
   已开启诊断时再保存 trace、截图和窗口树 artifact。失败后继续下一项，不要重试超过
   task contract 的上限。
6. 每项结束后调用 `gui_run(operation="reset", task_id=<ID>)`，以免运行态影响下一项。
   审计记录会保留，reset 不等于删除证据。

## 测试台契约

Treeland 的许多协议能力没有普通应用入口。部署测试环境时，将 Treeland 的 existing
examples 包装为可发现的 `.desktop` 测试台应用。测试台以普通 GUI 形式暴露步骤，主控
AI 像人一样点击、输入和观察；它不直接调用私有协议。

每个测试台必须在完成时把自己的**活动窗口标题**设为 `PASS <ID>`，失败为
`FAIL <ID>`，环境不足为 `BLOCKED <ID>`。这是当前 MCP 可以由 compositor-window
evidence 确定性验证的共同结果。没有该应用或标题契约时必须记录 BLOCKED，不能根据
模型的视觉描述自行判定成功。

统一的 task contract 形状如下；表中的“测试任务”和“预期”替换相应字段：

```json
{
  "task_id": "TMC-XXX",
  "goal": "表中测试任务",
  "assertions": [
    {
      "assertion_id": "pass",
      "path": "active_window.title",
      "operator": "contains",
      "expected": "表中的 PASS TMC-XXX"
    }
  ],
  "limits": {"max_steps": 10, "max_retries": 1}
}
```

## 顺序用例

| ID | 测试台应用 | 测试任务（交给 `gui_run`） | 唯一预期 |
| --- | --- | --- | --- |
| TMC-001 | `treeland-test-observability` | 按界面提示观察窗口树、活动输出和截图预览。 | `active_window.title contains "PASS TMC-001"` |
| TMC-010 | `treeland-test-window-basics` | 依次点击 Window A、Window B、Window A，确认焦点和层级。 | `PASS TMC-010` |
| TMC-011 | `treeland-test-window-basics` | 拖动 Window A 标题栏，再拖动右下角边框缩放。 | `PASS TMC-011` |
| TMC-012 | `treeland-test-window-basics` | 依次最大化、恢复、最小化、恢复、全屏和恢复目标窗口。 | `PASS TMC-012` |
| TMC-013 | `treeland-test-workspace` | 切换工作区、显示桌面并恢复原窗口。 | `PASS TMC-013` |
| TMC-020 | `treeland-test-input` | 完成指定点击、双击和滚动序列。 | `PASS TMC-020` |
| TMC-021 | `treeland-test-input` | 聚焦文本框，输入 `TMC-021`，再触发界面显示的快捷键。 | `PASS TMC-021` |
| TMC-022 | `treeland-test-gestures` | 完成界面指定的 pinch 与 swipe 手势。 | `PASS TMC-022` |
| TMC-023 | `treeland-test-ime` | 输入指定中英文文本并选择候选项。 | `PASS TMC-023` |
| TMC-030 | `treeland-test-shell` | 打开任务视图和窗口选择器，并选择指定窗口。 | `PASS TMC-030` |
| TMC-031 | `treeland-test-appearance` | 切换客户端装饰、圆角和玻璃效果后恢复。 | `PASS TMC-031` |
| TMC-032 | `treeland-test-wallpaper` | 切换指定壁纸和颜色主题，再恢复初始主题。 | `PASS TMC-032` |
| TMC-033 | `treeland-test-layer-shell` | 创建、聚焦、调整并关闭叠加层。 | `PASS TMC-033` |
| TMC-040 | `treeland-test-output` | 切换主输出、布局和分数缩放，再恢复基线。 | `PASS TMC-040` |
| TMC-041 | `treeland-test-virtual-output` | 创建虚拟输出，将测试窗口移入，移除虚拟输出并确认恢复。 | `PASS TMC-041` |
| TMC-042 | `treeland-test-output` | 使用第二输出完成连接、断开和 region-watch 提示流程。 | `PASS TMC-042` |
| TMC-050 | `treeland-test-data-control` | 在两个窗口间复制、粘贴和使用主选择。 | `PASS TMC-050` |
| TMC-051 | `treeland-test-capture` | 选择指定窗口和区域，开始捕获并确认预览帧到达。 | `PASS TMC-051` |
| TMC-052 | `treeland-test-wayland` | 创建模态对话框、激活请求和父子窗口关系，再关闭对话框。 | `PASS TMC-052` |
| TMC-053 | `treeland-test-xwayland` | 移动 X11 窗口、切换焦点并关闭。 | `PASS TMC-053` |
| TMC-054 | `treeland-test-wine` | 最小化、恢复并调整两个 Wine 窗口的层级。 | `PASS TMC-054` |
| TMC-060 | `treeland-test-idle-lock` | 在隔离会话启动 idle inhibit，按提示验证锁屏后恢复会话。 | `PASS TMC-060` |
| TMC-070 | `treeland-test-resilience` | 启动两个子窗口，使一个按测试台要求异常退出，继续操作另一个。 | `PASS TMC-070` |
| TMC-071 | `treeland-test-resilience` | 重复十次创建、移动、最小化、恢复和关闭窗口。 | `PASS TMC-071` |

## 主控 AI 的固定循环

对表中每一行，严格按下列伪代码执行：

```text
apps = desktop_applications_list(query=<测试台应用>)
if 指定 app_id 不在 apps:
    记录 BLOCKED（testbench application unavailable）
    continue

desktop_application_launch(app_id=<测试台应用>, expected_active_app_id=<测试台应用>)
result = gui_run(operation="run", task_contract=<该行合同>)
记录 result.task_state 与 result.error
if diagnostic 已开启:
    保存 gui_diagnostic(operation="trace", task_id=<ID>)
gui_run(operation="reset", task_id=<ID>)
```

`desktop_application_launch` 失败时同样 BLOCKED。不可为了继续流程而以 shell、SSH、
路径或任意按键替代受限应用启动接口。

## 结束报告

主控 AI 在所有用例结束后生成一个简短本地 Markdown 报告，列出 passed / failed /
blocked 数量和每项的原因；将其附在本次部署报告之后。报告只记录可见任务结果和
artifact 引用，不写入 API key、token、密码或完整桌面敏感内容。
