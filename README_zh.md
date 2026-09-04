# treeland-autogui-mcp

（[中文版](README_zh.md)）

这是一个用于安全、可验证桌面自动化的 [MCP server](https://modelcontextprotocol.io/introduction)。当前实现以跨合成器的 AutoUI v2 事务内核为主；Treeland 是首个合成器适配器。

在 Treeland 环境中，窗口树融合使用合成器提供的 `treeland-debug --tree` 命令；请确保运行 MCP 服务的环境中可从 `PATH` 找到 `treeland-debug`。

## AutoUI v2 通用事务内核

服务现在默认注册跨合成器的 `gui_run` facade。核心只依赖 Canonical Model
和可替换 port；Treeland、Qwen-CUA、PyAutoGUI 都位于 adapter 层，不进入核心。
Treeland/Deepin 桌面后端还可选提供 Deepin 快捷键和基于 `dde-am` 的应用启动能力。

v2 显式调用流程为：

1. `gui_run(operation="observe", task_contract=...)`
2. 使用 `gui_run(operation="propose", task_id=...)` 请求 Qwen，或通过同一操作提交一个主控 Proposal
3. `gui_run(operation="decide", task_id=..., proposal_id=...)`
4. `gui_run(operation="execute", task_id=..., proposal_id=...)`
5. `gui_run(operation="evaluate", task_id=...)`

正常响应只返回包含 `object_ref` 的紧凑信封；诊断时使用
`gui_run(operation="trace", object_ref=...)` 展开对象。默认策略在缺少独立语义证据时要求确认，模型给出的 `semantic_intent` 只作为 claim。
`ExecutionReceipt.status=delivered` 只表示输入已注入，不表示应用响应或任务完成。

参见 [v2 实现与扩展指南](docs/treeland-autoui-mcp-v2-implementation.md)
和 [v2 设计](docs/treeland-autoui-mcp-v2-design.md)。

## Qwen-CUA

默认操作链路使用本项目内嵌的 Qwen-CUA 服务，不需要启动 `gui-mcp` 后端。只需配置实际的 OpenAI-compatible Qwen 模型端点：

```bash
export CUA_BACKEND_MODE=embedded
export CUA_MODEL_BASE_URL=http://127.0.0.1:8000/v1
export CUA_MODEL=qwen3_rl
export CUA_MODEL_API_KEY=your-model-api-key   # 模型端点不校验时可省略
export CUA_MODEL_TLS_VERIFY=1                 # 仅自签名测试端点才显式设为 0
```

需要与旧部署对比时，可设置 `CUA_BACKEND_MODE=http`，并继续使用 `CUA_BACKEND_URL`、`CUA_BACKEND_API_KEY` 和 `CUA_TLS_VERIFY`。HTTP 模式只是兼容路径，不是默认依赖。

所有 Qwen 交互统一走 `gui_run` 工具；旧的 `qwen_cua_*` 工具已删除。内嵌
后端通过 task contract 寻址，每轮只产出一个 canonical action：

1. `gui_run(operation="run", task_contract={"task_id": ..., "goal": ...,
   "permissions": {...}, "limits": {"max_steps": 5, "max_retries": 2},
   "policy_overrides": {"unknown": "allow", "content_edit": "allow"}})`
   执行有界的单动作事务循环：
   observe -> propose（Qwen）-> decide -> guard 重检 -> execute ->
   evaluate -> 归约任务状态，直到任务阻塞或终止。
2. 需要细粒度控制时使用显式操作：`observe`、`propose`、`decide`、
   `execute`、`evaluate`（或 `verify`）、`status`、`reset`、`trace`。
   响应默认只返回对象引用；传 `diagnostic=true` 或使用 `trace` 展开
   存储对象，例如模型输出（`debug_ref`）、执行回执或断言结果。
3. `gui_run(operation="reset", task_id=...)` 会重置运行时任务和内嵌
   Qwen session，用于开始新任务。

窗口级完成条件通过 task contract 的 assertions 声明，例如
`assertions: [{"assertion_id": "application-active", "path":
"active_window.app_id", "operator": "equals", "expected": "deepin-editor"}]`。
评估器在每个动作之后采集证据；断言未通过前，Qwen 返回 `DONE` 也不能把
任务标记为完成。

模型输出只允许经过 AST 解析的 `pyautogui` 白名单动作；任意 Python、动态表达式和未允许的函数都会被拒绝。执行前会重新读取 Treeland tree，如果动作坐标命中的窗口与预测时不同，也会拒绝执行。

内嵌服务先保存待处理动作提案，只有收到本地实际执行结果后才更新正式 Qwen 历史。成功、部分执行、拒绝和失败都会显式反馈给同一 session，避免模型历史与真实桌面状态分叉。旧 HTTP 兼容后端不支持反馈时，仍会自动重置不一致 session。

OmniParser 默认关闭；启用后仅作为 v2 的只读 Evidence/Grounding Provider，
不会注册旧的直连执行接口。它产生概率性控件/文档证据，不能绕过 Proposal、
PolicyDecision、Guard、Receipt 或 Assertion 流程：

```bash
export GUI_OMNIPARSER_ENABLED=1
export OMNI_PARSER_SERVER=host:port
```

当前文档从 [文档导航](docs/README.md) 开始；手工验收和重复测试步骤见
[AutoUI MCP v2 手工验收与回归计划](docs/manual-test-guide.md)。

## Codex 连接

服务端配置见 [`config/mcp-autoui.json`](config/mcp-autoui.json)，字段说明和可复制模板见
[`config/mcp-autoui.example.json`](config/mcp-autoui.example.json)。通过 JSON 的
`desktop_backend.kind` 选择桌面后端；当前唯一可选值是 `treeland-deepin`。启动时传入：

```bash
uv run treeland-autogui-mcp --config config/mcp-autoui.json
```

JSON 是推荐入口；`CUA_*`、`GUI_*` 和 transport 环境变量只保留给旧部署兼容。API key 等
敏感值不应提交到该文件，应由受控 secret mechanism 注入。

使用默认 JSON 配置启动后，Codex 连接地址为：

```bash
codex mcp add treeland_autogui_mcp --url http://127.0.0.1:8651/mcp
```

`client_env.sh` 和 `MCP_TRANSPORT` 仅用于旧环境变量部署；SSE 旧客户端地址取决于 JSON
的 `transport.host` 与 `transport.port`，默认是 `http://127.0.0.1:8651/sse`。

## 安装

1. 请执行以下命令：

```
git clone https://github.com/zorowk/treeland-aitests.git
cd treeland-aitests
uv sync
```

## 远程部署 + LangChain Agent 连接（SSE）

在**测试机**上运行 MCP 服务，并在**控制机**通过 SSE 连接。

### 1) 测试机（运行 MCP 服务）

```bash
uv sync
SSE_HOST=0.0.0.0 SSE_PORT=8000 uv run treeland-autogui-mcp
```

开放 `8000` 端口，并记录测试机 IP。

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
      "transport": "sse",
      "url": "http://TEST_MACHINE_1_IP:8000/sse"
    }
  }
}
```

然后运行你的 LangChain agent（例如 `langchain_example.py`）通过 SSE 连接。
（如果要运行 `langchain_example.py`，请改用 `uv sync --extra langchain`。）
