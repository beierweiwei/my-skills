#Requires -Version 5.1
<#
.SYNOPSIS
  把本库的全局指令（AGENTS.md / CLAUDE.md）链接到各 Agent 的全局位置，所有 Agent 共用同一份源文件。
.DESCRIPTION
  - 覆盖：agents(通用约定) / Claude Code / Codex / Qoder(国内版) / opencode / ZCode 的全局指令路径。
  - 目录不存在视为该 Agent 未安装，自动跳过；已有实体文件先备份为 *.bak.<时间戳> 再链接。
  - Windows 创建符号链接需要开发者模式（设置 → 系统 → 开发者选项）或管理员权限。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/link-agents.ps1
  powershell -ExecutionPolicy Bypass -File scripts/link-agents.ps1 -DryRun
#>
param([switch]$DryRun)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

# 名称、链接路径、仓库内源文件（AGENTS.md 为共用源，CLAUDE.md 是指向它的指针）
$Targets = @(
  @{ name = "agents";   link = (Join-Path $HOME ".agents/AGENTS.md");          source = (Join-Path $Root "AGENTS.md") }
  @{ name = "claude";   link = (Join-Path $HOME ".claude/CLAUDE.md");          source = (Join-Path $Root "CLAUDE.md") }
  @{ name = "codex";    link = (Join-Path $HOME ".codex/AGENTS.md");           source = (Join-Path $Root "AGENTS.md") }
  @{ name = "qoder-cn"; link = (Join-Path $HOME ".qoder-cn/AGENTS.md");       source = (Join-Path $Root "AGENTS.md") }
  @{ name = "opencode"; link = (Join-Path $HOME ".config/opencode/AGENTS.md"); source = (Join-Path $Root "AGENTS.md") }
  @{ name = "zcode";    link = (Join-Path $HOME ".zcode/AGENTS.md");           source = (Join-Path $Root "AGENTS.md") }
)

$failed = @()

function New-SymLink([string]$Path, [string]$Target) {
  try {
    New-Item -ItemType SymbolicLink -Path $Path -Target $Target -ErrorAction Stop | Out-Null
  } catch { }
  if (Test-Path $Path) { return }
  # PS 5.1 的 New-Item 不带 SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE(0x800),
  # 开发者模式开着也报"需要管理员"——P/Invoke 直调补上旗标
  if (-not ("MySkills.SymLink" -as [type])) {
    Add-Type -Namespace MySkills -Name SymLink -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true, CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
public static extern bool CreateSymbolicLink(string lpSymlinkFileName, string lpTargetFileName, int dwFlags);
'@
  }
  $flags = 0x800  # SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE(文件链接,0x0)
  [void][MySkills.SymLink]::CreateSymbolicLink($Path, $Target, $flags)
  # Windows 怪相实锤(2026-10-12 本机):无特权时 API 竟返回 true 但不落任何
  # 文件(lasterr=1314 ERROR_PRIVILEGE_NOT_HELD)——返回值不可信,以落盘为准
  if (-not (Test-Path $Path)) {
    throw "符号链接未落盘(无特权且开发者模式旗标未生效)"
  }
}
foreach ($t in $Targets) {
  $dir = Split-Path -Parent $t.link
  if (-not (Test-Path $dir)) {
    Write-Host ("  - {0,-10} 未安装（{1} 不存在），跳过" -f $t.name, $dir)
    continue
  }
  if (-not (Test-Path $t.source)) {
    Write-Warning "仓库源文件缺失: $($t.source)"
    $failed += $t.name
    continue
  }

  $item = Get-Item $t.link -Force -ErrorAction SilentlyContinue
  $isLink = $item -and ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)

  if ($isLink -and $item.Target -eq $t.source) {
    Write-Host ("  = {0,-10} 已是链接" -f $t.name)
    continue
  }
  if ($DryRun) {
    if ($isLink)        { Write-Host ("  ~ {0,-10} 将重建链接: {1}" -f $t.name, $t.link) }
    elseif ($item)      { Write-Host ("  ~ {0,-10} 将备份实体文件并链接: {1}" -f $t.name, $t.link) }
    else                { Write-Host ("  ~ {0,-10} 将创建链接: {1} -> {2}" -f $t.name, $t.link, $t.source) }
    continue
  }

  if ($isLink) {
    [System.IO.File]::Delete($t.link)
  } elseif ($item) {
    $bak = "$($t.link).bak." + (Get-Date -Format "yyyyMMddHHmmss")
    Move-Item $t.link $bak
    Write-Host ("  ! {0,-10} 原文件已备份: {1}" -f $t.name, $bak)
  }

  try {
    New-SymLink -Path $t.link -Target $t.source
    Write-Host ("  + {0,-10} {1} -> {2}" -f $t.name, $t.link, $t.source) -ForegroundColor Green
  } catch {
    # 无符号链接权限(未开发者模式/非管理员,Windows 实锤):降级为副本接入,
    # 绝不让原位悬空——先试符号链接失败再落副本;副本也失败则把备份还原
    try {
      Copy-Item $t.source $t.link -Force
      Write-Host ("  + {0,-10} 副本接入(无符号链接权限): {1}" -f $t.name, $t.link) -ForegroundColor Green
      Write-Host "    改仓库后需重跑本脚本同步;以管理员身份重跑可升级为真链接(改仓库即全局生效)"
    } catch {
      Write-Warning "$($t.name) 副本接入也失败: $_"
      if ($item -and -not $isLink -and (Test-Path "$bak")) {
        Move-Item $bak $t.link -Force
        Write-Host "    已还原原文件: $t.link"
      }
      $failed += $t.name
    }
  }
}

Write-Host ""
if ($failed) {
  Write-Host "完成，失败: $($failed -join ', ')" -ForegroundColor Yellow
} else {
  Write-Host "全局指令已接入。改仓库文件即对所有 Agent 生效。" -ForegroundColor Green
}
