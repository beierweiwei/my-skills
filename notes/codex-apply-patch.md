# Codex apply_patch 在本机的正确调用

- 本机 `apply_patch` 是 `.bat` 包装，实际执行 `codex.exe --codex-run-as-apply-patch <patch>`；补丁作为命令行参数传入，不读 stdin。
- PowerShell 把多行补丁文本传给 `.bat` 时，换行会被 `cmd.exe` 截断，报 `Invalid patch: The last line of the patch must be '*** End Patch'`。
- 正确做法：直接调用背后的 `codex.exe`（路径示例：`C:\Users\Administrator\AppData\Local\nvm\v25.9.0\node_modules\@openai\codex\node_modules\@openai\codex-win32-x64\vendor\x86_64-pc-windows-msvc\bin\codex.exe`）并传 `--codex-run-as-apply-patch $patch`，由 PowerShell 直连 exe 保留换行。
- 补丁格式：第一行必须是 `*** Begin Patch`，最后一行必须是 `*** End Patch`，中间为 `*** Update File: <path>` 与 `@@`/diff 块。
