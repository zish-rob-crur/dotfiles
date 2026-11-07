# Task Board 窗口

`main.swift` 使用真正的 AppKit `NSWindow` 承载 `WKWebView`，显示时由系统暴露为
`AXStandardWindow`，供 Raycast 等窗口切换器识别。它是名为 **Task Board** 的普通应用窗口，
保持普通层级，支持移动、缩放、最小化和所有桌面。

HTML 和业务逻辑仍在 `hammerspoon/task_board.lua`。原有看板刷新、待办操作、
tmux 跳转、⌘⇧B、⌘⇧D 以及 `hammerspoon://task-board` 入口保持不变。
关闭按钮隐藏窗口；快捷键或 Dock 图标可重新显示。退出应用后，下次快捷键会重新启动。

## Focus 与 Weekly Tasks

**界面语言约定：Task Board 的所有 UI 文案统一使用英文**，包括标题、按钮、占位文本、
辅助说明、无障碍标签及状态/错误提示。用户填写的任务可以使用任意语言，后续修改继续沿用。

Weekly Markdown 是唯一任务来源。顶部 **Focus** 只是周任务的置顶视图，可以置顶多个任务。
顶部 Focus 使用紧凑条目，周任务保留项目纵列，所有操作仍指向同一个 Markdown 任务。

- **任务正文**：点击打开右侧详情抽屉，不会标记完成。抽屉覆盖右侧，不挤窄项目列；
  可以直接点击其他任务切换详情。**Close / Escape** 关闭并返回原任务；键盘焦点移回背景时自动收起。
- **复选框**：只有它用于勾选完成；**Show completed** 可找回并取消勾选。
- **Pin to Focus / Unpin**：在详情里置顶或移出顶部 Focus，不另建任务。
- **Start Focus**：顶部 Focus 和详情均可启动，自动置顶，并将任务名及全局选择的
  25 / 50 / 90 分钟传入 Raycast 内置 Focus。
- **Edit Markdown / Open log**：在详情中打开周任务文件或本月专注记录。
- **Group → Move**：在详情中选择同一库的目标分组，再点击 Move 保存。任务移动到目标列，
  保留原 ID、正文、日期、完成/置顶状态、评论和专注记录；不会重新创建任务。
  分组选择在本次页面会话中跨关闭/切换任务保留，写入失败也不会清掉选择。
  同组不会重复写入，已删除的目标分组会提示重新选择。复杂的嵌套任务或附带正文需要通过
  **Edit Markdown** 手工调整，避免误拆任务内容。
- **Add task**：顶部统一入口，选择 **Vault** 和 **Group**，写入对应库本周 Markdown 的小节；
  Group 默认 `Inbox`，也列出空分组和只有已完成任务的分组。切换库会重置为 Inbox，
  自动刷新和搜索保留当前选择；若所选分组被删除，会提示重新选择，不会静默写到其他分组。
  需要置顶时再 Pin。要增加全新的分组，可先在周 Markdown 中添加 `## 分组名`。
- **Sources N**：打开详情并展开来源。重复的“飞书原消息”标签从主卡片收起，
  MR、文档名和链接之间的补充文字仍保留。所有来源标签、完整 URL 和顺序均保留，点击外部打开。
  这只改变展示，不改写 Markdown。详情中的 Sources 默认折叠，避免挤压评论。

首次读取会为现有任务补充隐藏 ID，并在同目录保存 `.md.before-unified-focus` 原始备份。
这些标记在 Obsidian 阅读视图中隐藏；手工改名或移动任务时保留 ID，复制成新任务时删除 ID：

```markdown
- [ ] Review the proposal <!-- task:example-task-id --> <!-- focus -->
```

`<!-- focus -->` 表示置顶；删掉即可取消。稳定 ID 随未完成任务跨周结转，用于关联历史，
不依赖标题或行号。UI 完成时同时写入 Obsidian Tasks 的 `✅ YYYY-MM-DD` 和精确时间注释；
直接在 Markdown 勾选且没有时间戳时，不推测完成时间。

