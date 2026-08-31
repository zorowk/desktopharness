# treeland-autogui-mcp

（[中文版](README_zh.md)）

这是一个使用 [OmniParser](https://github.com/microsoft/OmniParser) 解析屏幕并自动操作 GUI 的 [MCP server](https://modelcontextprotocol.io/introduction)。
已在 Windows 上验证可用。

在 Treeland 环境中，窗口树融合使用合成器提供的 `treeland-debug --tree` 命令；请确保运行 MCP 服务的环境中可从 `PATH` 找到 `treeland-debug`。

## Qwen-CUA

默认操作链路使用独立部署的 Qwen-CUA 后端。启动前配置：

```bash
export CUA_BACKEND_URL=http://127.0.0.1:8326
export CUA_BACKEND_API_KEY=your-api-key       # 后端未启用认证时可省略
export CUA_TLS_VERIFY=1                       # 使用自签名证书时需显式设为 0
```

Qwen-CUA 工具采用显式的两阶段流程：

1. 调用 `qwen_cua_predict(instruction)` 获取一个 `session_id`、Qwen 动作步骤，以及动作坐标与 Treeland window tree 的融合结果。此调用不会执行动作。
2. 检查 `fused_actions` 中的目标窗口和校验信息，然后调用 `qwen_cua_execute(session_id, action_indexes)` 执行全部或选定动作。
3. 使用同一个 `session_id` 再次调用 `qwen_cua_predict` 获取下一步。新任务应使用新的 session，或先调用 `qwen_cua_reset`。

模型输出只允许经过 AST 解析的 `pyautogui` 白名单动作；任意 Python、动态表达式和未允许的函数都会被拒绝。执行前会重新读取 Treeland tree，如果动作坐标命中的窗口与预测时不同，也会拒绝执行。

现有 Qwen 后端会在预测时记录建议动作。因此上一轮必须完整执行成功才能延续同一个 session；动作被拒绝、部分执行或执行失败后，下一次预测会自动重置该 session，避免后端历史与真实桌面状态分叉。

OmniParser 旧接口仍保留用于对比测试，但默认不注册。需要启用时配置：

```bash
export GUI_OMNIPARSER_ENABLED=1
export OMNI_PARSER_SERVER=host:port
```

详细设计见 [Qwen-CUA 与 Treeland 协同架构设计](docs/qwen-cua-architecture.md)。

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
