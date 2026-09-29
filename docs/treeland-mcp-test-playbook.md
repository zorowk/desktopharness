# Treeland MCP 人工式 GUI 测试剧本

只测试系统已有应用的真实用户操作，不使用 testbench、定时器或 `PASS` 包装窗口。

## 通用规则

1. 使用 MCP 启动和操作桌面；不通过 SSH、shell 或 `treeland-debug` 直接控制 UI。`wlr-randr`、`wl-copy` 和 `wl-paste` 只可用于本剧本明确列出的只读状态检查或剪贴板恢复。
2. 每项开始前确认所需应用和硬件可用；不满足时记录 `skipped`。AT-SPI 不属于前置条件。
3. 每个编号步骤是一个可观察的 GUI 阶段。只有该步骤的阶段完成条件已经由屏幕或窗口树观察到，才能进入下一步；不要因前一子动作已完成而跳过同一步中的后续动作。
4. 中文输入法切换使用 `Ctrl+Shift`。中文输入结束后，按 `Shift` 切回英文输入状态并观察确认。
5. 每项结束后关闭本项创建的窗口、恢复显示或输入法状态、清理临时文件，再调用 `gui_run(operation="reset", task_id=<ID>)`。

### 固定测试夹具

本剧本就是文本用例的唯一夹具。执行前，控制器将当前 checkout 中的本文件复制到
`/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md`；复制是前置数据准备，不计入
GUI 测试步骤。控制器必须确认 `/home/uos/DesktopHarness-Test/` 可写，并在每项前确认该副本
存在。任务合同向 Qwen 提供文中写出的完整路径，不让它推导 checkout 位置。固定目录不可写
或夹具副本缺失时，记录 `skipped`。

## TMC-101：启动编辑器、输入并保存文档

前置条件：应用启动器和系统编辑器可用；`/home/uos/DesktopHarness-Test/` 可写；`/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md` 可打开。

1. 按 `Super`（Meta）键打开应用启动器，在搜索框中输入 `Text Editor`（中文界面名称为“文本编辑器”）并回车打开。阶段完成条件：编辑器为活动窗口。
2. 在编辑器按 `Ctrl+O`。阶段完成条件：文件选择器为活动窗口。
3. 在文件选择器按 `Ctrl+L` 打开位置栏，输入 `/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md`，按 Enter。阶段完成条件：编辑器重新成为活动窗口，且窗口标题包含 `treeland-mcp-test-playbook.md`。
4. 从可见正文中选择一个完整的短段落，关闭参考文档后新建文档并逐字输入该段落。阶段完成条件：新文档为活动窗口，且 Qwen 视觉确认输入文本完整。
5. 用保存对话框将新文档保存到 `/home/uos/DesktopHarness-Test/TMC-101.md`。阶段完成条件：窗口标题包含 `TMC-101.md`。

通过：Qwen 在编辑器正文中视觉确认所选段落完整且保存成功；编辑器为活动窗口，窗口标题包含 `TMC-101.md`。可用时再以 AT-SPI `document.text` 精确比对。

清理：关闭文档和编辑器，删除 `/home/uos/DesktopHarness-Test/TMC-101.md`，关闭启动器。

## TMC-102：修改单屏分辨率并恢复基线

前置条件：控制中心可用；MCP 通过图形会话内的只读 `wlr-randr` 已发现当前输出并列出至少两个可用模式；已记录启动前的输出布局、主输出、分辨率和缩放。没有可用输出或可用模式少于两个则 skipped。

1. 打开控制中心。阶段完成条件：控制中心为活动窗口。
2. 打开显示设置。阶段完成条件：当前输出及其可用分辨率模式在界面中可见。
3. 选择一个与基线不同的可用分辨率模式，并应用变更。阶段完成条件：控制中心界面和只读 `wlr-randr` 都显示新模式。
4. 在同一显示设置中选择启动前记录的分辨率并应用。阶段完成条件：控制中心界面和只读 `wlr-randr` 都显示基线模式。

通过：每次切换都由控制中心、Qwen 屏幕观察和 `wlr-randr` 输出状态确认；最终输出状态与基线一致。

清理：关闭控制中心和设置对话框；再次确认基线仍有效。

## TMC-103：切换中文输入法并输入中文

前置条件：系统编辑器可用；已启用中文拼音输入法；切换快捷键为 `Ctrl+Shift`；候选面板可见；`/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md` 可打开。任一条件不满足则 skipped。

