#Requires -Version 5.1
<#
.SYNOPSIS
  一步完成：先更新技能库，再安装到已装 Agent。
.DESCRIPTION
  依次运行 update-skills.ps1 与 install-skills.ps1。
  -Proxy / -NoProxy 传给更新步骤；其余参数原样传给安装步骤。
  更新步骤抛错时中止，不执行安装。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/update-all.ps1
  powershell -ExecutionPolicy Bypass -File scripts/update-all.ps1 -NoProxy -Agent qoder-cn
  powershell -ExecutionPolicy Bypass -File scripts/update-all.ps1 -SkipSetup
#>
param(
  [string]$Proxy = $(if ($env:HTTPS_PROXY) { $env:HTTPS_PROXY } else { "http://127.0.0.1:7890" }),
  [switch]$NoProxy,
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
$Root = Split-Path -Parent $PSScriptRoot

Write-Host "== 1/2 更新技能库 ==" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "update-skills.ps1") -Proxy $Proxy -NoProxy:$NoProxy

$installArgs = @{}
if ($Skill)   { $installArgs.Skill = $Skill }
if ($Layer)   { $installArgs.Layer = $Layer }
if ($Agent)   { $installArgs.Agent = $Agent }
if ($All)     { $installArgs.All = $true }
if ($List)    { $installArgs.List = $true }
if ($DestDir) { $installArgs.DestDir = $DestDir }
if ($DryRun)  { $installArgs.DryRun = $true }
if ($SkipSetup) { $installArgs.SkipSetup = $true }

Write-Host ""
Write-Host "== 2/2 安装到已装 Agent ==" -ForegroundColor Cyan
$global:LASTEXITCODE = 0
& (Join-Path $PSScriptRoot "install-skills.ps1") @installArgs
if ($LASTEXITCODE) { exit $LASTEXITCODE }
