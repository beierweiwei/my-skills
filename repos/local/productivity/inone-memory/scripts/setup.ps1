#Requires -Version 5.1
<#
.SYNOPSIS
  inone-memory 技能自举:配置凭据 + 装全局命令 inone + 拉取记忆分发。
.DESCRIPTION
  本技能由 my-skills 的 install-skills 安装到位;本脚本只做运行时接线
  (自 inone deploy/12-memory-hub/install/install.ps1 的 [3/6][4/6][5/6] 段
  蒸馏,行为同源):
    1. 配置:hub 地址 + token(已存 ~/.inone/config.json 且同服务则复用),
       setx 写 INONE_MEMORY_URL / INONE_MEMORY_TOKEN / INONE_AGENT_NAME;
    2. 全局命令:~\.inone\bin\inone.cmd(+ ps1 壳)包住本技能的
       scripts/inone_memory.py,注册用户 PATH——任何终端直呼 inone;
    3. 分发:inone login + init --global 把 MEMORY-CORE.md + topics/ 下到
       ~/inone-memory-dist,注册每日 pull --global 计划任务(schtasks)。
  铁律:永不触碰任何 agent 的全局指令文件(AGENTS.md/CLAUDE.md 由 my-skills
  link-agents 独家接入)与 auto-memory 目录;token 只进 env/交互输入,
  永不进 URL。全部幂等,重跑 = 刷新。
