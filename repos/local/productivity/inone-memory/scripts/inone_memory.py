#!/usr/bin/env python3
"""inone 统一记忆技能脚本(「保存记忆」/「检索记忆」/效用回报/登录/自更新
/项目初始化)。

调用 inone 溯源页同服务的记忆 REST API(8095,工单 12 主动通道;ku-09
起数据/读接口全部 POST——GET 一律 405,参数走 JSON body,凭据只进
Bearer 头永不进 URL):
    save    POST /memory/push            → 直接入库**即时生效**(写后审,
                                            ADR 0012:不当记忆在溯源页审核
                                            工作台随手删除)
    search  POST /memory/entries         → 查询**已生效**记忆(缺省
                                            status=active;带 --q 时统一召回)
    report  POST /memory/utility         → 任务收尾效用回报(工单 F):对本
                                            会话取用过的记忆报三态 + 一句话
                                            理由;可选 --supersedes 附取代建议
                                            (过时修正流)
    login   交互输入服务地址 + 粘贴 token → 存本机配置文件(工单 A)
    update  POST /client/latest          → 拉最新版自替换(ku-09 起 POST +
                                            Accept: application/json;工单 A:
                                            全程 HTTP+token,手机零 git 零 SSH)
    init    POST /memory/init-bundle     → 项目初始化(工单 C):交互多选领域 →
                                            拉取写项目内 .inone-memory/(按领域
                                            分文件,头部只读铁律)+ AGENTS.md 幂等
                                            追加引用段(标记判重,跑两次不重复)
    pull    POST /memory/init-bundle     → 增量刷新:按 init 的口径重拉覆盖
                                            .inone-memory/(AGENTS.md 不重复追加)

全局分发通道(ku-07:客户端零 git 零 SSH 的主路,替 git clone;ku-09 起
POST 化——manifest 空体,file 体带 path;ADR 0020 起体可带 domains 领域
口径——选了领域,主面四文件只含所选领域内容):
    init    --global  POST /dist/manifest + /dist/file → 首次拉取(口径:
                      显式 --domain/交互选择优先,否则存档口径;all=清口径
                      回全量):MEMORY-CORE.md + topics/<kind>.md 四文件经
                      HTTP 下载到 ~/inone-memory-dist(纯文件夹,无 .git),
                      逐文件 sha256 校验,写 .inone-dist-meta.json 版本元数据;
                      服务端该类型清零时本地残留同步摘除;领域深度面:交互/
                      --domain 选领域 → /memory/init-bundle 按领域分文件落
                      domains/(口径跨主面与领域面同一次选择)
    pull    --global  POST /dist/manifest → 每日增量:逐文件哈希比对,变了才
                      下载;任何失败保留旧文件 + 非零退出(下次重试);凭据
                      复用 login(安装脚本注册的每日计划任务调的就是它);
                      主面与领域面均按元数据存档口径一并增量

凭据解析顺序(环境变量优先,login 配置兜底):
    1. 环境变量 INONE_MEMORY_URL / INONE_MEMORY_TOKEN(不入 git,可注入)
    2. login 写的平台配置文件(缺省 ~/.inone/config.json,INONE_CONFIG 可
       覆盖路径)——token 来自溯源页「凭证管理」签发的库内 token(吊销
       即时生效)或运维主 token

零第三方依赖(仅 Python 标准库)。四家 agent 的技能目录各放一份同源拷贝
(deploy/12-memory-hub/skills/_shared 为源,修改后同步四份)。

用法:
    python inone_memory.py login
    python inone_memory.py save --statement "示例文档必须带可运行代码" \
        --kind semantic --evidence "用户说:记住……"
    python inone_memory.py search --q "示例" --kind semantic
    python inone_memory.py report --id 12 --outcome helped \
        --reason "按这条记忆避开了坑,一次通过"
    python inone_memory.py init            # 项目根目录内运行
    python inone_memory.py pull
    python inone_memory.py init --global   # 全局分发首次拉取(零 git)
    python inone_memory.py pull --global   # 每日增量刷新(计划任务调它)
    python inone_memory.py update
"""

import argparse
import getpass
import hashlib
import json
import os
import re
import stat
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# 三类型(ADR 0010,认知科学/LangMem 三分法;旧五类型词表已废止)
KINDS = ("semantic", "episodic", "procedural")

# 项目内快照目录与 AGENTS.md 引用段幂等标记(工单 C):标记与
# /memory/init-bundle 应答的引用段头部逐字节一致——服务端单点产出
# (core.project_init.AGENTS_MD_MARKER),两端一致性由仓库测试把关。
MEMORY_DIR = ".inone-memory"
MANIFEST = "manifest.json"
AGENTS_MD = "AGENTS.md"
AGENTS_MD_MARKER = "<!-- inone-memory:project-ref -->"

# 全局分发通道(ku-07):固定四文件(与服务端 dist_hub.DIST_PATHS 同源
# 契约,两端一致性由仓库测试把关);缺省目录与 git 兜底克隆同位,全局
# 指令文件(AGENTS.md/CLAUDE.md)的引用路径不变。
DIST_DIR = "inone-memory-dist"
DIST_META = ".inone-dist-meta.json"
DIST_PATHS = (
    "MEMORY-CORE.md",
    "topics/semantic.md",
    "topics/procedural.md",
    "topics/episodic.md",
)

# 全局领域追加面子目录:四文件主面(CORE+topics)恒全量不动,所选领域
# 的深度文件经 /memory/init-bundle(项目 init 同款服务端单点)落
# domains/;领域口径存 .inone-dist-meta.json 供 pull --global 增量。
DOMAINS_DIR = "domains"


# ---- 凭据解析(环境变量 > login 配置文件)----


def _config_path() -> Path:
    """本机配置文件路径(INONE_CONFIG 可覆盖;缺省 ~/.inone/config.json)。"""
    override = os.environ.get("INONE_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".inone" / "config.json"


def _load_config() -> dict:
    """读配置(坏文件/缺文件 = 空 dict,不炸命令)。"""
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_config(server: str, token: str) -> Path:
    """合并写入(保留未知键,向前兼容后续版本新增字段)。

    写后收紧权限到 0o600(仅属主读写):token 落盘不该全世界可读——
    POSIX chmod 生效;Windows 无 POSIX 权限位(chmod 只映射只读位,
    0o600 含写位等于不动),跳过,依赖用户目录的 ACL。"""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = _load_config()
    merged["server"] = server
    merged["token"] = token
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if os.name == "posix":
        os.chmod(path, 0o600)
    return path


_INVISIBLE_CHARS = ("\ufeff", "\ufffe", "\u200b", "\u200e", "\u200f", "\u00a0")

# 出站 UA 必须显式声明:urllib 缺省签名 Python-urllib/3.x 在 Cloudflare
# Browser Integrity Check 拦截名单上——公网隧道 202024.xyz 实锤 403
# "error code: 1010"(2026-10-10 外网安装报障;curl 下发能过、CLI login
# 被拦即此因)。三处出站(_request/_probe/_dist_request)全部携带。
USER_AGENT = "inone-cli/0.3"


def _sanitize(value) -> str:
    """清掉复制粘贴常见的隐形字符(U+FEFF BOM/零宽/方向标记/NBSP)。

    Windows 复制链路(网页/聊天窗 → PowerShell/终端 → env/交互输入)实测
    会把 BOM 带进 URL 与 token(0.2.5-ku 公司机首装实锤:urlopen 报
    unknown url type);所有外部进来的 URL/token 一律过此关。"""
    cleaned = str(value)
    for char in _INVISIBLE_CHARS:
        cleaned = cleaned.replace(char, "")
    return cleaned.strip()


def _base_url() -> str:
    """服务地址:INONE_MEMORY_URL 优先,login 配置兜底(去尾斜杠)。"""
    env = _sanitize(os.environ.get("INONE_MEMORY_URL") or "").rstrip("/")
    if env:
        return env
    return _sanitize(_load_config().get("server") or "").rstrip("/")


def _token() -> str:
    """Bearer 凭据:INONE_MEMORY_TOKEN 优先,login 配置兜底。"""
    env = _sanitize(os.environ.get("INONE_MEMORY_TOKEN") or "")
    if env:
        return env
    return _sanitize(_load_config().get("token") or "")


def _request(
    method: str,
    path: str,
    body: dict | None = None,
    query: str = "",
    *,
    accept_json: bool = False,
    timeout: float = 15,
) -> dict:
    base = _base_url()
    if not base:
        sys.exit(
            "错误:未配置服务地址——先运行 login"
            "(或设 INONE_MEMORY_URL,如 http://<NAS_LAN_IP>:8095)"
        )
    token = _token()
    url = f"{base}{path}{query}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", USER_AGENT)
    if body is not None:
        request.add_header("Content-Type", "application/json; charset=utf-8")
    if accept_json:
        # ku-09(D7):update 走 POST /client/latest 的 JSON 形态(服务端
        # 按协商回 JSON;缺省形态是浏览器附件下载)——version = 服务端
        # 内容哈希,截断/错版在写盘前被 _apply_update 拒
        request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400]
        if error.code == 401:
            # 人话引导(工单 03):save/search 撞 401 时 agent 要知道
            # 下一步是续 token,不是看一坨原始 JSON(_dist_request 同款)
            sys.exit(
                "HTTP 401:token 无效或未配置——先运行 login 粘贴 token"
                "(溯源页「凭证管理」签发,可随时吊销),"
                "或设 INONE_MEMORY_TOKEN 环境变量"
            )
        sys.exit(f"HTTP {error.code}:{detail}")
    except TimeoutError:
        # 超时≠失败:慢调用(save 同步跑混合推理提取,常态 25s+)在服务端
        # 可能已完成——先 search 验证再决定重推,盲重推会重复入库
        sys.exit(
            f"请求超时(>{timeout:.0f}s):服务端可能已完成——先 search 查证"
            "是否已入库,确认没进再重推,否则会推重"
        )
    except urllib.error.URLError as error:
        sys.exit(f"服务不可达:{url}({error})——Tailscale/内网是否连通?")