旧版 `Inbox/Focus/` 独立任务笔记会保留原文，其任务一次性关联到周任务并附上 Notes 链接，
原有确认过的 session 仍可汇总。已完成的历史任务不会在新的一周重新创建。
看板每 5 秒读取 Markdown，刷新保留搜索、选中的任务、抽屉状态和当前时长选择。
评论草稿在本次页面会话中跨关闭/切换任务保留；重载看板前需点击 Post comment 保存。

### Tmux 折叠

Tmux 位于周任务下方，标题行的 **Collapse / Expand** 可以收起或展开窗口卡片。
收起后只保留一份窗口/状态数量，把更多高度留给任务。选择会保存，刷新和重载后继续生效。
页面高度随窗口调整，任务列各自滚动；展开的 Tmux 占用有上限，不再需要拖动空白间距。

### Search and groups

顶部 **Search tasks** 同时筛选 Focus 与本周任务，支持任务正文、分组名、库名、
`@tag`、`#source`、截止日期和评论内容。不区分英文大小写；空格分隔的关键词需全部匹配，
例如 `CALLE review`。**Clear** 或输入框内按 Escape 清空搜索。默认搜索未完成任务，
打开 **Show completed** 后也会搜索本周已完成任务。没有匹配时会显示提示及结果数量。
搜索只改变视图，不修改 Markdown；刷新保留关键词、评论草稿和展开状态。

This week 按「库 + Markdown 小节」显示独立纵向列，例如 CALLE 一列、DM 一列。
同一分组的任务始终留在该列内；重复出现的小节合并为一列，不同库的同名分组分开显示。
列内上下滚动，分组较多时横向滚动；列标题显示分组名、库及当前可见任务数。
自动刷新保留横向位置与各列的纵向位置，改变搜索条件则回到结果开头。

每列按截止日期优先排列：逾期、今天到期、未来到期（日期从近到远）、没有截止日期，
最后是已完成任务。同一天、无截止日期及已完成任务各自保留 Markdown 原顺序。
项目列本身的顺序固定沿用 Markdown；排序只影响看板展示，不重排文件，也不改变 Focus 的置顶顺序。
日期提示按本地日历计算，今天到期显示 **Due today**，逾期显示 **Overdue**。

### Task comments

点击任务正文，在右侧详情的 **Comments** 区域输入多行评论，点击 **Post comment** 保存。
已有评论的任务也可点击卡片上的 **Comments N** 直接定位输入框。Focus 与周任务共用同一详情
和草稿；关闭抽屉或切换任务后草稿仍在，保存后显示时间与数量。自动刷新保留原输入框和
中文输入状态，保存失败或保存期间输入了新内容时保留草稿。

评论保存在各库的 `<todo_folder>/comments/<task-id>.md`，例如
`Inbox/Todo/comments/<task-id>.md`。任务名和周次改变不影响关联；完成任务也不会删除评论。
评论独立于专注计时，不会创建待办或变更任务状态。
文件使用 `task_id` 属性、带隐藏 ID 的时间标题及 Markdown 引用正文。可直接编辑内容，
但应保留 ID、时间戳和每一行的 `>` 前缀。工具追加时保留文件里的其他笔记。

CLI 与 `/todo` skill 使用相同数据：

```sh
focus-notes snapshot
focus-notes comments --id <task-id>
focus-notes comment --id <task-id> --text-file /path/to/comment.txt --request-id <uuid>
```

`comment` 也支持 `--text`，但自动化处理自由文本时优先传 UTF-8 文件，避免 shell 展开。
每条评论最多 4000 字符，同一写入重试复用 request ID。
`/todo comment <task title or ID> -- <comment>` 追加评论，
`/todo comments <task title or ID>` 读取评论；有同名任务时先确认目标。

### Raycast 与统计边界