.PARAMETER Url
  inone 服务地址(http://<NAS_LAN_IP>:8095)。缺省 = 已存配置里的同服务地址,
  再缺省 = 交互输入。
.PARAMETER Token
  INONE_MEMORY_TOKEN。缺省 = 已存凭据复用;再缺省 = 交互输入(不回显)。
.PARAMETER AgentName
  记账归因名。缺省 = 沿用现有 INONE_AGENT_NAME;都没有则不写该变量(CLI 落 unknown)。
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 --url http://192.168.2.25:8095
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 --url http://192.168.2.25:8095 --token <t> --agent-name zcode --smoke
#>
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Usage {
  Write-Host @'
用法:powershell -ExecutionPolicy Bypass -File setup.ps1 [--url http://<NAS_LAN_IP>:8095] [选项]

选项:
  --url <地址>          inone 服务地址(缺省 = 已存配置同服务地址,再缺省交互输入)
  --token <token>       INONE_MEMORY_TOKEN(优先交互输入不回显;留空 = 沿用已存凭据
                        或服务端未设鉴权)
  --agent-name <名>     记账归因名(缺省 = 沿用现有 INONE_AGENT_NAME)
  --dist-clone <路径>   分发目录(缺省 ~\inone-memory-dist)
  --dist-remote <url>   git 兜底:HTTP 不可用且无本地文件时自动 git clone 的远端地址
  --no-schedule         不注册每日 pull --global 计划任务(手动刷新:inone pull --global)
  --smoke               装完调技能 save 推送一条冒烟记忆
  --no-setx             不写用户环境变量(setx),仅当前会话生效
  -h | --help           本说明
'@
}

function Fail([string]$Message) {
  [Console]::Error.WriteLine("错误:$Message")
  exit 1
}

# ---- 参数(手动解析,--名 值 形态,兼容 -名 别名)----
$url = $null
$token = $env:INONE_MEMORY_TOKEN
$agentName = $null
$distCloneGiven = $null
$distRemote = ""
$noSchedule = $false
$smoke = $false
$noSetx = $false
$rest = @($args)
for ($i = 0; $i -lt $rest.Count; $i++) {
  switch ($rest[$i]) {
    "--url" { $url = $rest[++$i] }
    "-url" { $url = $rest[++$i] }
    "--token" { $token = $rest[++$i] }
    "-token" { $token = $rest[++$i] }
    "--agent-name" { $agentName = $rest[++$i] }
    "-agent-name" { $agentName = $rest[++$i] }
    "--dist-clone" { $distCloneGiven = $rest[++$i] }
    "--dist-remote" { $distRemote = $rest[++$i] }
    "--no-schedule" { $noSchedule = $true }
    "--smoke" { $smoke = $true }
    "--no-setx" { $noSetx = $true }
    "-h" { Usage; exit 0 }
    "--help" { Usage; exit 0 }
    default {
      [Console]::Error.WriteLine("未知参数:$($rest[$i])")
      Usage
      exit 2
    }
  }
}
# 隐形字符清洗(0.2.5-ku 公司机实锤):复制粘贴会把 U+FEFF BOM/零宽字符
# 带进参数,Python urlopen 报 unknown url type——所有外部进来的值先过此关
function Clean-Invisible([string]$s) {
  if ($null -eq $s) { return $s }
  return ($s -replace '\uFEFF|\uFFFE|\u200B|\u200E|\u200F|\u00A0', '').Trim()
}
if ($url) { $url = (Clean-Invisible $url).TrimEnd("/") }
if ($token) { $token = Clean-Invisible $token }
if ($agentName) { $agentName = Clean-Invisible $agentName }
if ($distRemote) { $distRemote = Clean-Invisible $distRemote }
if ($distCloneGiven) { $distCloneGiven = Clean-Invisible $distCloneGiven }

# 家目录基准(INONE_INSTALL_HOME 覆盖;测试替身与多用户隔离用)
$HomeBase = if ($env:INONE_INSTALL_HOME) { $env:INONE_INSTALL_HOME } else { $HOME }
$distClone = if ($distCloneGiven) { $distCloneGiven } else { Join-Path $HomeBase "inone-memory-dist" }

# python 探测(实跑甄别——Windows 的 WindowsApps\python 是商店占位桩,
# Get-Command 在但运行必败;login/init --global 与技能本体都依赖它)
function Test-Py([string]$Exe, [string[]]$Prefix) {
  if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { return $false }
  try { & $Exe @Prefix -c 'import sys' | Out-Null } catch { return $false }
  return ($LASTEXITCODE -eq 0)
}
$py = $null
$pyPrefix = @()
if (Test-Py "python" @()) { $py = "python"; $pyPrefix = @() }
elseif (Test-Py "py" @("-3")) { $py = "py"; $pyPrefix = @("-3") }
if (-not $py) { Fail "需要 python 3(技能本体与分发初始化依赖;py/python 均不可用或为商店占位桩)" }

# CLI = 本技能自带的单文件(scripts/ 同目录);install-skills 装到哪,壳指向哪
$skillCli = Join-Path $PSScriptRoot "inone_memory.py"
if (-not (Test-Path $skillCli)) { Fail "找不到 CLI 单文件:$skillCli(本脚本须在技能的 scripts\ 内运行)" }
$distClone = [System.IO.Path]::GetFullPath($distClone)

# ---- 1) 凭据解析(三态:显式 token → 已存凭据复用 → 交互输入)----
Write-Host "[1/3] 配置凭据…"
# 已存登录凭据检测(CLI 同口径:INONE_CONFIG 覆盖,缺省 ~\.inone\config.json):
# 有且指向本次服务的地址 → 不再讨 token,分发直接复用;指向别的服务 → 不沿用
$cliConfig = if ($env:INONE_CONFIG) { $env:INONE_CONFIG } else { Join-Path $HomeBase ".inone\config.json" }
$storedCreds = $false
$storedServer = $null
if (Test-Path $cliConfig) {
  try {
    $cfg = Get-Content $cliConfig -Raw | ConvertFrom-Json
    if ($cfg.server -and $cfg.token) {
      $storedServer = ([string]$cfg.server).TrimEnd("/")
    }
  } catch { }
}
if (-not $url -and $storedServer) {
  $url = $storedServer
  Write-Host "  未传 --url,沿用已存凭据的服务地址:$url"
}
if (-not $url) {
  # 无人值守(stdin 重定向)不给交互机会,直接报错给人话指引
  if ([Console]::IsInputRedirected) {
    Fail "缺 --url 且无已存凭据(无人值守环境不交互);示例:setup.ps1 --url http://<NAS_LAN_IP>:8095"
  }
  $url = (Read-Host "inone 服务地址(如 http://192.168.2.25:8095)").Trim()
}
if ($storedServer) {
  $storedSrv = $storedServer.TrimEnd("/")
  if ($storedSrv.ToLower() -eq $url.TrimEnd("/").ToLower()) {
    $storedCreds = $true
  } else {
    Write-Host "  已存凭据指向 $storedSrv,与本次地址不一致:不沿用;"
    Write-Host "  (服务迁移场景:带新 token 重跑即覆盖)"
  }
}
if (-not $token -and -not $storedCreds) {
  if ([Console]::IsInputRedirected) {
    Write-Host "  ⚠ 无人值守且无已存凭据:按「服务端未设鉴权」继续(分发走 git 兜底)"
  } else {
    $sec = Read-Host -AsSecureString "INONE_MEMORY_TOKEN(输入不回显;直接回车 = 服务端未设鉴权,分发走 git 兜底)"
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
      $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    } finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
  }
} elseif (-not $token) {
  Write-Host "  已检测到 inone login 存的凭据($cliConfig):跳过 token 输入,分发直接复用"
}
if (-not $agentName) { $agentName = $env:INONE_AGENT_NAME }
$env:INONE_MEMORY_URL = $url
if ($agentName) { $env:INONE_AGENT_NAME = $agentName }
if ($token) { $env:INONE_MEMORY_TOKEN = $token }  # 冒烟与本次会话即时可用
if ($noSetx) {
  Write-Host "  跳过 setx(--no-setx;仅当前会话生效)"
} else {
  setx INONE_MEMORY_URL $url | Out-Null
  if ($LASTEXITCODE -ne 0) { Fail "setx INONE_MEMORY_URL 失败" }
  if ($agentName) {
    setx INONE_AGENT_NAME $agentName | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "setx INONE_AGENT_NAME 失败" }
  }
  if ($token) {
    setx INONE_MEMORY_TOKEN $token | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "setx INONE_MEMORY_TOKEN 失败" }
  }
  Write-Host "  用户环境变量已写入(setx;已开终端需重开生效)"
}

