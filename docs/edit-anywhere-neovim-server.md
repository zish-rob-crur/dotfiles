# Edit Anywhere：常驻 Neovim Server 设计

本文是 Edit Anywhere 的稳定架构契约。安装和日常使用见
`edit-anywhere/README.md`；这里不记录试验过程或已退休的实现。

## 1. 设计结论

Edit Anywhere 只使用一个专用、常驻、headless Neovim Server。Ghostty Quick
Terminal 是可复用的 remote UI，Hammerspoon 是唯一 frontend owner 和写回者。

```text
前台输入框
    │ Cmd+Shift+E
    ▼
Hammerspoon ── request/context ──> session directory
    │                                  │
    └─ FIFO ─> Quick Terminal ─> edit-anywhere-nvim
                                      │ remote-ui
                                      ▼
                            dedicated Neovim Server
                                      │ result/output
                                      ▼
Hammerspoon ── identity check ── focus/select/paste ──> 原输入框
```

设计取舍：

- 只保留 server backend，不在不确定失败后启动第二个 Neovim writer。
- Server 加载主机 AstroNvim 配置，但使用独立 state/cache/tmp/cwd。
- 首屏和输入不等待 OCR；OCR 完成后异步进入 Minuet FIM context。
- `decision.json` 和 `result.json` 是权威状态，终态文件 at-most-once 发布。
- Hammerspoon 只有在 owner、session、nonce、server generation 和正文 digest 全部匹配
  时才自动写回。

## 2. 源码与安装边界

```text
bin/
  edit-anywhere-nvim            warm RPC + remote UI
  edit-anywhere-quick-terminal  FIFO dispatcher
  edit-anywhere-server          Server supervisor
  edit-anywhere-ocr             Swift/Vision OCR source
hammerspoon/
  init.lua
  edit_anywhere.lua             frontend owner、窗口、OCR、写回
edit-anywhere/
  nvim/lua/edit_anywhere/       专属 runtime
  schema/                       protocol v1 JSON schema
  tests/                        headless、PTY、性能测试
scripts/install_edit_anywhere.sh
```

源码唯一归属于 dotfiles。安装器创建符号链接、0700 运行目录并编译 OCR helper，不写
`~/.config/nvim`。新机器与更新都调用同一入口：

```sh
scripts/install_edit_anywhere.sh --dry-run
scripts/install_edit_anywhere.sh
```

## 3. 运行时边界

默认根目录为 `~/.cache/edit-anywhere`：

```text
server/
  nvim.sock
  nvim.pid
  identity.json
  generation
sessions/<session-id>/
  input.md
  context.txt
  request.json
  decision.json
  state.json
  ui-ready.json
  result.json
  output.md
  recovery.md
  delivery.json
  attempts/
  metrics/
frontend.lock/owner.json
quick-terminal.fifo
nvim-state/
nvim-cache/
tmp/
work/
```

目录必须属于当前 uid 且权限为 0700；协议文件为普通文件、属于当前 uid、权限 0600。
Server 与 Hammerspoon 都拒绝 symlink、路径穿越、未知 JSON 字段和越界文件。

Server 身份包含 `name=edit-anywhere`、`protocol_version`、随进程变化的
`server_uuid`、单调递增的 `generation`，以及 host config 与专属 runtime 的
`config_fingerprint`。只有身份和指纹匹配的进程才允许复用或停止；活动 session
期间配置变化不会强制重启。

## 4. Protocol v1

session id 格式固定为 `YYYYMMDD-HHMMSS-XXXXXXXX`；nonce/context token 是不可猜的
22–256 字符标识。所有消息只接受 schema 中的字段。

### request

`request.json` 固定包含协议版本、session id、nonce、创建与过期时间，Markdown
编辑参数，OCR source/token/相对路径，以及原窗口 pid/window id/bundle id。

Server 从 session id 推导目录，不接收调用者提供的任意路径。request 过期、身份不符、
路径不安全或 input 非普通文件时拒绝。

### decision

一个 session 只允许一个 admission decision：

- accepted：writer 必须是身份明确的 Server，`fallback_allowed=false`；
- Server rejection：携带 Server 身份与稳定 reason；
- supervisor rejection：只允许 `DECISION_LOST`，没有 Server 身份且
  `fallback_allowed=false`。

相同 decision 重放是幂等成功；不同 decision 冲突并 fail closed。

### state、ui-ready 与 result

`state.json` 用于观测活动状态。remote UI 完成 buffer、光标、mapping、Insert mode 和
redraw 后，才发布 `ui-ready.json` 并设置窗口标题 sentinel。

`result.json` 只有：

- `committed`：必须带 `output.md` 的 SHA-256；
- `cancelled`：没有 output digest；
- `failed`：带稳定 reason。

相同 result 重放是幂等成功；第二个不同终态进入 degraded/recovery，不覆盖首个终态。

## 5. 生命周期

### 预热

Quick Terminal dispatcher 启动时执行 `edit-anywhere-server ensure`。Server 完成 host
插件加载、Minuet/DeepSeek 配置、Markdown 预热和 layout 归一化后报告 IDLE、
prewarmed、adapters_ok 和 layout_ok。

### 打开

