# AutoUI MCP v2.2 实现与验证状态

本文是当前实现的交付记录和后续工作入口，不重复已完成阶段的设计草案。架构说明、模块图和
运行路径见 [v2.2 当前架构](treeland-autoui-mcp-v2-design.md)。桌面验收步骤见
[手工验收指南](manual-test-guide.md)。

## 当前交付

v2.2 的结构性重构已完成，当前包版本为 `0.2.0`：

| 项目 | 实现状态 | 代码入口 |
| --- | --- | --- |
| 当前协议与配置 | 已完成：protocol/config schema 为 2；旧权限、确认和兼容入口已移除 | `core/protocol.py`、`server_config.py`、`facade.py` |
| 提案校验执行链 | 已完成：`ProposalValidator.prepare/recheck` 替代 ActionGate；执行锁将重检和输入注入保持在同一临界区 | `core/proposal_validator.py`、`core/orchestrator.py` |
| 状态与回执 | 已完成：TaskRepository 管理单一运行态、去重、receipt 与 provider finalization | `core/task_repository.py`、`core/task_state.py` |
| 证据与断言 | 已完成：evidence provider 收集事实，AssertionEvaluator 归约 completed/retrying/failed | `core/evidence.py`、`core/assertion_evaluator.py` |
| recording/diagnostic | 已完成：默认关闭；审计为旁路，diagnostic 按配置注册 | `core/audit.py`、`core/audit_recorder.py`、`audit_cli.py` |
| 连接边界 | 已完成：默认 loopback；bearer token 由 FastMCP verifier 验证 | `server_config.py`、`transport_auth.py` |
| 依赖和使用入口 | 已完成：LangChain 与 OmniParser 为 optional extras；smoke 支持 FastMCP session/SSE | `pyproject.toml`、`smoke.py` |

## 实施时采用的边界

1. Core 不依赖 Treeland、Deepin、Qwen、PyAutoGUI 或 MCP transport；这些能力都通过 ports/adapters
   或 desktop backend 接入。
2. 常规输入不经语义审批，但每次输入都经过 action 参数、能力、坐标和目标依赖校验，以及执行前重检。
3. 运行状态不从审计存储读取；记录失败不能推翻真实 receipt，也不触发输入重放。
4. 空断言的成功语义为 `delivered-unverified`，不是 `completed`；带 assertions 的任务必须有证据。
5. desktop shortcut/application 工具复用 Core transaction runner，不能另建绕过 validator 的执行路径。

## 已验证内容

截至 2026-09-28，以下验证已在当前代码上完成：

| 范围 | 结果 |
| --- | --- |
| Python 回归 | `uv run --with pytest pytest -q`：`131 passed, 27 subtests passed` |
| unittest 回归 | `python -m unittest discover -s tests`：`127 tests` 通过 |
| 静态/打包检查 | `compileall`、`git diff --check`、`uv lock --check` 通过 |
| 本地 MCP smoke | `uv run autoui-smoke --config config/mcp-autoui.json` 通过；已验证 streamable-HTTP session、SSE 响应和本机代理绕过 |
| 部署验证器 | 对本地 `/mcp` 的无输入验证通过；默认 recording 模式未注册 diagnostic，符合配置 |
| 真实 Treeland/Deepin | 已验证编辑器启动、低风险快捷键、无输入 Qwen 观察和 Qwen 启动终端；`htop` 的预期 app id 设置不符时正确得到 partial，未误报完成 |

Qwen 键盘编辑与恢复任务曾返回 failed，未被计为成功。当前默认 recording 没有保留足以定位其
根因的详细诊断材料，因此不能把它定性为模型、executor 或 assertion 的实现缺陷。

## 尚未完成的验证

这些是发布/稳定性验证缺口，不表示 v2.2 核心架构尚未实现：

- 完整手工任务矩阵：文本准确性与清理、拖拽、多动作 receipt、stale recheck，以及重复稳定性测试。
- 在同一可用 Wayland 会话中启用 `recording.audit=true` 与 `recording.diagnostic=true`，为失败的
  输入任务采集可诊断证据。
- AT-SPI、OmniParser、LangChain 远程连接，以及审计重启/归档/保留期行为的端到端验收。
- 对 `gui_run` 的失败响应补充紧凑而稳定的公开 error/reason 信息；当前诊断关闭时，部分失败只
  暴露 task status，降低了现场排障能力。
- 如需用部署验证器做模型输入探针，为它提供可配置的读取超时；默认短超时适合无输入健康检查。

在这些验证完成前，S5 保持“进行中”，不要把真实桌面成功率或稳定性宣称为已达标。

## 建议执行顺序

1. 在启动服务的同一图形会话中启用 audit + diagnostic，先复现 Qwen 键盘任务，保存 trace 和
   action/evidence 事实，再决定修复位置。
2. 按手工验收指南补齐 T-02、T-04、T-06、T-07；每项记录初始状态、receipt、断言、耗时和人工介入。
3. 补齐 optional provider、远程 LangChain、audit retention 的端到端验证。
4. 修复经证据确认的问题后，重新运行全量 pytest、unittest、smoke 和相关真实桌面用例。

## 常用命令

```bash
uv run --with pytest pytest -q
python -m unittest discover -s tests
uv run autoui-smoke --config config/mcp-autoui.json
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

若需诊断工具，修改服务配置而不是临时改变 Core 行为：

```json
"recording": {
  "audit": true,
  "diagnostic": true,
  "directory": ".autoui-audit",
  "retention_days": 7,
  "max_gib": 16
}
```

服务需从已准备的桌面会话启动；单独 TTY 进程通常不会继承 Wayland 所需环境，不能替代真实桌面验收。