计时、屏蔽网站、暂停和结束会话继续由 Raycast 内置 Focus 管理。
调用使用官方 deep link：`raycast://focus/start?goal=…&duration=…`，不覆盖 Raycast 的屏蔽配置。
点击 Start Focus 不等于已经启动成功，也不等于已经专注了预定时长。
当前未验证到可靠的完成回传：官方 deep link 提供启动/切换/结束操作，没有已验证的状态读取；
本机 Focus 日志采样未返回可用事件。因此本版本只自动记录 **unconfirmed request**，
不会推测开始、结束、完成次数或实际分钟数。参见 [Raycast Focus 文档](https://manual.raycast.com/focus)。

记录按月保存在各库 `Inbox/Focus/YYYY-MM.md`，可用 config.toml 的 `focus_folder` 改路径。
详情抽屉的 **Focus history** 展示确认过的次数、分钟数、首次开始、任务完成时间、
未确认请求数及逐次记录。

```markdown
| Request | Task ID | Task | Requested | Planned min | Started | Finished | Focus min | Result | Source |
| --- | --- | --- | --- | ---: | --- | --- | ---: | --- | --- |
| example-request | example-task-id | Review the proposal | 2026-10-31T09:00:00+08:00 | 25 | — | — | 0 | requested | raycast-link |
```

以上为示例，并非真实记录。需要分析实际时间时，先在 Raycast 核对，再在 **Open log** 中补充
`Started`、`Finished`、`Focus min`，将 `Result` 改成 `completed` 或 `partial`、`Source` 改成 `manual`。
时间必须包含时区，实际分钟数不能超过起止时间差。`completed` 计一次完成并累计实际分钟数，
`partial` 只累计分钟数；`requested`、`cancelled`、`failed` 都不计入。确认来源不能仍为 `raycast-link`。
保留 Request / Task ID；重复 ID、非法时间或时长会显示错误并暂停该日志统计，修好后自动恢复。
任务完成与 session 完成独立：勾选任务不会停止 Raycast，也不会把请求变成已完成 session。

## 安装

需要 macOS 和 Xcode Command Line Tools 提供的 Swift 编译器，无第三方依赖。

```sh
bash scripts/install_task_board.sh
```

生成 `~/.local/share/task-board/Task Board.app`，由 Hammerspoon 启动并管理。
`scripts/install_edit_anywhere.sh`（包括 dotfiles bootstrap）也会安装它。
更新 Swift 后重新运行安装脚本，再重载 Hammerspoon。

两个进程通过私有 stdin/stdout 管道交换逐行 JSON，不开放网络端口。
命令收到确认后才发送下一条，避免 `hs.task:setInput` 覆盖尚未发送的数据。
读取任务的子进程完成后会显式解除完成/流式回调，并停止、清空收尾计时器，避免
Hammerspoon registry 回调与任务对象互相引用、持续保留旧快照。重载时也会清理这些引用；
已开始的 Markdown 写入会继续完成，并以不捕获任务对象的回调持续排空输出，避免管道阻塞。

窗口移动、缩放时将位置保存到原有 Hammerspoon 设置；重载或退出 Hammerspoon
会关闭管道并退出窗口进程。WebKit 内容进程退出时会重新加载页面及最近一次快照。

## 验证

```sh
lua hammerspoon/tests/test_task_board.lua hammerspoon/task_board.lua
lua hammerspoon/tests/test_task_board_lifetime.lua hammerspoon/task_board.lua
node task-board/test_focus.cjs
python3 -m unittest -v tmux/test_focus_notes.py
python3 -m unittest -v task-board/test_window.py
```

Swift 集成测试会短暂显示测试窗口但不主动聚焦，不修改真实待办。
测试标准窗口类型、HTML 渲染、快照更新、UTF-8 分段传输、操作消息和 EOF 退出，
以及 480 / 1200px 布局、抽屉与键盘焦点、来源保全、评论草稿/IME、搜索和列滚动保持。
Lua 测试覆盖消息排队、操作分发、窗口位置记忆及重载清理。生命周期测试模拟真实 registry
引用规则，反复轮询和重载后检查已结束的任务、计时器和回调均可回收。

运行时可通过 `hs -c 'require("task_board").inspect()'` 请求诊断，稍后通过
`hs -c 'print(hs.inspect(require("task_board").status()))'` 查看窗口类型和页面卡片数量。
隐藏或最小化后的窗口可能报告 `AXDialog`；标准窗口验收须针对显示中的窗口。
