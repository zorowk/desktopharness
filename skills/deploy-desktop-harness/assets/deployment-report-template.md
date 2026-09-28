# DesktopHarness 部署报告

> 主控模型仅填写已观察到的事实；未采集写“未采集”。不要包含 token、密码、私钥、API key、
> 原始环境变量、截图、桌面内容或模型原文。

## 结论

- 时间（UTC）：`<YYYY-MM-DDTHH:MM:SSZ>`
- 目标机器：`<hostname / 脱敏标识>`
- 状态：`READY / FAILED / PARTIAL / BLOCKED`
- 结论：`<一句话：什么可用，或阻塞在哪里>`

## 环境

- DesktopHarness：`<版本或 revision>`
- 桌面 backend / 会话：`<backend / session type>`
- MCP endpoint：`<endpoint，不含凭据>`

## 验证

| 项目 | 结果 | 简要证据 |
| --- | --- | --- |
| MCP 连通 / describe | `通过 / 失败 / 未采集` | `<摘要>` |
| 桌面 observe / screenshot | `通过 / 失败 / 未采集` | `<摘要>` |
| 已授权输入探针 | `通过 / 失败 / 未执行` | `<receipt/assertion 摘要>` |

## 问题与下一步

- 失败阶段 / 原因码：`<无 / phase / reason code>`
- 未完成项：`<无 / 内容>`
- 下一步：`<无 / 一个安全且具体的动作>`
