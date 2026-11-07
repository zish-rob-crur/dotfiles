---
target: Task Board 整体不和谐，重新思考设计
total_score: 22
max_score: 40
na_heuristics:
p0_count: 0
p1_count: 3
target_identity: "file:/Users/zhiwen.wang/GitHubRepos/dotfiles/hammerspoon/task_board.lua"
target_fingerprint: "sha256:45b018dce3580d0ee9d4b01d9a6b62c3658ad8f3dd1243d0fc82817f97b8a915"
target_path: /Users/zhiwen.wang/GitHubRepos/dotfiles/hammerspoon/task_board.lua
timestamp: 2026-11-04T03-08-39Z
slug: hammerspoon-task-board-lua
---
Method: dual-agent (A: /root/task_board_design_review · B: /root/task_board_design_evidence)

# Task Board：任务阅读优先的整体整理

依据用户提供的当前截图和 hammerspoon/task_board.lua 源码。这是静态评审，不是新一轮运行时可用性测试。

## 整体判断

功能逐项叠加后缺少统一的信息层级。Markdown 单一任务源、Focus 置顶视图、项目纵列和搜索值得保留。主要机会是把任务正文作为默认阅读内容，把证据、历史和管理动作分层展示。保持现有暗色主题，不依赖更换配色解决结构问题。

## 启发式健康检查

以下为静态初步判断，4 表示较好、0 表示明显缺失。交互恢复、键盘全流程仍需要实际验证，不能将分数当作可用性测试。

| 启发式 | 分数 | 依据 |
|---|---:|---|
| 系统状态可见 | 2 | 统计充分但重复，部分反馈远离动作 |
| 符合用户认知 | 3 | Weekly、Focus、项目模型清楚；stale/zz 含义偏技术 |
| 用户控制与自由 | 2 | 能重开任务，未见即时撤销 |
| 一致性与标准 | 2 | 共用任务行，但颜色和操作层级混杂 |
| 错误预防 | 2 | 有 pending；整段标题关联 checkbox 有误触风险 |
| 识别优于记忆 | 3 | 标签明确，时长选择与部分 Start 动作相距较远 |
| 灵活与高效 | 2 | 搜索有效，重复内容与嵌套滚动拖慢扫描 |
| 简洁与美感 | 1 | 正文、证据、元信息、动作同层竞争 |
| 错误识别与恢复 | 2 | 源码有错误与草稿保留；本轮未实测 |
| 帮助与说明 | 3 | 有说明，但常驻帮助占用主区域 |
| 合计 | 22/40 | 主要问题集中于阅读效率和层级 |

## 优先问题

1. P1：来源链接淹没正文。截图中 Focus 同名来源连续出现；源码没有来源分组或折叠。默认显示 Sources N，详情中逐条展示，完整保留不同 URL。截图不能证明链接本身重复，不能据此删数据。
2. P1：顶部层级过多。全局统计与 Tmux 统计重复、Focus 使用完整卡片、新增入口重复，周任务被推到下面。合并标题/搜索/新增工具栏；Focus 使用紧凑置顶行；Tmux 移至辅助区域且默认状态沿用用户设置。
3. P1：每行固定四按钮，来源/历史/评论展开又改变列高。默认只保留完成框、正文、必要状态和详情入口；高频 Start 在 Focus 中明确展示，完整操作随任务详情出现。不能仅依赖 hover，键盘聚焦和点击也须可达。
4. P2：项目色染满正文，又有侧边色条。正文统一中性色，项目色限于组标题，风险色保留给逾期等实际状态。既有配色身份可保留。
5. P2：横向项目滚动、各列纵向滚动、页面滚动叠加。保留用户要求的项目纵列，列宽按可读性确定；固定主体视口，让滚动归属清楚，减少页面与列的同时纵向滚动。窄窗口不强塞所有分组。

## 方案

Task Board / Week / Search / Add task 为一条全局工具栏；Focus 是紧凑的多任务置顶区；项目纵列为主体；任务默认呈现标题、必要日期、Sources 和 Comments 数量；Tmux 收进次级区域。

例：

    Task Board · W45       [Search tasks…]       [+ Add task]
    Focus · 3    Task title… / CALLE             [Start Focus]
    CALLE · 19           Botlab · 9           Quality · 2
    □ Task title         □ Task title         □ Task title
      Sources 3            Due Nov 6
      Comments 2           Sources 2
    ▸ Tmux · 21 windows · Needs you 2

关键待定：任务详情采用右侧按需抽屉，还是卡片内展开。抽屉集中来源、评论、专注记录与操作；内联更接近现有习惯，但仍改变列表高度。第二个权衡是列宽与同屏项目数量，优先保证中文正文可读，保留横向访问其他组。

## 用户风险与优势

高频用户需要先穿过大量重复链接与操作才能读完任务；键盘用户不能靠 hover 找到动作；阅读时点击标题可能完成任务，后续应把详情入口与完成框明确分开。优点是数据模型统一、任务 ID 稳定、搜索覆盖评论、纵列顺序稳定。

## 自动检测

CLI 退出码 2，9 条 warning，全为 side-tab：112 行 Tmux 状态边界，209–212 行任务分类边界。它们是既有状态/项目编码，不能等同于 9 个独立可用性问题。检测器没有发现/衡量正文来源密度、顶部重复层级及多重滚动；这些来自截图和源码评审。

## 删减审查

首轮不增加复杂筛选器、标签体系或任务状态。优先整理已有内容和操作，保留 Markdown、Raycast、评论和分组需求。
