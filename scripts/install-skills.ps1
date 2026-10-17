#Requires -Version 5.1
<#
.SYNOPSIS
  从本库选择技能安装到 Agent 技能目录（默认检测已装 Agent，全部安装）。
.DESCRIPTION
  技能按层放在 skills/<层>/<技能>/：
    engineering / productivity  生产层，默认安装
    in-progress                 试验层，默认不装，需显式点名
    deprecated                  淘汰层，永不安装
  安装 = 复制技能目录；同名目录已存在时覆盖（重跑即更新）。
  默认检测本机已装 Agent（基础目录存在视为已装，规则同 link-agents.ps1），
  装到全部已装 Agent 的 skills/ 子目录；-Agent 只装指定 Agent；
  -DestDir 显式指定单个目录（优先于 Agent 检测）。
  技能自带 scripts/setup.* 时，安装完成后自动运行其自举脚本（如 inone-memory
  配置凭据/装全局命令/拉取记忆，多目录只跑一次）；-SkipSetup 跳过，可事后手动运行。
  首次使用（或新克隆的机器）：先运行 scripts/update-skills.ps1 生成 skills/ 下的技能链接，否则技能库为空。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -List
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Skill tdd,mao-methods
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Agent claude
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Layer engineering -DryRun
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -DestDir "$HOME\.claude\skills"
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -SkipSetup
#>
param(
  [string[]]$Skill,
  [string[]]$Layer,
  [string[]]$Agent,
  [switch]$All,
  [switch]$List,
  [string]$DestDir = "",
  [switch]$DryRun,
  [switch]$SkipSetup
)
$ErrorActionPreference = "Stop"
$Root      = Split-Path -Parent $PSScriptRoot
$SkillsDir = Join-Path $Root "skills"

# -File 调用时数组参数是单个逗号串（如 -Skill a,b），拆开更符合直觉
if ($Skill) { $Skill = @($Skill | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() }) }
if ($Layer) { $Layer = @($Layer | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() }) }
if ($Agent) { $Agent = @($Agent | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() }) }

$DefaultLayers = @("engineering", "productivity")
$NeverLayers   = @("deprecated")

# 各 Agent 的技能目录约定（<基础目录>/skills）；检测规则与 link-agents.ps1 一致：
# 基础目录存在视为已安装。agents 是跨 Agent 共享目录（ZCode 也扫描这里）。
$AgentDirs = [ordered]@{
  "agents"   = Join-Path $HOME ".agents"
  "claude"   = Join-Path $HOME ".claude"
  "codex"    = Join-Path $HOME ".codex"
  "qoder-cn" = Join-Path $HOME ".qoder-cn"
  "opencode" = Join-Path $HOME ".config/opencode"
  "zcode"    = Join-Path $HOME ".zcode"
}

# 解析安装目标目录：-DestDir 显式指定优先；否则按 Agent 检测（显式 -Agent 只看指定项）
$Dests = @()
if ($DestDir) {
  $Dests = @($DestDir)
} else {
  $names = if ($Agent) { @($Agent) } else { @($AgentDirs.Keys) }
  foreach ($n in $names) {
    if (-not $AgentDirs.Contains($n)) { throw "未知 Agent: $n（可选: $($AgentDirs.Keys -join ', ')）" }
    if (Test-Path $AgentDirs[$n]) {
      $Dests += (Join-Path $AgentDirs[$n] "skills")
    } elseif ($Agent) {
      Write-Warning "Agent $n 未检测到（$($AgentDirs[$n]) 不存在），跳过"
    }
  }
  if (-not $Dests) {
    if ($Agent) { throw "指定的 Agent 均未检测到，无安装目标。" }
    else { throw "未检测到任何已安装的 Agent（基础目录: $($AgentDirs.Values -join ', ')）。" }
  }
}

function Get-SkillEntries {
  $entries = @()
  foreach ($layerDir in Get-ChildItem $SkillsDir -Directory) {
    foreach ($skillDir in Get-ChildItem $layerDir.FullName -Directory) {
      $skillMd = Join-Path $skillDir.FullName "SKILL.md"
      if (-not (Test-Path $skillMd)) { continue }
      $desc = ""
      foreach ($line in Get-Content $skillMd -TotalCount 12) {
        if ($line -match '^description:\s*(.+)$') { $desc = $Matches[1].Trim().Trim('"').Trim("'"); break }
      }
      if ($desc.Length -gt 64) { $desc = $desc.Substring(0, 64) + "..." }
      $entries += [pscustomobject]@{ Layer = $layerDir.Name; Name = $skillDir.Name; Path = $skillDir.FullName; Desc = $desc }
    }
  }
  $entries | Sort-Object Layer, Name
}

$library = @(Get-SkillEntries)
$layerNames = @($library | ForEach-Object { $_.Layer } | Sort-Object -Unique)

if (-not $library) {
  Write-Host "技能库为空：skills/ 下没有任何技能。技能条目（自研 + 上游）由 update-skills.ps1 生成的 junction 提供，请先运行一次。" -ForegroundColor Red
  Write-Host "powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1" -ForegroundColor DarkGray
  exit 1
}

