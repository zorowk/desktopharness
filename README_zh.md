# treeland-autogui-mcp

（[中文版](README_zh.md)）

这是一个使用 [OmniParser](https://github.com/microsoft/OmniParser) 解析屏幕并自动操作 GUI 的 [MCP server](https://modelcontextprotocol.io/introduction)。
已在 Windows 上验证可用。

在 Treeland 环境中，窗口树融合使用合成器提供的 `treeland-debug --tree` 命令；请确保运行 MCP 服务的环境中可从 `PATH` 找到 `treeland-debug`。

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
