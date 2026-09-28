# treeland-autogui-mcp

（[中文版](README_zh.md)）

这是一个用于安全、可验证桌面自动化的 [MCP server](https://modelcontextprotocol.io/introduction)。当前实现以跨合成器的 AutoUI v2 事务内核为主；Treeland 是首个合成器适配器。

在 Treeland 环境中，窗口树融合使用合成器提供的 `treeland-debug --json tree` 命令；请确保运行 MCP 服务的环境中可从 `PATH` 找到 `treeland-debug`。

## AutoUI v2 通用事务内核

服务默认注册跨合成器的 `gui_run` facade；只有显式开启完整记录诊断时才注册
`gui_diagnostic`。核心只依赖 Canonical Model 和可替换 port；Treeland、Qwen-CUA、
PyAutoGUI 都位于 adapter 层，不进入核心。
Treeland/Deepin 桌面后端还可选提供 Deepin 快捷键和基于 `dde-am` 的应用启动能力。

v2 公开调用流程为：

1. `gui_run(operation="run", task_contract=...)`
2. `gui_run(operation="status", task_id=...)`
3. `gui_run(operation="reset", task_id=...)`

正常响应只返回紧凑任务状态。只有同时启用 `recording.audit` 和
`recording.diagnostic` 时才注册 `gui_diagnostic` 并返回可追踪的 `object_ref`；
诊断接口提供 `observe`、`propose`、`prepare`、`execute`、`evaluate` 和 `trace`。
模型给出的 `claimed_intent` 只作为诊断信息，不参与执行控制。
`ExecutionReceipt.status=delivered` 只表示输入已注入，不表示应用响应或任务完成。

TaskContract 只包含目标、断言、预算和验证 profile。执行前由 `ProposalValidator`
校验动作参数、坐标、能力、目标、焦点和桌面依赖。部署可通过
`deployment.denied_actions` 可选限制 canonical action。

参见 [v2 实现与扩展指南](docs/treeland-autoui-mcp-v2-implementation.md)
和 [v2 设计](docs/treeland-autoui-mcp-v2-design.md)。

## Qwen-CUA

默认操作链路使用本项目内嵌的 Qwen-CUA 服务，不需要启动 `gui-mcp` 后端。模型端点写入
`config/mcp-autoui.json`，只有模型 API key 继续通过环境注入：

```bash
export CUA_MODEL_API_KEY=your-model-api-key   # 模型端点不校验时可省略
```

旧 gui-mcp HTTP 后端、二进制/JSON 回退和 `CUA_BACKEND_*` 配置已不再支持。

所有 Qwen 交互统一走 `gui_run` 工具；旧的 `qwen_cua_*` 工具已删除。内嵌
后端通过 task contract 寻址，每轮产出一个包含非空、有序 action sequence 的 canonical Proposal：

1. `gui_run(operation="run", task_contract={"task_id": ..., "goal": ...,
   "limits": {"max_steps": 5, "max_retries": 2}})`
   执行有界 Proposal 事务循环：
   observe -> propose（Qwen）-> prepare -> recheck -> execute ->
   evaluate -> 归约任务状态，直到任务阻塞或终止。
2. 需要细粒度控制时使用 `gui_diagnostic`：`observe`、`propose`、`prepare`、
   `execute`、`evaluate`、`trace`；`status` 和 `reset` 仍属于 `gui_run`。
   仅在 `recording.audit=true` 和 `recording.diagnostic=true` 时注册；它可展开
   存储对象，例如模型输出（`debug_ref`）、执行回执或断言结果。
3. `gui_run(operation="reset", task_id=...)` 会重置运行时任务和内嵌
   Qwen session，用于开始新任务。

原始动作限制默认关闭，只在配置中显式启用：

```json
"deployment": {
  "denied_actions": ["keyboard.text", "keyboard.shortcut"]
}
```

记录功能默认关闭。仅审计模式只保存任务、Proposal、实际回执、断言与状态转换，
不保存截图、模型原文、完整桌面树或完整证据；诊断模式必须依赖审计：

```json
"recording": {
  "audit": true,
  "diagnostic": true,
  "directory": ".autoui-audit",
  "retention_days": 7,
  "max_gib": 16
}
```

窗口级完成条件通过 task contract 的 assertions 声明，例如
`assertions: [{"assertion_id": "application-active", "path":
"active_window.app_id", "operator": "equals", "expected": "deepin-editor"}]`。
评估器在每个动作之后采集证据；断言未通过前，Qwen 返回 `DONE` 也不能把
任务标记为完成。

模型输出只允许经过 AST 解析的 `pyautogui` 白名单动作；任意 Python、动态表达式和未允许的函数都会被拒绝。执行前会重新读取 Treeland tree，如果动作坐标命中的窗口与预测时不同，也会拒绝执行。

内嵌服务先保存待处理动作提案，只有收到本地实际执行结果后才更新正式 Qwen 历史。成功、部分执行、拒绝和失败都会显式反馈给同一 session，避免模型历史与真实桌面状态分叉。

OmniParser 默认关闭；启用后仅作为 v2 的只读 Evidence/Grounding Provider，
不会注册旧的直连执行接口。它产生概率性控件/文档证据，不能绕过 Proposal
校验、Receipt 或 Assertion 流程：

在 JSON 的 `evidence_providers.omniparser` 中设置 `enabled: true` 和 `endpoint` 即可启用。
启用前需要安装它的可选 HTTP 依赖：`uv sync --extra omniparser`。

当前文档从 [文档导航](docs/README.md) 开始；手工验收和重复测试步骤见
[AutoUI MCP v2 手工验收与回归计划](docs/manual-test-guide.md)。

## Codex 连接

服务端配置见 [`config/mcp-autoui.json`](config/mcp-autoui.json)，字段说明和可复制模板见
[`config/mcp-autoui.example.json`](config/mcp-autoui.example.json)。通过 JSON 的
`desktop_backend.kind` 选择桌面后端；当前唯一可选值是 `treeland-deepin`。启动时传入：

```bash
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

JSON 是唯一的非秘密运行配置入口。API key 和 MCP bearer token 等敏感值不应提交到该文件，
只通过配置指定的环境变量注入。

使用默认 JSON 配置启动后，Codex 连接地址为：

```bash
codex mcp add desktop_harness_mcp --url http://127.0.0.1:8651/mcp
```

服务默认只监听 loopback。可信反向代理必须以 loopback 为 upstream，并负责 TLS 和鉴权；
直接监听非 loopback 地址时必须配置 `transport.auth.mode="bearer-token"`、`token_env`，客户端每次请求携带 `Authorization: Bearer ...`。

`client_env.sh` 会准备 Treeland 桌面会话、清除旧运行时环境变量，并使用
`config/mcp-autoui.json` 启动服务。只有 `AUTOUI_MCP_CONFIG` 可用于覆盖配置文件路径；
它保留桌面会话变量和 `CUA_MODEL_API_KEY` 等密钥，但不保留运行行为配置。

## 安装

1. 请执行以下命令：

```
git clone https://github.com/zorowk/treeland-aitests.git
cd treeland-aitests
uv sync
```

## 远程部署 + LangChain Agent 连接

在**测试机**上运行 MCP 服务，并在**控制机**通过 streamable HTTP 连接。

### 1) 测试机（运行 MCP 服务）

```bash
uv sync
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

不得直接暴露无鉴权的 MCP 进程。使用以 loopback 为 upstream 的 TLS/鉴权反向代理，或在服务端配置 bearer-token 模式。

### 2) 控制机（LangChain Agent）

使用远程配置模板：

```bash
cp langchain_settings/mcp_config.remote.json langchain_settings/mcp_config.json
```

编辑 `langchain_settings/mcp_config.json`：

```json
{
  "mcpServers": {
    "mcp_machine_01": {
      "transport": "streamable-http",
      "url": "http://TEST_MACHINE_1_IP:8651/mcp"
    }
  }
}
```

然后运行你的 LangChain agent（例如 `langchain_example.py`）。需要时以
`uv sync --extra langchain` 安装可选依赖。bearer-token 部署的 Authorization header
应由客户端环境注入，不能写入此文件。