# ---- 2) 全局命令 inone(~\.inone\bin,cmd 壳 + ps1 壳两层)----
# 两层壳的原因:cmd 批处理按系统 ANSI 代码页解读,写进 UTF-8 路径(中文
# 用户名)会乱码——inone.cmd 保持纯 ASCII,路径经 %~dp0 运行时解析转发给
# 同目录 inone.ps1(UTF-8 BOM,PowerShell 读无碍,真实绝对路径嵌这里)。
# python 换位/升级后重跑本脚本即重写。
Write-Host "[2/3] 安装全局命令 inone…"
$inoneBin = Join-Path $HomeBase ".inone\bin"
New-Item -ItemType Directory -Force -Path $inoneBin | Out-Null
$pyFull = (Get-Command $py).Source
$pyPrefixLit = if ($pyPrefix.Count) { " " + ($pyPrefix -join " ") } else { "" }
[System.IO.File]::WriteAllText(
  (Join-Path $inoneBin "inone.cmd"),
  "@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File `"%~dp0inone.ps1`" %*`r`n",
  (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText(
  (Join-Path $inoneBin "inone.ps1"),
  "# inone CLI 启动壳(setup.ps1 生成;重跑即重写)`r`n" +
  "& `"$pyFull`"$pyPrefixLit `"$skillCli`" @args`r`n" +
  "exit `$LASTEXITCODE`r`n",
  (New-Object System.Text.UTF8Encoding($true)))