if ($List) {
  $width = ($library | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum
  Write-Host "目标目录:" -ForegroundColor Cyan
  foreach ($d in $Dests) { "  $d" }
  foreach ($s in $library) {
    $hit = @($Dests | Where-Object { Test-Path (Join-Path $_ $s.Name) }).Count
    $mark = if ($Dests.Count -eq 1) { if ($hit -eq 1) { "[已装]" } else { "[    ]" } } else { "[$hit/$($Dests.Count)]" }
    "  {0} {1,-14} {2,-$width}  {3}" -f $mark, $s.Layer, $s.Name, $s.Desc
  }
  return
}

$wanted = @()
if ($All) {
  $wanted = @($library | Where-Object { $NeverLayers -notcontains $_.Layer })
} elseif ($Skill -or $Layer) {
  $picked = @()
  if ($Layer) {
    foreach ($l in $Layer) {
      if ($NeverLayers -contains $l) { throw "层 $l 已淘汰，永不安装。" }
      if ($layerNames -notcontains $l) { throw "层 $l 不存在（可选: $($layerNames -join ', ')）" }
      $picked += @($library | Where-Object { $_.Layer -eq $l })
    }
  }
  if ($Skill) {
    foreach ($n in $Skill) {
      $hit = @($library | Where-Object { $_.Name -eq $n })
      if (-not $hit) { Write-Warning "库中不存在，跳过: $n"; continue }
      If ($hit | Where-Object { $NeverLayers -contains $_.Layer }) { Write-Warning "已淘汰，不安装: $n"; continue }
      $picked += $hit
    }
  }
  $wanted = $picked
} else {
  $wanted = @($library | Where-Object { $DefaultLayers -contains $_.Layer })
}

$wanted = @($wanted | Sort-Object Layer, Name -Unique | Group-Object Name | ForEach-Object { $_.Group[0] })
if (-not $wanted) { Write-Warning "没有选中任何技能。"; return }

$installed = 0
$failed = @()
$setupByName = @{}
foreach ($dest in $Dests) {
  Write-Host "→ $dest" -ForegroundColor Cyan
  foreach ($s in $wanted) {
    $target = Join-Path $dest $s.Name
    if ($DryRun) {
      Write-Host "  ~ $($s.Layer)/$($s.Name)" -ForegroundColor DarkGray
      if ((Test-Path (Join-Path $s.Path "scripts/setup.ps1")) -and -not $setupByName.ContainsKey($s.Name)) {
        Write-Host "    ~ 自举: $($s.Name)/scripts/setup.ps1 将运行" -ForegroundColor DarkGray
      }
      continue
    }
    try {
      New-Item -ItemType Directory -Force -Path $dest | Out-Null
      if (Test-Path $target) {
        $item = Get-Item $target -Force
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
          [System.IO.Directory]::Delete($target)
        } else {
          Remove-Item -Recurse -Force $target
        }
      }
      Copy-Item -Recurse -Force $s.Path $target
      Write-Host "  + $($s.Layer)/$($s.Name)" -ForegroundColor Green
      $installed++
      # 自举按技能名去重：装多份只跑一次（配置的是全局环境）
      if ((Test-Path (Join-Path $target "scripts/setup.ps1")) -and -not $setupByName.ContainsKey($s.Name)) {
        $setupByName[$s.Name] = $target
      }
    } catch {
      Write-Warning "安装失败 $dest/$($s.Name): $_"
      $failed += "$dest/$($s.Name)"
    }
  }
}

# 技能自带自举脚本:安装完成后逐个运行(如 inone-memory 配置凭据/装全局
# 命令/拉取记忆)。setup 失败只警告不中断——技能本体已装好,可事后手动重跑
if (-not $DryRun -and -not $SkipSetup -and $setupByName.Count) {
  foreach ($name in $setupByName.Keys) {
    $dir = $setupByName[$name]
    Write-Host ""
    Write-Host "运行自举: $name/scripts/setup.ps1 …" -ForegroundColor Cyan
    try {
      & (Join-Path $dir "scripts/setup.ps1")
      if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "setup 退出码 $LASTEXITCODE" }
    } catch {
      Write-Warning "自举失败($name): $_ ——技能本体已装好,可事后手动重跑 scripts/setup.ps1"
    }
  }
}

Write-Host ""
if ($DryRun) {
  Write-Host "预览：将安装 $($wanted.Count) 个技能 × $($Dests.Count) 个目标目录（-DryRun 未实际写入）" -ForegroundColor Yellow
} elseif ($failed) {
  Write-Host "完成 $installed/$($wanted.Count * $Dests.Count)，失败: $($failed -join ', ')" -ForegroundColor Yellow
  exit 1
} else {
  Write-Host "已安装 $($wanted.Count) 个技能 × $($Dests.Count) 个目标目录" -ForegroundColor Green
}
