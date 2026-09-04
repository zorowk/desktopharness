# 文档导航

项目的当前架构、接口和验收要求均以 v2 文档为准；不要以日期记录、旧
`qwen_cua_*` 工具说明或 OmniParser 实验记录推断当前行为。

## 当前规范

- [v2 跨合成器设计](treeland-autoui-mcp-v2-design.md)：协议、不变量、组件边界和验收条件。
- [v2 实现与扩展指南](treeland-autoui-mcp-v2-implementation.md)：已实现范围、扩展方式和当前 MCP 工具面。
- [v2 手工验收与回归计划](manual-test-guide.md)：真实桌面测试前提、测试矩阵、记录格式和通过标准。

## 在其他桌面/合成器上继续开发

按以下顺序阅读和实施：先在设计文档确认 Core 不变量与 port 边界，再阅读实现指南中的
“新增合成器”和“新增桌面后端能力”。新后端必须拥有自己的合成器观察能力，以及可选的
应用启动和平台快捷键能力；Core 只接收它们各自实现的 port，不能引入特定桌面系统的
import 或命令。接入真实的新平台时，需要在 `desktop_backend.py` 注册它；JSON 配置的
`desktop_backend.kind` 会在启动时显式选择已注册后端。当前 registry 只有
`treeland-deepin`，因此这不是已经完成的跨平台运行时支持。

OmniParser 默认关闭；启用后仅作为 v2 的 Evidence/Grounding Provider。它不注册
旧的直连执行工具，且其概率性证据不能绕过 v2 的 Proposal、PolicyDecision、Guard、
Receipt 与 Assertion 流程。
