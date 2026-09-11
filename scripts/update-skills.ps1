#Requires -Version 5.1
<#
.SYNOPSIS
  拉取全部开源技能/插件最新版，并以目录联接(junction)引用进 skills/ 与 plugins/。
.DESCRIPTION
  - 每次运行都把 repos/ 下的镜像更新到远端最新（--depth 1）。
  - skills/<名> 是指向 repos/ 内技能目录的 junction，升级 = 重跑本脚本。
  - 断网或代理不可用时自动回退直连。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1
  powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1 -Proxy http://127.0.0.1:7890
#>
param(
  [string]$Proxy = $(if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "http://127.0.0.1:7890" }),
  [switch]$NoProxy
)
$ErrorActionPreference = "Stop"
$Root      = Split-Path -Parent $PSScriptRoot
$ReposDir  = Join-Path $Root "repos"
$SkillsDir = Join-Path $Root "skills"
$PluginsDir = Join-Path $Root "plugins"
foreach ($d in $ReposDir, $SkillsDir, $PluginsDir) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# 目标名 = 仓库内路径（相对于仓库根）
$Repos = [ordered]@{
  "mattpocock-skills" = @{
    url    = "https://github.com/mattpocock/skills.git"
    ref    = "main"
    skills = [ordered]@{
      "ask-matt"                    = "skills/engineering/ask-matt"
      "code-review"                 = "skills/engineering/code-review"
      "codebase-design"             = "skills/engineering/codebase-design"
      "diagnosing-bugs"             = "skills/engineering/diagnosing-bugs"
      "domain-modeling"             = "skills/engineering/domain-modeling"
      "grill-with-docs"             = "skills/engineering/grill-with-docs"
      "implement"                   = "skills/engineering/implement"
      "improve-codebase-architecture" = "skills/engineering/improve-codebase-architecture"
      "prototype"                   = "skills/engineering/prototype"
      "research"                    = "skills/engineering/research"
      "resolving-merge-conflicts"   = "skills/engineering/resolving-merge-conflicts"
      "setup-matt-pocock-skills"    = "skills/engineering/setup-matt-pocock-skills"
      "tdd"                         = "skills/engineering/tdd"
      "to-spec"                     = "skills/engineering/to-spec"
      "to-tickets"                  = "skills/engineering/to-tickets"
      "triage"                      = "skills/engineering/triage"
      "wayfinder"                   = "skills/engineering/wayfinder"
      "wizard"                      = "skills/engineering/wizard"
      "grill-me"                    = "skills/productivity/grill-me"
      "grilling"                    = "skills/productivity/grilling"
      "handoff"                     = "skills/productivity/handoff"
      "teach"                       = "skills/productivity/teach"
      "to-questionnaire"            = "skills/productivity/to-questionnaire"
      "wait-what"                   = "skills/productivity/wait-what"
      "writing-for-agents"          = "skills/productivity/writing-for-agents"
    }
  }
  "ponytail" = @{
    url    = "https://github.com/DietrichGebert/ponytail.git"
    ref    = "main"
    skills = [ordered]@{
      "ponytail"        = "skills/ponytail"
      "ponytail-audit"  = "skills/ponytail-audit"
      "ponytail-debt"   = "skills/ponytail-debt"
      "ponytail-gain"   = "skills/ponytail-gain"
      "ponytail-help"   = "skills/ponytail-help"
      "ponytail-review" = "skills/ponytail-review"
    }
  }
  "caveman" = @{
    url    = "https://github.com/JuliusBrussee/caveman.git"
    ref    = "main"
    skills = [ordered]@{ "caveman" = "skills/caveman" }
  }
  "ui-ux-pro-max-skill" = @{
    url    = "https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git"
    ref    = "main"
    skills = [ordered]@{ "ui-ux-pro-max" = ".claude/skills/ui-ux-pro-max" }
  }
  "planning-with-files" = @{
    url    = "https://github.com/OthmanAdi/planning-with-files.git"
    ref    = "master"
    skills = [ordered]@{ "planning-with-files" = "skills/planning-with-files" }
  }
  "claude-plugins-official" = @{
    url     = "https://github.com/anthropics/claude-plugins-official.git"
    ref     = "main"
    skills  = [ordered]@{}
    plugins = @(
      "claude-code-setup", "claude-md-management", "code-review", "commit-commands",
      "feature-dev", "frontend-design", "hookify", "ralph-loop", "typescript-lsp"
    )
  }
}

function Update-Repo([string]$name, [string]$url, [string]$ref) {
  $dest = Join-Path $ReposDir $name
  $gitArgs = @()
  if (-not $NoProxy) { $gitArgs += @("-c", "http.proxy=$Proxy", "-c", "https.proxy=$Proxy") }
  if (Test-Path (Join-Path $dest ".git")) {
    Write-Host "[$name] 更新到 $ref 最新…" -ForegroundColor Cyan
    git @gitArgs -C $dest fetch --depth 1 origin $ref
    git -C $dest reset --hard FETCH_HEAD
    git -C $dest clean -fdq
  } else {
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Write-Host "[$name] 首次克隆…" -ForegroundColor Cyan
    git @gitArgs clone --depth 1 --branch $ref $url $dest
  }
  if ($LASTEXITCODE -ne 0) { throw "[$name] git 失败（检查代理 $Proxy 或用 -NoProxy）" }
}

# 只删联接本身，不动目标内容
function Remove-Junction([string]$path) {
  $item = Get-Item $path -Force
  if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    [System.IO.Directory]::Delete($path)
  } else {
    throw "$path 已存在且不是联接，请手动处理"
  }
}

function Add-Junction([string]$link, [string]$target) {
  if (-not (Test-Path $target)) { Write-Warning "目标不存在，跳过: $target"; return }
  if (Test-Path $link) { Remove-Junction $link }
  New-Item -ItemType Junction -Path $link -Target $target | Out-Null
  Write-Host "  + $link -> $target" -ForegroundColor Green
}

$failed = @()
foreach ($kv in $Repos.GetEnumerator()) {
  $r = $kv.Value
  try {
    Update-Repo $kv.Key $r.url $r.ref
    foreach ($s in $r.skills.GetEnumerator()) {
      Add-Junction (Join-Path $SkillsDir $s.Key) (Join-Path (Join-Path $ReposDir $kv.Key) $s.Value)
    }
    if ($r.plugins) {
      foreach ($p in $r.plugins) {
        Add-Junction (Join-Path $PluginsDir $p) (Join-Path (Join-Path $ReposDir $kv.Key) "plugins/$p")
      }
    }
  } catch {
    Write-Warning $_
    $failed += $kv.Key
  }
}

Write-Host ""
if ($failed) {
  Write-Host "完成（失败: $($failed -join ', ')）。失败项沿用上次拉取的版本。" -ForegroundColor Yellow
} else {
  Write-Host "全部开源技能/插件已更新到最新。" -ForegroundColor Green
}
