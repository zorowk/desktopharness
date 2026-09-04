# 文档导航

项目的当前架构、接口和验收要求均以 v2 文档为准；不要以日期记录、旧
`qwen_cua_*` 工具说明或 OmniParser 实验记录推断当前行为。

## 当前规范

- [v2 跨合成器设计](treeland-autoui-mcp-v2-design.md)：协议、不变量、组件边界和验收条件。
- [v2 实现与扩展指南](treeland-autoui-mcp-v2-implementation.md)：已实现范围、扩展方式和当前 MCP 工具面。
- [v2 手工验收与回归计划](manual-test-guide.md)：真实桌面测试前提、测试矩阵、记录格式和通过标准。

OmniParser 默认关闭；启用后仅作为 v2 的 Evidence/Grounding Provider。它不注册
旧的直连执行工具，且其概率性证据不能绕过 v2 的 Proposal、PolicyDecision、Guard、
Receipt 与 Assertion 流程。