1. Hammerspoon 读取当前 AX 输入框和原窗口身份。
2. 原子获取 frontend owner，创建私有 session 目录和 request/input。
3. 立即启动异步 OCR，然后把 session id 写入 FIFO。
4. dispatcher 记录 ack，运行稳定入口。
5. 稳定入口优先对现有 socket 发 warm RPC；失败时只调用 supervisor `admit`。
6. Server 创建隔离 `acwrite` buffer；Quick Terminal 以 `--remote-ui` 附着。
7. UI ready 后窗口定位到原窗口附近并把焦点交给编辑 buffer。

任意时刻只允许一个 frontend owner 和一个 Server active session。重复快捷键不会创建
第二份 writer；孤立 owner 会根据 terminal files、Server health 和 generation 回收。

### OCR 与补全

OCR 只截取快捷键触发时的前台窗口快照，不会截到随后出现的 Neovim。Vision 使用
`.accurate`、语言修正和 `zh-Hans`/`zh-Hant`/`en-US`。结果经过控制字符清理、凭据
redaction、行数和字符数限制后写入 `context.txt`。

Neovim 首屏不等待 OCR。context 从 pending 变为 ready 时触发一次
`ZishExternalContextChanged`；若用户仍在 Insert mode，Minuet 丢弃旧候选并刷新。
状态栏显示 pending/ready/used/empty/failed，`<Leader>oc` 可查看实际 OCR 文本。

FIM prompt 把 OCR 标为不可信引用。空草稿回答 OCR 中最后一个清晰请求；非空草稿延续
当前语言、语气和格式。模型只返回可插入正文，不输出分析、内部标签或代码围栏。

### 提交与取消

`ZZ`：

1. 序列化 buffer，写入不可变 attempt 和正文 digest；
2. 把窗口切回 keeper buffer；
3. 仅在对应 remote UI 确认 detach 后发布 output/result；
4. Hammerspoon 校验全部身份与 digest，再聚焦原窗口、全选并粘贴；
5. 写入 delivery 终态并释放 owner。

`ZQ` 或连续两次 `Ctrl-C`：同样先确认 detach，再发布 cancelled，不生成 output、不修改
原输入框。未带合法 detach intent 的 UI 离开一律进入 suspended 并保留 recovery，不会
把退出 Neovim 误判成提交。

## 6. 不变量与恢复

必须始终成立：

1. 一个 session 只有一个 decision、一个 result、一个 frontend delivery。
2. 未 accepted 的 session 不允许 resume 或附着 UI。
3. output 只由 accepted Server generation 发布。
4. Hammerspoon 只写回当前 owner 对应的原窗口。
5. 自动粘贴最多一次；不确定时只保留剪贴板。
6. 活动 session 的 Server 不因普通 `stop/restart` 被杀死。
7. 任何异常退出都保留最近 recovery shadow。

| 情况 | 行为 |
|---|---|
| Server 未启动 | supervisor 启动并 admission |
| Server 配置变化且 IDLE | 安全重启后 admission |
| Server busy | 新请求拒绝，不创建第二 backend |
| remote UI 意外退出 | suspended + recovery，可重新附着 |
| accepted session 的 Server 消失 | recovery_required，owner 可回收 |
| Hammerspoon reload | 校验 owner/decision/health 后接管或安全释放 |
| 原窗口消失或焦点校验失败 | 正文放入剪贴板，不自动粘贴 |
| paste callback/watchdog 异常 | clipboard_only 并释放 owner |
| terminal file 冲突 | degraded，保留文件供人工恢复 |

人工解除只用于 Server 已确认死亡但 owner 仍存在：

```lua
require("edit_anywhere").recoverStuckSession()
```

## 7. 管理与观测

```sh
edit-anywhere-server health
edit-anywhere-server ensure
edit-anywhere-server restart
edit-anywhere-server stop
```

`stop` 拒绝活动 session；`stop --abort-active` 会先写 recovery 再终止。

Hammerspoon 指标记录 hotkey、request、dispatcher ack、OCR、UI ready、窗口可见和
delivery。暖态目标是 request → 可输入 UI 的 p50 ≤ 60 ms、p95 ≤ 100 ms，20 次
session 内 Server PID 固定、RSS 增长 ≤ 30 MiB，且 OCR 不在首屏关键路径。

## 8. 验收

自动测试使用独立 `/tmp` cache 和 PTY，不操作用户的 Ghostty 或前台窗口：

```sh
python3 -m unittest edit-anywhere.tests.test_server

for test_file in edit-anywhere/tests/test_*.lua; do
  nvim --headless -u NONE \
    --cmd "set runtimepath^=$PWD/edit-anywhere/nvim" \
    -l "$test_file"
done

EDIT_ANYWHERE_TEST=1 lua \
  hammerspoon/tests/test_edit_anywhere_static.lua \
  hammerspoon/edit_anywhere.lua

scripts/install_edit_anywhere.sh --dry-run
python3 edit-anywhere/tests/benchmark.py attach --warmups 3 --samples 20
```

发布前还要由用户人工确认：快捷键位置、中文 OCR、context 状态、LLM 自动补全、`ZZ`
写回、`ZQ` 取消、意外退出恢复、连续触发不假死，以及不会影响其他 Ghostty 窗口。
