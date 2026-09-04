# 文档导航

项目的当前架构、接口和验收要求均以 v2 文档为准；不要以日期记录、旧
`qwen_cua_*` 工具说明或 OmniParser 实验记录推断当前行为。

## 当前规范

- [v2 跨合成器设计](treeland-autoui-mcp-v2-design.md)：协议、不变量、组件边界和验收条件。
- [v2 实现与扩展指南](treeland-autoui-mcp-v2-implementation.md)：已实现范围、扩展方式和当前 MCP 工具面。
- [v2 手工验收与回归计划](manual-test-guide.md)：真实桌面测试前提、测试矩阵、记录格式和通过标准。

OmniParser 是默认关闭的历史对照能力，不属于 v2 生产执行路径。在它被改造成
v2 Evidence/Grounding Provider 前，不得用其旧执行工具做当前功能验收。