1. 打开文本编辑器并按 `Ctrl+O`。阶段完成条件：文件选择器为活动窗口。
2. 按 `Ctrl+L` 打开位置栏，输入 `/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md`，按 Enter。阶段完成条件：编辑器标题包含 `treeland-mcp-test-playbook.md`。
3. 选择一条完整、可见的中文句子作为参考，关闭参考文档后新建文档并聚焦文本区。阶段完成条件：新文档文本区获得焦点。
4. 按 `Ctrl+Shift` 切换到中文输入法。阶段完成条件：候选面板或输入法状态的可见变化表明中文输入法已启用。
5. 用拼音逐段输入参考句；每次出现候选面板时，依据可见候选文字选择正确中文并提交。阶段完成条件：编辑器中的完整中文句与参考句视觉一致。
6. 按 `Shift` 切回英文输入状态。阶段完成条件：输入法状态的可见变化表明英文输入已恢复。

通过：Qwen 视觉确认候选面板选择正确，且编辑器中的中文与参考句一致。Dock 输入法图标只能作为视觉线索，不能单独作为断言；可用时再以 AT-SPI `document.text` 精确比对。

清理：恢复原输入法，关闭编辑器和候选面板，不保存临时文本。

## TMC-104：多窗口切换与窗口状态保留

前置条件：系统编辑器和文件管理器可用。

1. 通过启动器启动 `Text Editor`，新建文档并输入固定 ASCII 文本 `Treeland window state test`。阶段完成条件：编辑器为活动窗口，且 Qwen 视觉确认短文本完整。
2. 将文档保存到 `/home/uos/DesktopHarness-Test/TMC-104.txt`。阶段完成条件：编辑器标题包含 `TMC-104.txt`。
3. 通过启动器启动 `File Manager`（中文界面名称为“文件管理器”）。阶段完成条件：文件管理器为活动窗口。
4. 在文件管理器按 `Ctrl+L`，输入 `/home/uos/DesktopHarness-Test/`，按 Enter。阶段完成条件：该目录内容可见。
5. 用任务视图或 `Alt+Tab` 返回编辑器。阶段完成条件：编辑器为活动窗口，且短文本仍可见。
6. 最大化编辑器，再恢复。阶段完成条件：窗口树分别观察到最大化和恢复状态。
7. 最小化编辑器，再恢复。阶段完成条件：窗口树分别观察到最小化和恢复状态，最终编辑器为活动窗口。

通过：窗口树确认每个窗口状态；最终编辑器为活动窗口、处于恢复状态，且 Qwen 视觉确认固定短文本仍完整。可用时再以 AT-SPI 精确比对。

清理：关闭本项创建的文件管理器、编辑器和任务视图，删除 `/home/uos/DesktopHarness-Test/TMC-104.txt`。

## TMC-105：跨应用复制粘贴

前置条件：系统编辑器和终端或第二个编辑器窗口可用；`/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md` 可打开。启用 `wl_clipboard` evidence provider；测试前的剪贴板必须是可恢复的纯文本，否则 skipped。

1. 打开源编辑器并按 `Ctrl+O`。阶段完成条件：文件选择器为活动窗口。
2. 按 `Ctrl+L` 打开位置栏，输入 `/home/uos/DesktopHarness-Test/treeland-mcp-test-playbook.md`，按 Enter。阶段完成条件：源编辑器标题包含 `treeland-mcp-test-playbook.md`。
3. 选择并复制一个完整、可见的段落。阶段完成条件：只读 `wl-paste` 读出的 `clipboard.text` 与该段落一致。
4. 启动或切换到目标应用并粘贴。阶段完成条件：Qwen 视觉确认该段落完整出现。
5. 回到源编辑器，选择并复制另一个不同的可见段落。阶段完成条件：只读 `wl-paste` 显示的新文本与第一次不同。
6. 切换到目标应用并再次粘贴。阶段完成条件：Qwen 视觉确认第二段完整出现，且不是旧内容。

通过：每次复制后由 `wl-paste` 读取的 `clipboard.text` 与当前参考段落一致；Qwen 视觉确认两段参考文本均完整出现，第二次粘贴不是旧内容。可用时再以 AT-SPI 精确比对。

清理：关闭源/目标应用和临时文档；用 `wl-copy` 恢复测试前保存的纯文本剪贴板，不清空测试前已有的用户剪贴板。

## 执行与报告

每项按“前置检查 → GUI 操作 → 真实证据验证 → 清理 → reset”执行。报告记录 ID、completed/failed/skipped、失败原因、恢复结果和诊断 artifact 引用；不得记录凭据或用户敏感内容。
