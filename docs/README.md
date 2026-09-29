# 文档导航

项目的当前架构、接口和验收要求均以 v2 文档为准；v2.2 迁移已完成 S4，协议、轻量校验执行链、单一运行态、按需记录及依赖/使用入口已收敛。v2.2 允许破坏性变更，直接删除旧版本兼容代码。不要以日期记录、旧
`qwen_cua_*` 工具说明或 OmniParser 实验记录推断当前行为。

## 当前规范

- [v2.2 精简架构设计](treeland-autoui-mcp-v2-design.md)：整体简化目标、核心能力、运行态与记录职责、协议及迁移边界。
- [v2.2 实施指南](treeland-autoui-mcp-v2-implementation.md)：S0–S5 实施顺序、候选提交单元、删除项、保留能力、完成状态和验收条件。
- [v2 手工验收与回归计划](manual-test-guide.md)：真实桌面测试前提、测试矩阵、记录格式和通过标准。
- [Treeland MCP 人工式 GUI 测试剧本](treeland-mcp-test-playbook.md)：项目随 checkout 提供的真实桌面工作流测试；可直接按目标环境调整。
- [v2 项目评分卡](PROJECT_SCORECARD.md)：固定的 release 评分维度、否决项、指标和当前基线评分。
- [S5 真实验证记录](s5-validation-report.md)：自动、连接与已执行的真实桌面用例，以及未完成项。

## 在其他桌面/合成器上继续开发

按以下顺序阅读和实施：先在设计文档确认 Core 不变量与 port 边界，再阅读实施指南中的
“必须保留的能力”及对应阶段。新后端必须拥有自己的合成器观察能力，以及可选的
应用启动和平台快捷键能力；Core 只接收它们各自实现的 port，不能引入特定桌面系统的
import 或命令。接入真实的新平台时，需要在 `desktop_backend.py` 注册它；JSON 配置的
`desktop_backend.kind` 会在启动时显式选择已注册后端。当前 registry 只有
`treeland-deepin`，因此这不是已经完成的跨平台运行时支持。

OmniParser 默认关闭；启用后仅作为 v2 的 Evidence/Grounding Provider。它不注册
旧的直连执行工具，且其概率性证据不能绕过 ProposalValidator、执行回执和完成断言。