# ---- login(工单 A:粘贴 token 存本机)----


def _secret(prompt: str) -> str:
    """读敏感输入:终端用 getpass(不回显);管道/非终端退化为普通
    input——Windows 管道下 getpass 会卡在 msvcrt 控制台读,脚本化/
    agent 管道场景必须可用(退化时 token 会回显,接受:个人终端)。"""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    return input(prompt)


def _probe(base: str, token: str) -> "str | None":
    """登录校验:POST /memory/entries(连通性 + 凭据;ku-09 起读面 POST 化,
    空体 + status=active 即最小探针);None = 通过。"""
    request = urllib.request.Request(
        f"{base}/memory/entries",
        data=json.dumps({"status": "active"}, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
        return None
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:200]
        return f"HTTP {error.code}:{detail}"
    except urllib.error.URLError as error:
        return f"服务不可达:{base}({error})"
    except (ValueError, OSError) as error:  # 坏 URL 形态等
        return f"请求失败:{error}"


def _login(args: argparse.Namespace, *, prompt=input, secret=None,
           probe=_probe) -> int:
    """交互登录:问服务地址 → 粘贴 token → 校验 → 存本机配置文件。

    prompt/secret/probe 为注入缝(测试替身);secret 缺省 _secret
    (终端 getpass 不回显,管道退化普通输入)。"""
    if secret is None:
        secret = _secret
    print("inone 登录:token 从溯源页「凭证管理」生成(库内 token,可随时吊销)。")
    print("环境变量 INONE_MEMORY_URL / INONE_MEMORY_TOKEN 仍优先于本配置。")
    # 默认链与 _base_url 同序(env 优先,config 兜底):提示里展示的默认
    # 必须等于实际会用的地址——装机脚本 setx/profile 写的 INONE_MEMORY_URL
    # 在这里生效,装完机回车即真默认(公司机实锤:示例地址冒充默认,
    # 回车交白卷直接退出,报错一闪而过)
    current = _load_config()
    env_server = _sanitize(os.environ.get("INONE_MEMORY_URL") or "").rstrip("/")
    default_server = env_server or _sanitize(current.get("server") or "").rstrip("/")
    try:
        if default_server:
            server = _sanitize(prompt(f"服务地址[{default_server}](回车用默认):")).rstrip("/")
            if not server:
                server = default_server
        else:
            # 两头都没有:方括号里放示例会诱导"回车用默认"——明说必填
            server = _sanitize(prompt("服务地址(必填,如 http://192.168.1.10:8095):")).rstrip("/")
    except (EOFError, KeyboardInterrupt):
        print("错误:输入中断(窗口被关闭或 Ctrl+Z/C):未登录,重跑 login 再试", file=sys.stderr)
        return 2
    if not server:
        print(
            "错误:未输入服务地址(须完整地址,如 http://192.168.2.25:8095;"
            "先跑安装脚本或设 INONE_MEMORY_URL 后,这里回车即有默认)",
            file=sys.stderr,
        )
        return 2
    try:
        token = _sanitize(secret("token(粘贴,输入不回显):"))
    except (EOFError, KeyboardInterrupt):
        print("错误:token 输入中断(窗口被关闭或 Ctrl+Z/C):未登录,重跑 login 再试", file=sys.stderr)
        return 2
    if not token:
        print("错误:未输入 token", file=sys.stderr)
        return 2
    error = probe(server, token)
    if error:
        print(f"错误:登录校验失败:{error}", file=sys.stderr)
        return 2
    path = _save_config(server, token)
    print(f"已登录并保存到 {path}")
    print("save/search/update 现在直接可用(无需环境变量)。")
    return 0


def _owns_console() -> bool:
    """Windows:本进程是否独占当前控制台(独占 = 窗口随本进程退出而关闭,
    双击/新窗启动形态)。经典判据 GetConsoleProcessList:附着进程数 ≤1
    即只有自己;用户终端里 shell+自己 ≥2,计划任务的 cmd 包装也 ≥2,
    CI/服务无控制台返 0——这三种都判 False,不暂停。非 Windows 一律
    False(终端不随进程退出消失)。"""
    if os.name != "nt":
        return False
    try:
        import ctypes

        count = ctypes.windll.kernel32.GetConsoleProcessList(
            (ctypes.c_uint * 1)(), 1
        )
        return count == 1
    except Exception:  # 探测失败一律"不暂停"——暂停只是体验补丁,绝不能炸主流程
        return False


def _pause_if_window_owned() -> None:
    """出错/登录结束时,若窗口即将随本进程关闭,等一个回车再退——错误
    信息不再一闪而过(公司机实锤:报错即关窗,现场全丢)。"""
    if not _owns_console():
        return
    try:
        input("\n按回车键退出…")
    except EOFError:
        pass


# ---- update(工单 A:自更新)----


def _apply_update(target: Path, payload: dict) -> str:
    """校验下发内容并自替换;返回结果说明(纯本地,不网络访问,可离线单测)。

    基本校验(防把错误页/坏载荷写进磁盘):
    - 必须含 version 与 content,且 content 的 SHA-256 必须等于 version
      (内容寻址完整性:截断/错版载荷在写盘前被拒);
    - content 必须含主入口标记 ``def main(`` 且可编译(合法 Python 源码)
      ——JSON 错误体(如 ``{"error": ...}``)虽是合法表达式,过不了标记检查。
    自替换:先写同目录临时文件再 ``os.replace``(同卷原子换名,Windows 上
    目标存在也允许替换;正在运行的旧实例不受影响)。写盘与换名任一步
    失败都清理临时文件,不留 ``.new`` 残留。版本比较用文件字节
    的 SHA-256(内容寻址:服务端 version 即下发内容的 SHA-256)。"""
    content = str(payload.get("content") or "")
    version = str(payload.get("version") or "")
    if not content or not version:
        raise ValueError("下发载荷缺少 version/content")
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != version:
        raise ValueError("下发内容的 SHA-256 与 version 不符(载荷可能被截断或错版),拒绝写盘")
    if "def main(" not in content:
        raise ValueError("下发内容缺少主入口标记 def main((可能不是 inone 客户端脚本)")
    try:
        compile(content, "inone_memory.py", "exec")
    except SyntaxError as error:
        raise ValueError(f"下发内容不是合法 Python 源码:{error}") from error
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == version:
            return f"已是最新版本({version[:12]}…),无需更新"
    tmp = target.with_name(target.name + ".new")
    try:
        tmp.write_text(content, encoding="utf-8", newline="")
        if target.exists():  # 保住可执行位(Windows 无此位,忽略失败)
            try:
                os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))
            except OSError:
                pass
        os.replace(tmp, target)
    except OSError:
        tmp.unlink(missing_ok=True)  # 半失败清理:不留 .new 残留
        raise
    return f"已自替换 {target}(新版本 {version[:12]}…)"


