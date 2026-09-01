# treeland-autogui-mcp

（[中文版](README_zh.md)）

这是一个使用 [OmniParser](https://github.com/microsoft/OmniParser) 解析屏幕并自动操作 GUI 的 [MCP server](https://modelcontextprotocol.io/introduction)。
已在 Windows 上验证可用。

在 Treeland 环境中，窗口树融合使用合成器提供的 `treeland-debug --tree` 命令；请确保运行 MCP 服务的环境中可从 `PATH` 找到 `treeland-debug`。

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

Qwen-CUA 工具采用显式的两阶段流程：

1. 调用 `qwen_cua_predict(instruction)` 获取一个 `session_id`、Qwen 动作步骤，以及动作坐标与 Treeland window tree 的融合结果。此调用不会执行动作。
2. 检查 `fused_actions` 中的目标窗口和校验信息，然后调用 `qwen_cua_execute(session_id, action_indexes)` 执行全部或选定动作。
3. 使用同一个 `session_id` 再次调用 `qwen_cua_predict` 获取下一步。新任务应使用新的 session，或先调用 `qwen_cua_reset`。

需要由控制层做精确坐标校准、但仍必须经过 Qwen 时，向 `qwen_cua_predict` 传入
`expected_action="mouse_move"` 和 `expected_screenshot_coordinate=[x, y]`。控制层会把截图目标换算为 Qwen 原生的 0–999 坐标，要求 Qwen 给出这一个提案，并拒绝超出像素容差的返回。普通视觉任务不应传这些可选约束。

模型输出只允许经过 AST 解析的 `pyautogui` 白名单动作；任意 Python、动态表达式和未允许的函数都会被拒绝。执行前会重新读取 Treeland tree，如果动作坐标命中的窗口与预测时不同，也会拒绝执行。

内嵌服务先保存待处理动作提案，只有收到本地实际执行结果后才更新正式 Qwen 历史。成功、部分执行、拒绝和失败都会显式反馈给同一 session，避免模型历史与真实桌面状态分叉。旧 HTTP 兼容后端不支持反馈时，仍会自动重置不一致 session。

OmniParser 旧接口仍保留用于对比测试，但默认不注册。需要启用时配置：

```bash
export GUI_OMNIPARSER_ENABLED=1
export OMNI_PARSER_SERVER=host:port
```

详细设计见 [Qwen-CUA 与 Treeland 协同架构设计](docs/qwen-cua-architecture.md)。
手工验收和重复测试步骤见 [Qwen-CUA + Treeland 手工测试指南](docs/manual-test-guide.md)。

## Codex 连接

`client_env.sh` 默认以 Streamable HTTP 启动服务。使用以下命令配置 Codex：

```bash
codex mcp add treeland_autogui_mcp --url http://127.0.0.1:8000/mcp
```

只有旧 SSE 客户端才需设置 `MCP_TRANSPORT=sse`，对应地址仍为
`http://127.0.0.1:8000/sse`。

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
