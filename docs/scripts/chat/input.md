# chat 多行输入（prompt_toolkit）

> 对应代码：`scripts/chat/prompt.py`（`build_prompt_session`）。入口与渲染开关见
> [README.md](README.md)。

输入基于 prompt_toolkit `PromptSession(multiline=True)`，解决「长题目粘贴被
Enter 逐行误触发发送」的问题。

## 键位约定

`multiline=True` 下 prompt_toolkit 默认把 Enter 变成插入换行，须显式反转语义
（自定义 `KeyBindings`）：

| 操作 | 效果 |
|------|------|
| **直接粘贴多行题目** | 整段进入缓冲区，换行保留，不触发发送（bracketed paste） |
| `Enter` | 发送整段（单行短消息不受影响） |
| `Alt+Enter` / `Ctrl+J` | 手动换行（Windows 终端常吞 Alt+Enter，Ctrl+J 兜底） |
| `↑` / `↓` | 翻历史输入 |

## 历史持久化

`FileHistory` 落在 `{store.data_dir}/chat_history.txt`（`config.toml` 的
`store.data_dir`）：跨进程可 ↑↓ 翻旧输入；`data/` 已 gitignore，与业务库同
生命周期，可随 `data/` 一起清理。

## 终端限制与回退

Git Bash（mintty）伪终端下 prompt_toolkit 拿不到 Win32 控制台缓冲区，构造
`PromptSession` 即抛 `NoConsoleScreenBufferError`——app.py 捕获后**自动降级为
单行 `input()`** 并打印提示，调试入口不被终端形态阻断。

需要多行粘贴时，在 **cmd / PowerShell / Windows Terminal** 下运行入口命令。

## 相关

- REPL 主循环与回退位置：`scripts/chat/app.py`（`_repl` 输入层构造段）
- 输出侧渲染：[rendering.md](rendering.md)