def _update(args: argparse.Namespace, *, path=None, fetch=None) -> int:
    """自更新:带 token 拉 /client/latest,校验后自替换本文件。

    path/fetch 为注入缝(测试替身);缺省目标是本脚本自身(服务地址/
    token 未配置时 _request 会以明确指引退出)。ku-09(D3/D7)起走
    POST + ``Accept: application/json``(服务端同端点的浏览器形态仍是
    附件下载);老版脚本走 GET 的 compat shim 服务端保留至下一版,
    存量客户端一次 update 即自愈到本 POST 版。"""
    target = Path(path) if path is not None else Path(__file__).resolve()
    fetch = fetch or (lambda: _request("POST", "/client/latest", body={}, accept_json=True))
    payload = fetch()
    try:
        message = _apply_update(target, payload)
    except (OSError, ValueError) as error:
        print(f"错误:更新失败:{error}", file=sys.stderr)
        return 2
    print(message)
    print("提示:新版本下次调用时生效(当前进程仍运行旧代码)。")
    _check_skill_md_freshness()
    return 0


# ---- init --global / pull --global(ku-07:全局分发,零 git 零 SSH)----

# agent 配置主目录标记(auto-memory 只读铁律的客户端面;与服务端
# adapters/agents_md.AGENT_CONFIG_HOME_MARKERS 同源):目标目录落在这些
# 主目录树内 = 拒写,即使 --dest 显式指过去。
_AGENT_CONFIG_MARKS = (".claude", ".zcode", ".qoder", ".codex", ".agents")


class DistError(Exception):
    """全局分发失败(信息已说人话;调用方打印 + 非零退出,旧文件保留)。"""


def _dist_request(path: str, body: "dict | None" = None) -> bytes:
    """POST 分发端点(ku-09 D3 改 POST:manifest 空体;file 体带 path;
    Bearer 头,token 永不进 URL);故障抛 DistError。

    与 _request 分立而非复用:分发命令要把失败转成「留旧文件 + 非零
    退出」的可捕获异常(计划任务每天跑,错误信息是唯一现场),而
    _request 直接 sys.exit;401/404 在此翻成带指引的人话。"""
    base = _base_url()
    if not base:
        raise DistError(
            "未配置服务地址——先运行 login"
            "(或设 INONE_MEMORY_URL,如 http://<NAS_LAN_IP>:8095)"
        )
    url = f"{base}{path}"
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    token = _token()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:200]
        if error.code == 401:
            raise DistError(
                "token 无效或未配置(HTTP 401)——先运行 login 粘贴 token"
                "(溯源页「凭证管理」签发,可随时吊销),"
                "或设 INONE_MEMORY_TOKEN 环境变量;本地旧文件未动"
            ) from error
        if error.code == 404:
            raise DistError(
                f"服务端没有可下发的分发内容(HTTP 404:{detail or path})"
                "——请确认服务端已跑 inone memory dist 生成 MEMORY-CORE.md,"
                "且 serve 配置了 INONE_AGENTS_MD_ALLOWED_ROOTS"
            ) from error
        raise DistError(
            f"HTTP {error.code}:{detail or '(无正文)'};本地旧文件未动,"
            "稍后重试(计划任务每天会自动再拉)"
        ) from error
    except urllib.error.URLError as error:
        raise DistError(
            f"服务不可达:{url}({error.reason})——内网/Tailscale 是否连通?"
            "本地旧文件未动,网络恢复后重试"
        ) from error


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refuse_agent_config_dest(dest: Path) -> None:
    """auto-memory 铁律(客户端面):目标目录落在 agent 配置主目录树内
    一律拒写(与服务端分发器同向,纵深防御)。比对前 normcase 归一
    (Windows 盘符/目录大小写不敏感,防大小写变体绕过)。"""
    resolved = dest.expanduser().resolve()
    home = Path.home().resolve()
    for mark in _AGENT_CONFIG_MARKS:
        guarded = home / mark
        resolved_nc = os.path.normcase(str(resolved))
        guarded_nc = os.path.normcase(str(guarded))
        if resolved_nc == guarded_nc or guarded_nc in (
            os.path.normcase(str(parent)) for parent in resolved.parents
        ):
            raise DistError(
                f"目标目录落在 agent 配置主目录树内(auto-memory 只读"
                f"铁律),拒绝写入:{resolved}"
            )


