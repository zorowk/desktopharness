# treeland-autoui-mcp 验收交接（2026-09-01）

## 接手目标

继续完成 `play.md` 的**阶段 2：迁移 Qwen-CUA 后端并完成联调**。验收动作必须走本项目 MCP 链路，不要绕过 Qwen-CUA 或直接调用 ydotool / pyautogui。

Qwen-CUA 负责视觉理解和提出单步动作；本地控制层结合 Treeland 树、坐标约束和执行前复核决定是否执行。OmniParser 旧接口保留但默认关闭，之后再按同一指标单独进行对比测试。

## 当前结论

项目目前对“有人监督、低风险、可恢复”的日常桌面操作**有条件通过**；无人值守或高风险操作不通过。完整结论见 [desktop-acceptance-conclusion-2026-09-01.md](desktop-acceptance-conclusion-2026-09-01.md)。

已证明的能力包括：精确移动、动态窗口重投影、目标窗口变化拒绝、基础点击/双击/右击/输入/滚动/快捷键、单步 Qwen 动作约束，以及被拒绝提案后的会话恢复。当前问题、训练候选和可观测性边界见 [model-training-observations.md](model-training-observations.md)。

## 先做：重启并复验 FailSafe 修复

当前 MCP 进程仍是旧代码。未提交修复会在鼠标处于屏幕角落时返回结构化 `cursor_in_failsafe_corner`，而不是让 PyAutoGUI 抛出未经处理的 `FailSafeException`；它**不会**全局关闭 `pyautogui.FAILSAFE`。

1. 在 Treeland 桌面会话终端停止并重启服务：

   ```bash
   Ctrl+C
   ./client_env.sh
   ```

2. 手动把鼠标从右下角 `(1919,1079)` 移开；上次边界测试将它停在那里。
3. 用 MCP 执行受约束移动到 `(1919,1079)`，再预测并执行安全的 `Super+D` 或其他无破坏按键。
4. 预期第二次 `qwen_cua_execute` 返回 `status: "error"`、`error_code: "cursor_in_failsafe_corner"`，且不会注入按键；不应再出现原始 `FailSafeException`。
5. 测完后手动移开鼠标，再继续其他验收；此策略刻意保留用户可用的安全角行为。

MCP 顺序：`qwen_cua_predict -> inspect proposal/tree/fusion -> qwen_cua_execute -> qwen_cua_predict -> qwen_cua_reset`。每个测试 session 结束都 reset。不要以终端 ydotool、pyautogui 或其他工具代替验收动作；它们只可用于底层诊断。

## 当前工作区：不要覆盖或误丢弃

```text
M  play.md
M  src/mcp_autogui/qwen_actions.py
M  tests/test_qwen_actions.py
?? docs/desktop-acceptance-conclusion-2026-09-01.md
?? docs/model-training-observations.md
?? docs/acceptance-handoff-2026-09-01.md
```

- `qwen_actions.py` 与对应测试：FailSafe 结构化拒绝修复。
- `play.md`：唯一的执行计划与实时验收记录；继续工作时必须更新。
- 两份结论文档与本交接文档均尚未提交。
- 存在用户保留的 `stash@{0}`：`e28dce0 update pyautogui and wayland_automation`。不要应用、删除或覆盖。

关键已提交代码：

- `84613d5 fix(qwen-cua): preserve sessions across dynamic constraints`：稳定任务 instruction 与单轮动态约束分离；拒绝/解析失败不会污染会话。
- `8742c36 fix(input): apply flat profile to ydotool pointer`：正确写入 `dde` DConfig 作用域，解决 Wayland/Treeland 绝对移动加速偏差。
- `71486bf feat(qwen-cua): add controller coordinate constraints`：控制层给 Qwen 精确坐标约束并复核，不能绕过 Qwen 直接行动。

如用户要求提交，按功能拆分：FailSafe 代码+测试一个提交；计划与 docs 另一个提交。不要混入用户其他修改。

## 已完成的真实桌面验收证据

- 嵌入式后端正常：模型 `qwen3_rl`，不需要启动 `~/gui-mcp/`。
- MCP 工具可用：`qwen_cua_predict`、`qwen_cua_execute`、`qwen_cua_reset`、`qwen_cua_status`。
- 精确移动：`(100,100)` 和 `(600,316)` 通过；后者重复 10 次均成功，最大量化偏差 1 px。
- 窗口移动重投影：Text Editor 从 `(80,80)` 移到 `(280,96)` 后，窗口内相对点重算为 `(600,316)`；鼠标读回正确。
- 目标窗口变化：Text Editor 提案被 Firefox 覆盖时执行返回 `target_window_changed`，未注入输入；后续能重新提案。
- 输入/清理：`Untitled 4` 曾输入测试文本再用 `Ctrl+A` / `Backspace` 恢复 `Characters 0`。Qwen 曾将 `Qwen` 视觉转写为 `Owen`；没有确定性文本真值前，不可宣称字符级精确通过。
- 双击 `cua-test.html` 后 Firefox 打开；单步 `scroll(-3)` 有可见页面变化；右击实际打开菜单。Tree 只将该菜单归到 `BackgroundContainer`，无法区分桌面文件与 Dock 菜单，这是 Tree 子控件语义边界，不是右击失败。
- 边界：`(1919,1079)` 可准确执行；`(1920,1080)` 在调用模型前被控制层拒绝。
- 格式安全：多个 `computer_use` 调用或约 80 个重复 `moveTo` 会被解析器拒绝、无副作用；严格要求唯一 `scroll(-3)` 后能成功。

## 接下来优先验收/修复

1. 完成并记录 FailSafe 真实复验，将 `play.md` 对应项勾选。
2. 验证并修复 `visible=false` 融合：显示桌面后，隐藏全屏 Firefox 曾排在 `BackgroundContainer` 前。先采集 Tree 确认层名（可能为 `WorkspaceContainer`，而代码仅匹配 `workspace`），补单测后再修复过滤。
3. 为端到端延迟加分阶段 telemetry。模型约 0.9–2.2 秒，外层 MCP 偶有 55–215 秒；现在不能归因。不要全局设置 `NO_PROXY`，Codex 需要代理；若需绕过本机代理，应在 Mihomo 规则中让 `127.0.0.0/8`、`localhost` DIRECT，并分别记录截图、取树、执行、反馈、响应序列化耗时。
4. 为文字输入建立确定性后置条件（AT-SPI、专用测试页状态或其他可审计来源），再做任务 2/5 的 10 次重复验收。
5. 补部分执行/异常后安全重置、`pending_sessions` 字段语义和其余阶段 2 项；不要把阶段 3 的规划误当成阶段 2 全部必须项。

## 操作边界与验证

- 仅在可恢复测试桌面操作；不要保存、删除文件、提交、付款或授权。
- 不要碰用户已有 `Untitled 3`；此前测试使用 `Untitled 4`。
- GUI 结论需保存 Qwen 原始动作、融合/执行坐标、Tree 摘要、执行结果和下一帧观察。没有独立真值的视觉结论必须标为候选/不确定。
- 发现问题时直接修复并加测试，但先确认根因属于本项目而不是 Treeland、Mihomo 或用户桌面状态。

每次修改后的最低验证：

```bash
UV_CACHE_DIR=/tmp/treeland-autoui-uv-cache uv run python -m unittest discover -s tests -p 'test_*.py'
UV_CACHE_DIR=/tmp/treeland-autoui-uv-cache uv run python -m compileall -q src
git diff --check
```

上次 FailSafe 修改后上述检查已通过：32 个单元测试全部通过、编译通过、diff 检查通过。任何新修改都要重新运行并记录结果。