# 当前会话立即可用(大小写不敏感查重,Windows PATH 语义)
$binForCompare = $inoneBin.TrimEnd("\")
if (-not @($env:Path -split ";" | Where-Object { $_.Trim().TrimEnd("\") -ieq $binForCompare })) {
  $env:Path = "$env:Path;$inoneBin"
}
# 用户 PATH(HKCU)追加:原生注册表 API 直写——setx 有 1024 截断与
# REG_EXPAND_SZ 重写两重坑;读取不展开 %VAR%、写回保留原值类型,
# 大小写不敏感查重,幂等。INONE_INSTALL_REG 指向替身子键 = 测试隔离。
$regSub = if ($env:INONE_INSTALL_REG) { $env:INONE_INSTALL_REG } else { "Environment" }
$regKey = $null
try {
  $regKey = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($regSub)
  $rawPath = [string]$regKey.GetValue(
    "Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $already = @($rawPath -split ";" |
    Where-Object { $_.Trim().TrimEnd("\") -ieq $binForCompare })
  if (-not $already) {
    $kind = try { $regKey.GetValueKind("Path") }
            catch { [Microsoft.Win32.RegistryValueKind]::ExpandString }
    $newPath = if ($rawPath.Trim()) { $rawPath.TrimEnd(";") + ";" + $inoneBin } else { $inoneBin }
    $regKey.SetValue("Path", $newPath, $kind)
    if (-not $env:INONE_INSTALL_REG) {
      # 广播环境变更:之后新开的终端立即可见,无需注销重登
      if (-not ("Inone.NativeEnv" -as [type])) {
        Add-Type -Namespace Inone -Name NativeEnv -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);
'@
      }
      $result = [IntPtr]::Zero
      [Inone.NativeEnv]::SendMessageTimeout(
        [IntPtr]0xFFFF, 0x1A, [IntPtr]::Zero, "Environment", 2, 5000, [ref]$result) | Out-Null
    }
    Write-Host "  全局命令就绪:inone(新开终端即可用;本会话已可直呼 inone)"
  } else {
    Write-Host "  全局命令就绪:inone(PATH 已含 $inoneBin)"
  }
} catch {
  Write-Host "  ⚠ PATH 追加失败($_):命令文件已装在 $inoneBin,可手工把该目录加入 PATH"
} finally {
  if ($regKey) { $regKey.Close() }
}

# ---- 3) 分发初始化(HTTP 主路在 CLI,本段只接线)----
# 凭据三态:显式 token(login 落盘)→ 已存凭据(直接用)→ 都没有(git 兜底)。
# credsDurable = CLI 本机配置里有可用凭据——每日 schtasks 没有用户 env,
# 凭据全靠配置文件;login 未通过时绝不注册任务(免得每天空跑失败)。
Write-Host "[3/3] 分发初始化…"
$distHttpOk = $false
$credsDurable = $false
if ($token) {
  # a. 引导 inone login:服务地址+token 存 CLI 本机配置(~/.inone/config.json)
  #    值经 stdin 管道传入,不进命令行参数
  $loginInput = "$url`n$token"
  # PS 5.1 坑:外层 EAP=Stop 会把原生命令经 2>&1 进流的 stderr 首行变成
  # 终止性错误(NativeCommandError)——login 失败是预期分支(⚠ 后继续
  # init),局部降为 Continue,stderr 原样进 $loginOut
  $loginOut = & {
    $ErrorActionPreference = "Continue"
    $loginInput | & $py @pyPrefix $skillCli login 2>&1 | Out-String
  }
  $loginOk = $LASTEXITCODE -eq 0
  if ($loginOk) {
    Write-Host "  inone login 成功(服务地址+token 已存 CLI 本机配置,计划任务可用)"
  } else {
    Write-Host "  ⚠ inone login 未通过:$loginOut(继续尝试 init;服务端可能未开鉴权)"
  }
  # b. inone init --global:HTTP 下载 MEMORY-CORE.md + topics/ 到分发目录
  #    (--no-domain:无人值守管道不弹领域交互;逐文件 sha256 校验,失败留旧)
  & $py @pyPrefix $skillCli init --global --no-domain --dest $distClone
  if ($LASTEXITCODE -eq 0) {
    $distHttpOk = $true
    if ($loginOk) { $credsDurable = $true }
  } else {
    Write-Host "  ⚠ HTTP 分发初始化失败(服务端 /dist 未就绪或 token 无效)——转 git 兜底"
  }
} elseif ($storedCreds) {
  # 已存凭据:不重新 login,init 直接复用配置(计划任务凭据同源)
  Write-Host "  复用已存凭据(跳过 login):inone init --global 直连…"
  & $py @pyPrefix $skillCli init --global --no-domain --dest $distClone
  if ($LASTEXITCODE -eq 0) {
    $distHttpOk = $true
    $credsDurable = $true
  } else {
    Write-Host "  ⚠ HTTP 分发初始化失败(凭据可能已失效——重跑带新 token 重新 login)"
  }
} else {
  Write-Host "  未提供 token:HTTP 分发主路需要 token(溯源页「凭证管理」可签发)"
  Write-Host "  ——本次走 git 兜底,带 token 重跑本脚本即切主路"
}

if ($distHttpOk -and $credsDurable) {
  # c. 每日增量刷新计划任务:inone pull --global(CLI 自身哈希比对,变了才
  #    下载,失败留旧文件)。schtasks /F 幂等覆盖;卸载:
  #    schtasks /Delete /TN inone-dist-pull /F
  if ($noSchedule) {
    Write-Host "  跳过每日刷新注册(--no-schedule;手动刷新:inone pull --global)"
  } else {
    $taskCmd = "cmd /c `"`"$pyFull`"$pyPrefixLit `"$skillCli`" pull --global --dest `"$distClone`" >> `"$HomeBase\.inone\dist-pull.log`" 2>&1`""
    schtasks /Create /TN "inone-dist-pull" /TR $taskCmd /SC DAILY /ST 09:17 /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
      Write-Host "  每日分发刷新已注册(schtasks 09:17 inone pull --global;"
      Write-Host "  卸载:schtasks /Delete /TN inone-dist-pull /F)"
    } else {
      Write-Host "  ⚠ schtasks 注册失败(权限?);手动刷新:inone pull --global"
    }
  }
} elseif ($distHttpOk) {
  Write-Host "  ⚠ init 成功但 login 配置未落盘:暂不注册每日刷新(空跑无益);"
  Write-Host "    服务可达后带 token 重跑本脚本即补上"
} elseif (Test-Path (Join-Path $distClone ".git")) {
  Write-Host "  分发克隆就位(git 兜底形态):$distClone——日常更新 git -C 手动 pull,"
  Write-Host "  或带 token 重跑本脚本切到 HTTP 主路(每日自动刷新)"
} elseif ($distRemote) {
  Write-Host "  分发目录缺失,git 兜底自动 clone $distRemote …"
  $git = Get-Command git -ErrorAction SilentlyContinue
  if (-not $git) { Fail "git 兜底 clone 需要 git(或带 token 重跑走 HTTP 主路)" }
  git clone $distRemote $distClone
  if ($LASTEXITCODE -ne 0) { Fail "git clone 失败(可手工重试,或带 token 重跑走 HTTP 主路)" }
} else {
  Write-Host "  ⚠ 分发目录缺失:$distClone(本次未初始化)"
  Write-Host "    主路:带 token 重跑本脚本(inone init --global 经 HTTP 下载,零 git 零 SSH)"
  Write-Host "    git 兜底:重跑时加 --dist-remote <git地址> 自动 clone"
}

# ---- 冒烟(可选):推一条记忆验证端到端 ----
if ($smoke) {
  Write-Host "[冒烟] 推送验证…"
  & $py @pyPrefix $skillCli save --statement "冒烟:inone-memory setup 通道可用(可归档)" `
      --kind semantic --evidence "setup.ps1 --smoke"
  if ($LASTEXITCODE -eq 0) {
    Write-Host "  冒烟成功:溯源页 $url/ 记忆 tab 审核子视图应立即可见"
  } else {
    Write-Host "  ⚠ 冒烟推送失败(检查 --url/--token;技能与配置已就位,不影响安装)"
  }
}

Write-Host "完成:inone-memory 运行时就绪。重跑本脚本 = 刷新;全局指令(AGENTS.md)由 my-skills link-agents 接入。"
exit 0