def _write_dist_meta(dest: Path, manifest: dict) -> None:
    """版本元数据(.inone-dist-meta.json):manifest 版本时间戳 + 逐文件
    哈希快照 + 拉取时刻——pull 的「变了才下载」按逐文件比对,元数据
    供人看/排障(何时拉的、哪个服务端)。"""
    payload = {
        "version": manifest.get("version"),
        "server": _base_url(),
        "files": {
            str(entry.get("path")): entry.get("sha256")
            for entry in manifest.get("files", [])
            if isinstance(entry, dict)
        },
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    # 领域口径跨四文件同步存活:_sync_dist 每次重写元数据,若不回带,
    # pull --global 的领域增量会在主面同步后断流(口径被抹)
    stored_domains = _read_dist_meta(dest).get("domains")
    if stored_domains:
        payload["domains"] = stored_domains
    (dest / DIST_META).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_dist_meta(dest: Path) -> dict:
    """读 .inone-dist-meta.json;缺失/损坏返回 {}(领域口径视为无,
    pull --global 静默跳过领域面——老安装零感知)。"""
    try:
        payload = json.loads((dest / DIST_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _upsert_dist_meta_domains(dest: Path, domains: "list[str]") -> None:
    """领域口径并入元数据(保留 _sync_dist 写下的版本面字段)。"""
    meta = _read_dist_meta(dest)
    if domains:
        meta["domains"] = list(domains)
    else:
        meta.pop("domains", None)
    (dest / DIST_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _sync_dist(
    dest: Path, *, force: bool, domains: "list[str] | None" = None
) -> int:
    """全局分发同步核心(init --global force / pull --global 增量共用)。

    领域口径(ADR 0020):``domains`` 非空时请求体携带(逗号串)——服务端
    按口径从记忆库生成四文件(选了领域,主面只含所选领域内容);None/空
    = 全量静态 checkout(无口径的既有语义,老服务端零感知)。口径下某
    类型无条目时 manifest 记 sha256=null → 本地残留摘除,契约不变。

    全部下载到内存并逐文件 sha256 校验通过后才动盘——任何失败保留
    旧文件(计划任务的下次重试即自愈);服务端清零的类型摘除本地
    残留(清单即权威状态)。"""
    body = {"domains": ",".join(domains)} if domains else {}
    raw = _dist_request("/dist/manifest", body=body)
    try:
        manifest = json.loads(raw.decode("utf-8"))
        entries = [e for e in (manifest.get("files") or []) if isinstance(e, dict)]
    except (ValueError, AttributeError) as error:
        raise DistError(
            f"manifest 应答不是预期的 JSON({error})——服务端版本可能过旧"
            "(需要带 /dist 端点的版本);本地文件未动"
        ) from error
    by_path: dict[str, dict] = {}
    for entry in entries:
        relative = str(entry.get("path") or "")
        if relative not in DIST_PATHS:  # 固定四名外拒收(服务端单点契约)
            print(f"警告:忽略清单外路径:{relative!r}", file=sys.stderr)
            continue
        by_path[relative] = entry
    downloads: dict[str, str] = {}
    removes: list[str] = []
    for relative in DIST_PATHS:
        entry = by_path.get(relative)
        target = dest / Path(relative)
        if entry is None or not entry.get("sha256"):
            if target.exists():  # 服务端该类型清零 → 摘除本地残留
                removes.append(relative)
            continue
        wanted = str(entry["sha256"])
        if (
            not force
            and target.is_file()
            and _sha256_file(target) == wanted
        ):
            continue  # 哈希一致,零下载(计划任务的日常路径)
        downloads[relative] = wanted
    if not downloads and not removes:
        _write_dist_meta(dest, manifest)
        suffix = f"(领域口径:{','.join(domains)})" if domains else ""
        print(f"已是最新(版本 {manifest.get('version') or '未知'}),无需下载{suffix}")
        return 0
    blobs: dict[str, bytes] = {}
    for relative, wanted in sorted(downloads.items()):
        data = _dist_request("/dist/file", body={**body, "path": relative})
        actual = hashlib.sha256(data).hexdigest()
        if actual != wanted:
            raise DistError(
                f"{relative} 下载内容校验失败(manifest={wanted[:12]}… "
                f"实际={actual[:12]}…,可能被截断或错版),本地文件未动"
            )
        blobs[relative] = data
    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for relative, data in sorted(blobs.items()):
        target = dest / Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, target)  # 同卷原子换名,半失败不留 .part 残留
        except OSError as error:
            tmp.unlink(missing_ok=True)
            raise DistError(
                f"写盘失败:{target}({error});此前已完成的文件保留,"
                "重跑本命令即续"
            ) from error
        written.append(relative)
    for relative in removes:
        (dest / Path(relative)).unlink()
    _write_dist_meta(dest, manifest)
    for relative in written:
        print(f"已下载 {relative}({len(blobs[relative])} 字节)")
    for relative in removes:
        print(f"已摘除(服务端已清零):{relative}")
    suffix = f";领域口径:{','.join(domains)}" if domains else ""
    print(
        f"分发目录已同步:{dest}(版本 {manifest.get('version') or '未知'};"
        f"下载 {len(written)} 个,摘除 {len(removes)} 个{suffix})"
    )
    return 0


def _resolve_global_domain_tokens(
    args: argparse.Namespace, *, force: bool, prompt=input
) -> "list[str] | None":
    """init --global 的领域口径解析(主面过滤与领域面**共用同一次结果**,
    ADR 0020):--no-domain 优先(不碰领域维度,显式 --domain 一并忽略,
    返回 []);--domain 显式次之;交互环境多选(取消/未选 = [],领域面
    不动);非交互 = [](提示后跳过)。pull(force=False)恒 None——由
    调用方按存档口径解析。"""
    if not force:
        return None
    explicit = _parse_domain_args(getattr(args, "domain", None) or [])
    if getattr(args, "no_domain", False):
        # --no-domain = 不碰领域维度:显式 --domain 一并忽略(同给属操作
        # 矛盾,主面回退存档口径,不产生「主面 X/meta 旧口径」的漂移)
        if explicit:
            print("提示:--no-domain 与 --domain 同给,已忽略 --domain(主面按已存口径)")
        return []
    if explicit:
        return explicit
    tokens: list[str] = []
    if sys.stdin.isatty():
        names = _fetch_domains()
        if names:
            tokens = _choose_domains(names, prompt=prompt) or []
            if not tokens:
                print(
                    "未选择领域:domains/ 不变(主面按已存口径;重跑"
                    " init --global 可再选,或 --domain 显式指定)"
                )
    else:
        print(
            "提示:非交互环境跳过领域面——如需按领域深度文件,重跑"
            " init --global --domain <领域>[,…]"
        )
    return tokens


def _sync_dist_domains(
    dest: Path,
    args: argparse.Namespace,
    *,
    force: bool,
    prompt=input,
    tokens: "list[str] | None" = None,
) -> int:
    """全局领域追加面(项目 init 同款服务端单点 /memory/init-bundle)。

    口径解析:调用方已预解析时经 ``tokens`` 传入(主面过滤与领域面共用
    同一次选择,ADR 0020);None 时自行解析——init --global 交互选
    (取消/非交互 = 跳过),pull --global 按元数据存档口径增量。
    束即权威状态:束外旧 domains/*.md 摘除;束空而目录已有 .md 视为
    异常载荷拒动(与项目面 _apply_bundle 同款防御)。"""
    if force and getattr(args, "no_domain", False):
        # --no-domain:安装脚本/无人值守显式跳过领域面(交互选择器绝不在
        # 脚本管道里弹出);pull 不受影响,仍按存档口径增量
        return 0
    if tokens is None:
        if force:  # init --global:交互选;取消 = 不动 domains/(不误删)
            tokens = _resolve_global_domain_tokens(args, force=True, prompt=prompt) or []
        else:  # pull --global:存档口径;无口径(老安装/未选过)静默跳过
            tokens = [
                str(item).strip()
                for item in _read_dist_meta(dest).get("domains") or []
                if str(item).strip()
            ]
    if not tokens:
        return 0
    bundle = _request("POST", "/memory/init-bundle", body={"domain": ",".join(tokens)})
    target_dir = dest / DOMAINS_DIR
    wanted: dict[str, str] = {}
    for item in bundle.get("files", []):
        if not isinstance(item, dict):
            print(f"警告:忽略非对象文件项:{item!r}", file=sys.stderr)
            continue
        relative = str(item.get("path") or "")
        # 只收 .inone-memory/<单段名>:全局模式不请求 project 文件,
        # 服务端领域文件即单段名(路径前缀契约与项目面同源)
        if (
            not relative.startswith(MEMORY_DIR + "/")
            or ".." in relative
            or "/" in relative[len(MEMORY_DIR) + 1:]
        ):
            print(f"警告:忽略束外/异常路径:{relative!r}", file=sys.stderr)
            continue
        name = relative[len(MEMORY_DIR) + 1:]
        wanted[f"{DOMAINS_DIR}/{name}"] = str(item.get("text") or "")
    if not wanted and any(target_dir.glob("*.md")):
        print(
            f"错误:拉取束无可落盘文件,但 {DOMAINS_DIR}/ 已有 .md——"
            "疑似异常载荷,拒绝摘除,目录未动(排查服务端版本/应答后重试)",
            file=sys.stderr,
        )
        return 2
    if not wanted:
        _upsert_dist_meta_domains(dest, tokens)
        print(f"领域面:束内无可落盘文件({DOMAINS_DIR}/ 未动)")
        return 0
    target_dir.mkdir(parents=True, exist_ok=True)
    for relative, text in sorted(wanted.items()):
        (dest / relative).write_text(text, encoding="utf-8", newline="\n")
    removed = [
        path.name
        for path in sorted(target_dir.glob("*.md"))
        if f"{DOMAINS_DIR}/{path.name}" not in wanted
    ]
    for name in removed:
        (target_dir / name).unlink()
    _upsert_dist_meta_domains(dest, tokens)
    print(
        f"领域面已同步:{DOMAINS_DIR}/({int(bundle.get('count') or 0)} 条;"
        f"写入 {len(wanted)} 个,移除 {len(removed)} 个;领域 {'、'.join(tokens)})"
    )
    return 0


def _clear_domain_caliber(target: Path, *, force: bool) -> int:
    """--domain all(ADR 0020):清领域口径——主面回全量、domains/ 摘除、
    存档口径删除。主面同步成功后才动口径面(失败留旧,重跑自愈)。"""
    code = _sync_dist(target, force=force)
    if code != 0:
        return code
    removed = 0
    target_dir = target / DOMAINS_DIR
    if target_dir.is_dir():
        for path in sorted(target_dir.glob("*.md")):
            path.unlink()
            removed += 1
    meta = _read_dist_meta(target)
    meta.pop("domains", None)
    (target / DIST_META).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"领域口径已清:主面回全量;{DOMAINS_DIR}/ 摘除 {removed} 个文件")
    return 0


def _cmd_dist_global(args: argparse.Namespace, *, force: bool) -> int:
    """init --global / pull --global 共用入口:解析目标目录 + 铁律护栏
    + 同步 + 人话错误(非零退出;意外 I/O 故障也翻成可读信息,计划
    任务每天跑,错误输出是唯一现场——绝不裸抛 traceback)。

    领域口径(ADR 0020):主面四文件按口径过滤——本次显式/交互选择
    优先,否则续用存档口径(pull 即此路);--domain all 清口径回全量;
    --no-domain 只跳领域面交互,不抹已存口径(重装不破坏选好的面)。"""
    target = Path(
        getattr(args, "dest", None) or Path.home() / DIST_DIR
    ).expanduser()
    try:
        _refuse_agent_config_dest(target)
        explicit = _parse_domain_args(getattr(args, "domain", None) or [])
        if "all" in {token.lower() for token in explicit}:
            return _clear_domain_caliber(target, force=force)
        chosen = _resolve_global_domain_tokens(args, force=force)
        stored = [
            str(item).strip()
            for item in _read_dist_meta(target).get("domains") or []
            if str(item).strip()
        ]
        caliber = chosen or stored
        code = _sync_dist(target, force=force, domains=caliber or None)
        if code != 0:
            return code
        return _sync_dist_domains(target, args, force=force, tokens=chosen)
    except DistError as error:
        print(f"错误:{error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(
            f"错误:本地 I/O 失败:{target}({error});已完成的文件保留,"
            "重跑本命令即续",
            file=sys.stderr,
        )
        return 2


# ---- init / pull(工单 C:项目初始化与增量刷新)----


def _slug_from(root: Path) -> str:
    """从项目目录名推导 slug:小写,[^a-z0-9_-]+ 折叠为 '-',去首尾 '-'。"""
    return re.sub(r"[^a-z0-9_-]+", "-", root.name.lower()).strip("-")


def _parse_domain_args(values: list[str]) -> list[str]:
    """--domain 参数归并:逐项再按逗号拆分、strip、保序去重。"""
    tokens: list[str] = []
    for value in values:
        for part in str(value).split(","):
            part = part.strip()
            if part and part not in tokens:
                tokens.append(part)
    return tokens


def _fetch_domains() -> list[str]:
    """读服务端领域词表(POST /memory/domains/list,工单 B;ku-09 起清单
    读面 POST 化且与 add 端点分路)——交互选择的选项源。"""
    payload = _request("POST", "/memory/domains/list", body={})
    return [
        str(item.get("name") or "")
        for item in payload.get("domains", [])
        if isinstance(item, dict) and item.get("name")
    ]


def _choose_domains(names: list[str], *, prompt=input) -> "list[str] | None":
    """交互多选领域:编号逗号分隔(a=全部;0=未分类 none);空输入/非法
    输入返回 None(取消)。返回逐项 token 列表(可含 none)。"""
    print("选择要拉取的领域(可多选;主面四文件与领域深度面一并按所选口径过滤):")
    print("  0. 未分类(none)——自动打标之外的未归类条目")
    for index, name in enumerate(names, 1):
        print(f"  {index}. {name}")
    print("示例:1,3(第 1、3 项)/ 0,2(未分类 + 第 2 项)/ a(全部);回车取消。")
    raw = str(prompt("领域编号:")).strip().lower()
    if not raw:
        return None
    if raw in ("a", "all", "全部"):
        return list(names)
    picked: list[str] = []
    for part in raw.replace(" ", "").split(","):
        if not part:
            continue
        if part == "0":
            token = "none"
        elif part.isdigit() and 1 <= int(part) <= len(names):
            token = names[int(part) - 1]
        else:
            print(f"错误:无效编号 {part!r}(1-{len(names)},0=未分类)", file=sys.stderr)
            return None
        if token not in picked:
            picked.append(token)
    return picked or None


def _apply_bundle(root: Path, bundle: dict) -> "dict | None":
    """拉取束落盘:写 .inone-memory/ 全部文件 + 摘除束外旧 .md(重拉覆盖,
    manifest 非 .md 不动);返回统计(写入/移除/条数),异常载荷拒动时
    返回 None(调用方非零退出)。只接受束内 MEMORY_DIR 前缀且不含 ..
    的路径(服务端单点权威,防御异常载荷)。

    防御加深(审查修复 P2):束内无一可落盘文件(files 缺失/全空/全
    拒收)而目录里已有 .md 时,按异常载荷报错拒动而非摘除——摘除语义
    只服务服务端的合法收窄;正常应答每个口径至少产一个文件(哪怕 0
    条),空束几乎必是坏载荷/错版,静默清空目录不可接受。"""
    target_dir = root / MEMORY_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    wanted: dict[str, str] = {}
    for item in bundle.get("files", []):
        if not isinstance(item, dict):
            print(f"警告:忽略非对象文件项:{item!r}", file=sys.stderr)
            continue
        relative = str(item.get("path") or "")
        if not relative.startswith(MEMORY_DIR + "/") or ".." in relative:
            print(f"警告:忽略束外/异常路径:{relative!r}", file=sys.stderr)
            continue
        wanted[relative] = str(item.get("text") or "")
    if not wanted and any(target_dir.glob("*.md")):
        print(
            f"错误:拉取束无可落盘文件,但 {MEMORY_DIR}/ 已有 .md——"
            "疑似异常载荷,拒绝摘除,目录未动(排查服务端版本/应答后重试)",
            file=sys.stderr,
        )
        return None
    for relative, text in wanted.items():
        (root / relative).write_text(text, encoding="utf-8", newline="\n")
    removed = [
        relative
        for relative in (
            path.relative_to(root).as_posix()
            for path in sorted(target_dir.glob("*.md"))
        )
        if relative not in wanted
    ]
    for relative in removed:
        (root / relative).unlink()
    return {
        "written": sorted(wanted),
        "removed": removed,
        "count": int(bundle.get("count") or 0),
    }


def _ensure_agents_md(root: Path, section: str) -> str:
    """AGENTS.md 幂等追加引用段:标记已在则跳过(skipped),否则追加
    (added,补空行分隔);文件不存在则创建。

    换行风格保持(审查修复 P3-2):写前按原始字节探测,既有文件含
    CRLF 则整文件按 CRLF 写回——不把追加段变成整文件 CRLF→LF 的
    大重排(非注入区逐字节不动)。"""
    path = root / AGENTS_MD
    raw = path.read_bytes() if path.exists() else b""
    newline = "\r\n" if b"\r\n" in raw else "\n"
    existing = raw.decode("utf-8").replace("\r\n", "\n")
    if AGENTS_MD_MARKER in existing:  # 标记不含换行,CRLF 原文同样命中
        return "skipped"
    body = existing
    if body:
        if not body.endswith("\n"):
            body += "\n"
        body += "\n"
    path.write_text(body + section, encoding="utf-8", newline=newline)
    return "added"


def _cmd_init(args: argparse.Namespace, *, prompt=input, root=None) -> int:
    """项目初始化:选领域(--domain 或交互)→ 拉取 → 落盘 .inone-memory/
    + AGENTS.md 引用段;口径存 manifest 供 pull 复用。

    --global(ku-07)改走全局分发:HTTP 拉四文件到 ~/inone-memory-dist
    (项目级语义不变,两模式互斥)。"""
    if getattr(args, "global_mode", False):
        return _cmd_dist_global(args, force=True)
    root = Path(root) if root is not None else Path.cwd()
    project = (args.project or "").strip() or _slug_from(root)
    if not project:
        print("错误:无法从目录名推导项目 slug,请用 --project 显式指定", file=sys.stderr)
        return 2
    tokens = _parse_domain_args(args.domain)
    if not tokens:
        if not sys.stdin.isatty():
            print(
                "错误:非交互环境需显式 --domain(可多次;none=未分类)",
                file=sys.stderr,
            )
            return 2
        names = _fetch_domains()
        if not names:
            print("错误:服务端领域词表为空(先在溯源页系统设置或 CLI 维护词表)",
                  file=sys.stderr)
            return 2
        tokens = _choose_domains(names, prompt=prompt) or []
        if not tokens:
            print("未选择任何领域,已取消(可重跑 init 或用 --domain 显式指定)",
                  file=sys.stderr)
            return 2
    bundle = _request(
        "POST",
        "/memory/init-bundle",
        body={"domain": ",".join(tokens), "project": project},
    )
    summary = _apply_bundle(root, bundle)
    if summary is None:  # 异常载荷拒动(审查修复 P2):不写 manifest
        return 2
    (root / MEMORY_DIR / MANIFEST).write_text(
        json.dumps({"domain": ",".join(tokens), "project": project},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    agents = _ensure_agents_md(root, str(bundle.get("agents_md_section") or ""))
    print(
        f"已初始化 {MEMORY_DIR}/({summary['count']} 条;"
        f"文件 {'、'.join(summary['written']) or '(无)'})"
    )
    for relative in summary["removed"]:
        print(f"已移除束外旧文件:{relative}")
    print(f"AGENTS.md 引用段:{'已追加' if agents == 'added' else '已存在,跳过(幂等)'}")
    print(f"增量刷新用:python {Path(sys.argv[0]).name} pull")
    return 0


def _cmd_pull(args: argparse.Namespace, *, root=None) -> int:
    """增量刷新:按 init 存档口径重拉覆盖 .inone-memory/;AGENTS.md 只在
    标记缺失时补(正常不重复追加)。

    --global(ku-07)改走全局分发增量:manifest 哈希比对,变了才下载,
    失败保留旧文件(项目级语义不变,两模式互斥)。"""
    if getattr(args, "global_mode", False):
        return _cmd_dist_global(args, force=False)
    root = Path(root) if root is not None else Path.cwd()
    manifest_path = root / MEMORY_DIR / MANIFEST
    if not manifest_path.exists():
        print(f"错误:未找到 {MEMORY_DIR}/{MANIFEST}——先在本项目运行 init",
              file=sys.stderr)
        return 2
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest 顶层不是对象")
    except ValueError as error:
        print(f"错误:{manifest_path} 损坏({error});删除后重跑 init", file=sys.stderr)
        return 2
    params = {}
    if str(manifest.get("domain") or "").strip():
        params["domain"] = str(manifest["domain"]).strip()
    if str(manifest.get("project") or "").strip():
        params["project"] = str(manifest["project"]).strip()
    bundle = _request("POST", "/memory/init-bundle", body=params)
    summary = _apply_bundle(root, bundle)
    if summary is None:  # 异常载荷拒动(审查修复 P2):目录原样保留
        return 2
    agents = _ensure_agents_md(root, str(bundle.get("agents_md_section") or ""))
    print(
        f"已刷新 {MEMORY_DIR}/({summary['count']} 条;"
        f"文件 {'、'.join(summary['written']) or '(无)'})"
    )
    for relative in summary["removed"]:
        print(f"已移除束外旧文件:{relative}")
    if agents == "added":
        print("AGENTS.md 引用段:缺失已补(标记不在,防手删后失联)")
    return 0


# 与本版脚本配套的 SKILL.md 内容哈希(memory-skill-qa 工单 07 方案 a):
# update 自检同技能目录的 SKILL.md 是否与本版配套——漂移(旧版/手改)提示
# 重跑安装脚本,不自动改写(说明书的安装归属安装脚本)。集合 = 六份变体
# (五家 agent + _shared 母本)各自当前内容;改任一 SKILL.md 必同步这里,
# 否则装机侧 update 会误报「不配套」。
_SKILL_MD_SHA256 = frozenset((
    "d7b8d4d2d77bc05c9a266a3bf9b7a90b45da16d0f3bf2fea6de642a045ee8b5a",
    "6decc5a7867d1f7dca99d3449af44abfe300dccdd9e5360fff69ab17e928d061",
    "2d01b76b61dda122b2235259a0c7016f309167aae3d2527c24790daf4eaa3a0d",
    "a0fcb2d2e8256638ca9373b2ec2f769c2e5e8df5b0e315c33fc47e133237be4f",
    "72fbf72c13e9112c4c84481b2a2693ba651ca7c420a9692340dca684e61ff965",
    "3e09994eaea1b58ae1fdb934fada500d878ccad9a1af97c7064abd93e607e636",
))


def _check_skill_md_freshness() -> None:
    """update 收尾自检(工单 07):SKILL.md 过期只提示,永不自动覆盖。"""
    skill_md = Path(__file__).resolve().parent.parent / "SKILL.md"
    try:
        digest = hashlib.sha256(skill_md.read_bytes()).hexdigest()
    except OSError:
        return  # 装的是裸脚本(无说明书)不打扰——合法形态
    if digest not in _SKILL_MD_SHA256:
        print(
            "提示:SKILL.md 与本版脚本不配套(旧版或被改动)——重跑安装脚本"
            "刷新,或从仓库 deploy/12-memory-hub/skills/<agent>/ 同步对应变体"
            "(update 只换脚本,不换说明书)",
            file=sys.stderr,
        )


# ---- 保存 / 检索 / 效用回报 ----

# 幂等读自动重试的等待秒数(工单 02):服务端降级多为 rerank 云 API 的
# TPM 限流,窗口分钟级滚动,等 5 秒重发一次大概率恢复;测试替身置 0。
_SEARCH_RETRY_DELAY_S = 5.0


def _hint_unknown_agent(agent: str) -> None:
    """记账身份提示(工单 03):agent 名缺省落 "unknown" 时吱一声——
    来源地图按身份分组,全挂 unknown 事后没法归因。不阻塞、不改行为。"""
    if agent == "unknown":
        print(
            '提示:未设 agent 名,取用/推送记账将落在 "unknown"'
            "(export INONE_AGENT_NAME=<agent 名> 可消除)",
            file=sys.stderr,
        )


def _knowledge_snippet(origin_path: str, text: str) -> str:
    """知识紧凑行片段(工单 03,纯展示层):剥与行首重复的路径 H1、
    YAML frontmatter 与前导标题/空行,让片段从首个有信息量的行起。

    紧凑行行首已打 origin_path,chunk 原文再以 `# <同路径>` 开头就是
    同一信息打两遍;frontmatter(title/type/tags…)则会把真正有用的
    TLDR 挤出 200 字窗口。--json 的 text 字段原样不动,只有紧凑行走这。"""
    lines = str(text or "").splitlines()
    normalized = str(origin_path or "").strip().strip("/").lower()
    if normalized:
        # 前导路径 H1(# wiki/entities/X.md 与行首 origin_path 同值)
        while lines:
            first = lines[0].strip()
            if first.startswith("#") and first.lstrip("#").strip().strip("/").lower() == normalized:
                lines = lines[1:]
                continue
            break
    # YAML frontmatter(--- 开头且有闭合 ---;未闭合按原文,不吞内容;
    # 头上的空行不拦着 frontmatter 判定)
    while lines and not lines[0].strip():
        lines = lines[1:]
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                lines = lines[index + 1:]
                break
    # 剩余前导标题/空行(正文 H1 多为文件名同义标题,信息量为零)
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines = lines[1:]
    return " ".join(" ".join(lines).split())


def _save(args: argparse.Namespace) -> None:
    _hint_unknown_agent(args.agent)
    payload = {
        "statement": args.statement,
        "kind": args.kind,
        "agent": args.agent,
        "evidence": args.evidence or "agent 会话中的用户显式语句",
    }
    if args.scope != "user":
        payload["scope"] = args.scope
    if args.tag:
        payload["tags"] = args.tag
    if args.ttl_days is not None:
        payload["ttl_days"] = args.ttl_days
    if args.negative:
        payload["negative"] = True
    if args.provenance:
        payload["provenance"] = args.provenance
    # 写后审推送同步跑外流提取(混合推理模型常态 25s+),15s 缺省必假超时
    entry = _request("POST", "/memory/push", body=payload, timeout=120)
    print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    print(
        f"已直接入库即时生效 entry_id={entry.get('entry_id')}"
        "(进检索与下次分发;不当记忆可在溯源页审核工作台删除)。",
        file=sys.stderr,
    )


def _search(args: argparse.Namespace) -> None:
    _hint_unknown_agent(args.agent)
    params = {"status": args.status}
    if args.kind:
        params["kind"] = args.kind
    if args.scope:
        params["scope"] = args.scope
    if args.q:
        params["q"] = args.q
        # 带关键词 = 统一召回(记忆 + 知识 wiki/notes 混排;服务端按底座
        # 分排序 + 砍尾)——agent 查内容型问题靠知识通道,只查记忆会
        # 淹死在不相干画像句里(2026-10-09 生产实证)
        params["unified"] = "1"
    # 消费方身份自报(工单 F 取用记账):缺省用 agent 名,来源地图按它分组
    params["consumer"] = args.consumer or args.agent
    if args.task_context:
        params["task_context"] = args.task_context
    if args.limit is not None:
        params["limit"] = args.limit
    if args.offset is not None:
        params["offset"] = args.offset
    # ku-09(D3):读面 POST 化,参数迁 JSON body(凭据只进 Bearer 头)。
    # 幂等读自动重试一次(工单 02):服务端降级(degraded=true,多为
    # rerank 云 API TPM 限流,分钟级窗口)或网络闪断时,等几秒重发一次
    # 拿真结果;仍降级则带警告输出。HTTP 4xx/5xx 是服务端明确表态,
    # 不重试;save/report/init/pull/update 永不自动重试(服务端不保证
    # 推送幂等,重试即双推——本重试只限 search 这一条幂等读路径)。
    result: dict = {}
    degraded_seen = False
    for attempt in (1, 2):
        try:
            result = _request("POST", "/memory/entries", body=params)
        except SystemExit as exit_error:
            if attempt == 1 and str(exit_error).startswith("服务不可达"):
                time.sleep(_SEARCH_RETRY_DELAY_S)
                continue
            raise
        if attempt == 1 and result.get("degraded"):
            degraded_seen = True
            time.sleep(_SEARCH_RETRY_DELAY_S)
            continue
        break
    degraded = bool(result.get("degraded"))
    knowledge_error = str(result.get("knowledge_error") or "").strip()
    if degraded or knowledge_error:
        # 知识路故障独立于记忆路降级(knowledge_error 可单独存在)——
        # 任一路出问题都要吱声,否则知识面缺席会被当成「没有收藏」
        reason = knowledge_error[:80]
        print(
            "⚠ 本次检索降级兜底(原因:"
            + (
                reason
                or f"检索后端不可达(via={result.get('via') or 'ledger'}),走账本字面过滤"
            )
            + "),结果可能严重不全——请重试或换措辞,"
            "勿据此下「没有」结论",
            file=sys.stderr,
        )
    elif degraded_seen:
        # 重试自愈也要留痕(透明):首次降级被自动重试救回,本次结果可信
        print("提示:首次检索曾降级,自动重试已成功(本次结果可信)", file=sys.stderr)
    results = result.get("results")

    def _trimmed(items):
        # --json --max-chars N(工单 03):全量 JSON 太重(8 条 17-28KB),
        # 中间档按需截 text;紧凑行不受影响,缺省不截保持现状
        if args.max_chars is None:
            return items
        limit = max(0, args.max_chars)
        trimmed = []
        for item in items:
            if (
                isinstance(item, dict)
                and isinstance(item.get("text"), str)
                and len(item["text"]) > limit
            ):
                item = dict(item, text=item["text"][:limit])
            trimmed.append(item)
        return trimmed

    if args.json:
        # 全量载荷(调试/审计用;日常检索用缺省紧凑行,别把账本字段
        # 灌进 agent 上下文)
        if results is not None:
            for item in _trimmed(results):
                print(json.dumps(item, ensure_ascii=False, sort_keys=True))
        else:
            for entry in _trimmed(result.get("entries", [])):
                print(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    elif results is not None:
        for item in results:
            score = item.get("score")
            badge = f"[{score:.2f}]" if isinstance(score, (int, float)) else ""
            if item.get("type") == "memory":
                entry = item.get("entry") or {}
                print(f"#{entry.get('entry_id')} {badge} {entry.get('statement', '')}")
            else:
                text = _knowledge_snippet(
                    str(item.get("origin_path") or ""), str(item.get("text") or "")
                )[:200]
                print(
                    f"[{item.get('source')}] {item.get('origin_path')} "
                    f"{badge} {text}"
                )
    else:
        for entry in result.get("entries", []):
            print(f"#{entry.get('entry_id')} {entry.get('statement', '')}")
    if results is not None:
        print(
            f"共 {result.get('count', 0)} 条合流(记忆+知识;"
            f"status={result.get('status')})",
            file=sys.stderr,
        )
    else:
        print(f"共 {result.get('count', 0)} 条(status={result.get('status')})", file=sys.stderr)
    if not result.get("count") and not degraded:
        # 0 条 ≠ 没有记忆(工单 02):单次 0 条就断言「没有」是本次体检
        # 实锤的翻车姿势——把口径说出口,agent 换措辞重试后再下结论
        print(
            "0 条=本查询措辞无命中(非故障);存疑请换措辞重试,"
            "勿直接下「没有」结论",
            file=sys.stderr,
        )


# ---- 知识正典读通道(memory-skill-qa 工单 05)----


def _get(args: argparse.Namespace) -> None:
    """按路径读知识正典全文(工单 05):search 只能看到进 top-k 的片段,
    已知路径的全文(如搜索结果里的 wiki/entities/X.md)直接取,不再换
    查询词碰运气。"""
    _hint_unknown_agent(args.agent)
    result = _request(
        "POST",
        "/knowledge/file",
        body={
            "source": args.source,
            "path": args.path,
            # 读即消费(工单 05):consumer 自报,与 search 同口径入账
            "consumer": args.agent,
        },
    )
    print(result.get("text") or "")
    print(
        f"已读取 {result.get('source')}/{result.get('path')}"
        f"({result.get('chars', 0)} 字符;来自 vault 正典)",
        file=sys.stderr,
    )


def _ls(args: argparse.Namespace) -> None:
    """列知识正典目录(工单 05):「我收藏了哪些 X」的盘点面——路径+标题
    +时间,可按前缀过滤、翻页。"""
    params: dict = {"source": args.source}
    if args.prefix:
        params["prefix"] = args.prefix
    if args.limit is not None:
        params["limit"] = args.limit
    if args.offset:
        params["offset"] = args.offset
    result = _request("POST", "/knowledge/list", body=params)
    for item in result.get("items", []):
        title = f"\t{item.get('title')}" if item.get("title") else ""
        print(f"{item.get('path')}{title}\t{item.get('mtime')}")
    print(
        f"共 {result.get('count', 0)}/{result.get('total', 0)} 条"
        f"(source={result.get('source')};limit/offset 可翻页)",
        file=sys.stderr,
    )


# 效用三态(工单 F):helped=帮上忙 / unused=没用到 / harmful=帮倒忙
OUTCOMES = ("helped", "unused", "harmful")


def _report(args: argparse.Namespace) -> int:
    """任务收尾效用回报(工单 F):对本会话取用过的记忆逐条 POST
    /memory/utility(三态 + 一句话理由;可选 supersedes 取代建议)。

    只进统计不改条目——服务端只落事件(永不自动归档/改写,证据链
    零自评内容);--supersedes 用于过时修正流:刚推了修正条目时,对
    修正条目报效用并指向它所修正的旧条目(建议进事件流,人工终审)。
    """
    if not args.id and not args.kpath:
        print("错误:缺少 --id(记忆条目)或 --kpath(知识正典)", file=sys.stderr)
        return 2
    payload_base = {
        "outcome": args.outcome,
        "reason": args.reason,
        "agent": args.agent,
    }
    if args.task_context:
        payload_base["task_context"] = args.task_context
    if args.supersedes:
        payload_base["supersedes"] = args.supersedes
    _hint_unknown_agent(args.agent)
    if args.kpath:
        # 知识面效用(memory-skill-qa 工单 09):对象是收藏的正典
        # (wiki|notes/<路径>,即搜索结果的 origin_path),只进统计不改文件
        if args.id:
            print("错误:--kpath 与 --id 互斥(一次报一个对象)", file=sys.stderr)
            return 2
        result = _request(
            "POST", "/memory/utility", body=dict(payload_base, knowledge_path=args.kpath)
        )
        print(
            f"已报知识效用 {result.get('knowledge_path')} "
            f"outcome={result.get('outcome', args.outcome)}"
            "(只进统计,不改任何文件)。",
            file=sys.stderr,
        )
        return 0
    for entry_id in args.id:
        result = _request(
            "POST", "/memory/utility", body=dict(payload_base, entry_id=entry_id)
        )
        print(
            f"已报效用 entry_id={result.get('entry_id')} "
            f"outcome={result.get('outcome', args.outcome)}"
            "(只进统计,不改条目)。",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="inone 统一记忆:保存(即时生效,写后审)/检索(已生效)/"
                    "效用回报(三态)/登录/自更新/项目初始化(init/pull)/"
                    "全局分发(init/pull --global,零 git)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    save = sub.add_parser("save", help="推送一条记忆(直接入库即时生效)")
    save.add_argument("--statement", required=True, help="记忆陈述句(要记住的内容)")
    save.add_argument("--kind", choices=KINDS, required=True,
                      help="三类型必填(无缺省):semantic(事实/知识/偏好)/"
                           "episodic(经历/决策史)/procedural(规则/教训/纠错)")
    save.add_argument("--evidence", default=None,
                      help="证据描述:用户在会话中的显式语句/上下文(进证据链)")
    save.add_argument("--agent", default=os.environ.get("INONE_AGENT_NAME") or "unknown",
                      help="推送方标识(进证据 ref,便于审计;缺省读 INONE_AGENT_NAME)")
    save.add_argument("--scope", default="user",
                      help="作用域命名空间:user|project:<名>(缺省 user;项目记忆不进全局分发)")
    save.add_argument("--tag", action="append", default=[], metavar="TAG",
                      help="自由标签(题材/时段,可多次)")
    save.add_argument("--ttl-days", type=int, default=None, help="时效(天),缺省永久")
    save.add_argument("--negative", action="store_true", help="负例区(我拒绝过什么)")
    save.add_argument("--provenance", choices=("inferred", "feedback", "user_entered"),
                      default=None, help="来源分层(缺省 inferred,机器通道默认)")
    save.set_defaults(func=_save)

    search = sub.add_parser(
        "search",
        help="检索记忆(带 --q 时统一召回:记忆+知识 wiki/notes 混排;"
             "缺省紧凑行输出,账本字段不进 agent 上下文)",
    )
    search.add_argument("--q", default=None,
                        help="关键词(统一召回:语义检索记忆+知识 wiki/notes)")
    search.add_argument("--kind", choices=KINDS, default=None, help="按类型过滤")
    search.add_argument("--scope", default=None,
                        help="按作用域命名空间:user|project:<名>")
    search.add_argument("--status", default="active",
                        choices=("active", "shadow", "archived"),
                        help="晋升状态(缺省 active=已生效)")
    search.add_argument("--consumer", default=None,
                        help="消费方身份(取用记账;缺省用 --agent 的值)")
    search.add_argument("--agent", default=os.environ.get("INONE_AGENT_NAME") or "unknown",
                        help="检索方标识(取用记账;缺省读 INONE_AGENT_NAME)")
    search.add_argument("--task-context", default=None, metavar="TEXT",
                        help="任务上下文(取用记账,可选)")
    search.add_argument("--json", action="store_true",
                        help="输出全量 JSON 载荷(调试/审计;缺省紧凑行)")
    search.add_argument("--max-chars", type=int, default=None, metavar="N",
                        help="--json 输出把 text 截到 N 字符(中间档;缺省不截,"
                             "紧凑行不受影响)")
    search.add_argument("--limit", type=int, default=None, metavar="N",
                        help="返回条数上限(服务端分页;配合翻页看 top-k 之外)")
    search.add_argument("--offset", type=int, default=None, metavar="N",
                        help="翻页偏移(与 --limit 配合;缺省 0)")
    search.set_defaults(func=_search)

    get = sub.add_parser(
        "get", help="读知识正典全文(工单 05):--source wiki|notes --path <vault 相对路径>"
    )
    get.add_argument("--source", required=True, choices=("wiki", "notes"),
                     help="知识来源(wiki|notes)")
    get.add_argument("--path", required=True, metavar="PATH",
                     help="vault 相对路径(如 entities/X.md;带 wiki/ 前缀的搜索结果路径也可)")
    get.add_argument("--agent", default=os.environ.get("INONE_AGENT_NAME") or "unknown",
                     help="读取方标识(取用记账;缺省读 INONE_AGENT_NAME)")
    get.set_defaults(func=_get)

    browse = sub.add_parser(
        "ls", help="列知识正典目录(工单 05):路径+标题+时间,可翻页"
    )
    browse.add_argument("--source", required=True, choices=("wiki", "notes"),
                        help="知识来源(wiki|notes)")
    browse.add_argument("--prefix", default="", metavar="PREFIX",
                        help="路径前缀过滤(如 entities/)")
    browse.add_argument("--limit", type=int, default=None, metavar="N",
                        help="单页条数(缺省 50,上限 200)")
    browse.add_argument("--offset", type=int, default=0, metavar="N",
                        help="翻页偏移(缺省 0)")
    browse.set_defaults(func=_ls)

    report = sub.add_parser(
        "report",
        help="任务收尾:报本会话取用过的记忆的效用(三态 + 一句话理由)",
    )
    report.add_argument("--id", action="append", default=[], metavar="ENTRY_ID",
                        help="被评价的记忆条目 ID(可多次;与 --kpath 互斥)")
    report.add_argument("--kpath", default=None, metavar="SOURCE/PATH",
                        help="被评价的知识正典(wiki|notes/<路径>,即搜索结果的 "
                             "origin_path;与 --id 互斥)")
    report.add_argument("--outcome", required=True, choices=OUTCOMES,
                        help="三态:helped=帮上忙/unused=没用到/harmful=帮倒忙")
    report.add_argument("--reason", required=True,
                        help="一句话理由(必填;无理由的自评没有判读价值)")
    report.add_argument("--supersedes", default=None, metavar="OLD_ENTRY_ID",
                        help="取代建议:指向被修正的旧条目(仅记录,不改写旧条目;"
                             "旧条目退役由用户终审)")
    report.add_argument("--task-context", default=None, metavar="TEXT",
                        help="任务上下文(可选)")
    report.add_argument("--agent", default=os.environ.get("INONE_AGENT_NAME") or "unknown",
                        help="回报方标识(进事件审计;缺省读 INONE_AGENT_NAME)")
    report.set_defaults(func=_report)

    login = sub.add_parser(
        "login", help="登录:输入服务地址+粘贴 token,存本机(~/.inone/config.json)"
    )
    login.set_defaults(func=_login)

    update = sub.add_parser(
        "update", help="自更新:拉服务端最新版并自替换(校验通过才换文件)"
    )
    update.set_defaults(func=_update)

    init = sub.add_parser(
        "init",
        help="项目初始化:交互选领域 → 拉取写 .inone-memory/(头部只读铁律)"
             " + AGENTS.md 幂等追加引用段;--global 则走全局分发(HTTP 拉四"
             " 文件到 ~/inone-memory-dist,零 git)",
    )
    init.add_argument(
        "--domain", action="append", default=[], metavar="DOMAIN|none|all",
        help="跳过交互直接指定领域(可多次或逗号分隔;none=未分类;"
             "all=清领域口径回全量主面;非交互环境必填)",
    )
    init.add_argument(
        "--project", default=None, metavar="SLUG",
        help="项目 slug(缺省从当前目录名推导;slug=[a-z0-9_-]+,"
             "决定 project:<slug> 作用域的条目全量并入)",
    )
    init.add_argument(
        "--global", dest="global_mode", action="store_true",
        help="全局分发首次拉取(ku-07,与项目级互斥):经 HTTP 下载"
             " MEMORY-CORE.md + topics/<kind>.md 四文件到 ~/inone-memory-dist"
             "(纯文件夹无 .git;逐文件 sha256 校验,写版本元数据;"
             "凭据复用 login);可选领域口径——交互或 --domain 选领域,"
             "主面四文件按所选领域过滤(ADR 0020),领域深度文件另落 domains/",
    )
    init.add_argument(
        "--dest", default=None, metavar="PATH",
        help="--global 的分发目录(缺省 ~/" + DIST_DIR +
             ";须与 AGENTS.md 引用路径一致)",
    )
    init.add_argument(
        "--no-domain", dest="no_domain", action="store_true",
        help="--global 跳过领域追加面交互(安装脚本/无人值守管道用;"
             "手动刷新领域面:去掉本旗标重跑,或 --domain 显式指定)",
    )
    init.set_defaults(func=_cmd_init)

    pull = sub.add_parser(
        "pull",
        help="增量刷新:按 init 存档的口径重拉覆盖 .inone-memory/"
             "(AGENTS.md 不重复追加);--global 则按 manifest 哈希增量刷新"
             " ~/inone-memory-dist(变了才下载,失败留旧文件)",
    )
    pull.add_argument(
        "--global", dest="global_mode", action="store_true",
        help="全局分发增量刷新(ku-07,与项目级互斥):manifest 哈希比对,"
             "变了才下载;失败保留旧文件 + 非零退出;凭据复用 login"
             "(安装脚本注册的每日计划任务调的就是它)",
    )
    pull.add_argument(
        "--dest", default=None, metavar="PATH",
        help="--global 的分发目录(缺省 ~/" + DIST_DIR + ")",
    )
    pull.set_defaults(func=_cmd_pull)

    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except (EOFError, KeyboardInterrupt) as error:
        # 交互流中断:给人话并落到暂停逻辑,窗口模式下不再闪退
        print(f"错误:交互输入中断({type(error).__name__}):命令未完成", file=sys.stderr)
        result = 2
    except Exception as error:  # 兜底:任何未预期异常都要过"出错必停",窗口不闪没
        print(f"错误:{type(error).__name__}: {error}", file=sys.stderr)
        result = 1
    code = 0 if result is None else int(result)
    # 出错必停(人要看原因);login 无论成败都停(交互会话要看结果)——
    # 仅当本进程独占控制台(窗口即将关闭)时;agent 管道/计划任务/
    # 用户自己的终端都不暂停(_owns_console 判据见上)
    if code != 0 or args.command == "login":
        _pause_if_window_owned()
    return code


if __name__ == "__main__":
    sys.exit(main())
