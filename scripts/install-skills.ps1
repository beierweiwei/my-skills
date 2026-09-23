#Requires -Version 5.1
<#
.SYNOPSIS
  从本库选择技能安装到 Agent 技能目录（默认 ~/.agents/skills）。
.DESCRIPTION
  技能按层放在 skills/<层>/<技能>/：
    engineering / productivity  生产层，默认安装
    in-progress                 试验层，默认不装，需显式点名
    deprecated                  淘汰层，永不安装
  安装 = 复制技能目录；同名目录已存在时覆盖（重跑即更新）。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -List
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Skill tdd,mao-methods
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Layer engineering -DryRun
  powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -DestDir "$HOME\.claude\skills"
#>
param(
  [string[]]$Skill,
  [string[]]$Layer,
  [switch]$All,
  [switch]$List,
  [string]$DestDir = (Join-Path $HOME ".agents/skills"),
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Root      = Split-Path -Parent $PSScriptRoot
$SkillsDir = Join-Path $Root "skills"

# -File 调用时数组参数是单个逗号串（如 -Skill a,b），拆开更符合直觉
if ($Skill) { $Skill = @($Skill | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() }) }
if ($Layer) { $Layer = @($Layer | ForEach-Object { $_ -split ',' } | Where-Object { $_ } | ForEach-Object { $_.Trim() }) }

$DefaultLayers = @("engineering", "productivity")
$NeverLayers   = @("deprecated")

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

if ($List) {
  $width = ($library | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum
  Write-Host "目标目录: $DestDir" -ForegroundColor Cyan
  foreach ($s in $library) {
    $mark = if (Test-Path (Join-Path $DestDir $s.Name)) { "[已装]" } else { "[    ]" }
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
foreach ($s in $wanted) {
  $target = Join-Path $DestDir $s.Name
  if ($DryRun) {
    Write-Host "  ~ $($s.Layer)/$($s.Name) -> $target" -ForegroundColor DarkGray
    continue
  }
  try {
    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
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
  } catch {
    Write-Warning "安装失败 $($s.Name): $_"
    $failed += $s.Name
  }
}

Write-Host ""
if ($DryRun) {
  Write-Host "预览：将安装 $($wanted.Count) 个技能到 $DestDir（-DryRun 未实际写入）" -ForegroundColor Yellow
} elseif ($failed) {
  Write-Host "完成 $installed/$($wanted.Count)，失败: $($failed -join ', ')" -ForegroundColor Yellow
  exit 1
} else {
  Write-Host "已安装 $installed 个技能到 $DestDir" -ForegroundColor Green
}