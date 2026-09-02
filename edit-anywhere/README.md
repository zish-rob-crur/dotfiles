# Edit Anywhere

在 macOS 的任意可复制输入框中按 `Cmd+Shift+E`，用 Ghostty Quick Terminal
里的主机 Neovim 配置编辑文本。`ZZ` 提交并写回原窗口，`ZQ` 取消；当前窗口的
OCR context 会在编辑界面可输入以后异步加入补全上下文，不阻塞首屏。

核心后端是一个专用、常驻、无界面的 Neovim Server。每次快捷键只创建隔离的
session buffer，再让 Quick Terminal 附着；退出编辑后 Server 保持预热。它加载
主机的 AstroNvim 配置，但状态、cache、cwd、buffer 和 OCR context 都保存在
`~/.cache/edit-anywhere`，不复用或修改日常 Neovim 实例。

## 安装

源码唯一归属于 dotfiles 仓库。安装器只创建链接、私有运行目录并编译中英文 OCR
helper，不会写入 `~/.config/nvim`：

```sh
scripts/install_edit_anywhere.sh --dry-run
scripts/install_edit_anywhere.sh
```

完整的新机器初始化也会调用同一个安装器：

```sh
scripts/bootstrap_dotfiles.sh --mode init
```

需要安装 Ghostty、Hammerspoon 和 Neovim 0.12+，并给 Hammerspoon 开启辅助功能、
屏幕录制权限。屏幕录制不可用时编辑本身仍可用，OCR 状态会显示失败。

## 首次启用或升级协议后

按这个顺序做一次：

1. 关闭并重新创建 Ghostty 的专属 Quick Terminal surface，让它运行新的 dispatcher；
2. 在 Hammerspoon 菜单中选择 **Reload Config**。

这样不会重启或最小化其他 Ghostty 窗口。此后 Quick Terminal 会在空闲时持续预热
专用 Server。

## 使用与状态

- `Cmd+Shift+E`：读取当前输入框并打开编辑界面。
- `ZZ`：原子提交；只有正文 hash、session 身份和原窗口都校验成功才自动写回。
- `ZQ`：取消，不生成输出，也不改动原输入框。
- `<Leader>oc`：在只读浮窗中查看当前 session 已完成的 OCR 文本；`q` 或 `Esc` 关闭。
- `:EditAnywhereStatus`：查看当前 session、OCR ready/used 和错误状态。
- 状态栏 OCR 图标：pending、ready、used、empty 或 failed。

OCR 仍然不阻塞首屏和输入。若第一轮补全早于 OCR 完成，context 变为 ready 后会自动
取消旧候选并刷新一次 Minuet；Edit Anywhere 的 FIM prompt 会要求模型直接生成可粘贴的
续写或回答，而不是描述 OCR 内容。若原输入框为空，则回答 OCR 中最后一个问题或请求。
Vision OCR 使用 `.accurate` 与 `zh-Hans`、`zh-Hant`、`en-US`；中文不兼容的 `.fast`
模式不用于 context 识别。识别继续在后台异步执行，因此不会阻塞编辑窗口首屏。

FIM prompt 用自然语言把 OCR 标记为不可信引用，并把实际任务放在草稿之前：空草稿只回答
OCR 中最后一个清晰问题，找不到则不生成；非空草稿优先保持原语言、语气、格式和意图。
prompt 不使用 `BEGIN`/`END` 一类会诱发模型补全的模板标记，也不配置针对旧标记的隐藏停止词。
DeepSeek 只返回可插入正文，不输出分析、复述、OCR 说明、内部标签或代码围栏。采样仅设置
`temperature=0.2`，不同时修改 `top_p`。

Minuet 为首行 prefix 和文末 suffix 构造的占位换行会在 adapter 模板中正规化：首行
草稿不带伪前导空行，光标位于真实文末时 suffix 为 `nil`。只有光标后确实存在正文时
才把它作为 FIM suffix，避免把回答强制挤成单行或产生截断式续写。

同一时间只允许一个 session。Hammerspoon reload 或原窗口丢失后，已提交正文只恢复
到剪贴板，不会猜测性地重复粘贴。

写回原输入框的 focus、select 和 paste acknowledgement timer 都由 session 强引用并在
结束时统一清理。进入 `paste_intent` 后另有 2 秒 watchdog；若回调、焦点或按键发送异常，
流程会降级为 `clipboard_only`、释放 frontend owner，并保留结果到剪贴板，绝不重复粘贴。

## Server 管理

```sh
edit-anywhere-server health
edit-anywhere-server ensure
edit-anywhere-server restart
edit-anywhere-server stop
```

`stop` 会拒绝结束活动 session；只有明确需要放弃恢复数据时才使用
`edit-anywhere-server stop --abort-active`。

## 自动验证

以下测试使用独立的 `/tmp` cache 和 PTY；不会打开 Ghostty、触发快捷键、切换窗口
或粘贴到任何应用：

```sh
python3 edit-anywhere/tests/test_server.py
python3 edit-anywhere/tests/benchmark.py attach --warmups 3 --samples 20
```

Lua 单元测试：

```sh
for test_file in edit-anywhere/tests/test_*.lua; do
  nvim --headless -u NONE \
    --cmd "set runtimepath^=$PWD/edit-anywhere/nvim" \
    -l "$test_file"
done
```

Hammerspoon 的 owner/reload 安全检查：

```sh
EDIT_ANYWHERE_TEST=1 lua \
  hammerspoon/tests/test_edit_anywhere_static.lua \
  hammerspoon/edit_anywhere.lua
```

暖态 benchmark 的门槛是 request 到可输入 UI 的 p50 不高于 60 ms、p95 不高于
100 ms，20 次期间 Server PID 固定且 RSS 增长不超过 30 MiB。真实
`快捷键 -> Neovim 可输入` 指标由 Hammerspoon 自己记录；GUI 测试由用户亲自触发。
可先启动纯被动采集器，再由用户完成 3 次预热和 20 次实测：

```sh
python3 edit-anywhere/tests/benchmark.py e2e-start --warmups 3 --samples 20
```

采集器只读取新 session 的指标，不会触发快捷键、聚焦窗口或操作 Ghostty。

## 文件与数据边界

- `edit-anywhere/nvim/`：专属 Server runtime。
- `hammerspoon/`：快捷键、窗口、OCR 和安全写回前端。
- `bin/edit-anywhere-*`：Server、dispatcher、remote UI 和 OCR helper。
- `edit-anywhere/schema/`：versioned request/decision/result schema。
- `edit-anywhere/tests/`：无 GUI 的功能与性能测试。
- `~/.cache/edit-anywhere/sessions/`：session、recovery 和分阶段指标。

协议、安全边界、故障恢复与验收矩阵见
[`docs/edit-anywhere-neovim-server.md`](../docs/edit-anywhere-neovim-server.md)。
