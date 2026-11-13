#!/usr/bin/env node
/**
 * 跨 Agent 状态栏渲染器（Claude Code / Qoder / Qoder CN 通用）
 * 展示四项：模型、已用上下文大小、git 仓库名、分支。
 * 会话数据由 Agent 通过 stdin 传入 JSON；字段名做兼容归一化。
 * 输出一行文本（含 ANSI 颜色，可用 NO_COLOR=1 关闭）。
 */
'use strict';

const { execFileSync } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const env = process.env;
const useColor = !('NO_COLOR' in env);
const paint = (code, text) => (useColor ? `\x1b[${code}m${text}\x1b[0m` : text);
const first = (...values) => values.find((v) => v !== undefined && v !== null && v !== '');

function readStdin() {
  if (process.stdin.isTTY) return '';
  try {
    return fs.readFileSync(0, 'utf8');
  } catch {
    return '';
  }
}

/**
 * T57/R1 校准辅助：STATUSLINE_PAYLOAD_LOG 指向目录时把 stdin 原始 JSON 落盘
 * （每 session_id 最多 3 条样本），供 ctx 取值链与真实 payload 比对。
 * 失败静默（状态栏异常不能打断主流程）。
 */
function dumpPayload(raw, d) {
  const dir = env.STATUSLINE_PAYLOAD_LOG;
  if (!dir) return;
  try {
    fs.mkdirSync(dir, { recursive: true });
    const sid = String(d?.session_id || 'nosession').slice(0, 40);
    const marker = path.join(dir, `.seen-${sid}`);
    let seen = 0;
    try { seen = Number(fs.readFileSync(marker, 'utf8')) || 0; } catch {}
    if (seen >= 3) return;
    fs.writeFileSync(
      path.join(dir, `payload-${sid}-${seen}.json`), raw, { flag: 'wx' });
    fs.writeFileSync(marker, String(seen + 1));
  } catch {}
}

function gitInfo(dir) {
  const run = (args) => {
    try {
      return execFileSync('git', ['-C', dir, ...args], {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
        timeout: 500,
      }).trim();
    } catch {
      return '';
    }
  };

  const branch = run(['rev-parse', '--abbrev-ref', 'HEAD']);
  let repo = '';
  const url = run(['config', '--get', 'remote.origin.url']);
  if (url) repo = url.replace(/[\\/]+$/, '').split(/[\\/:]/).pop().replace(/\.git$/, '');
  if (!repo) {
    const top = run(['rev-parse', '--show-toplevel']);
    if (top) repo = path.basename(top);
  }
  return { branch: branch === 'HEAD' ? '' : branch, repo };
}

function formatTokens(n) {
  if (n >= 1000000) return `${(n / 1000000).toFixed(1).replace(/\.0$/, '')}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(n < 100000 ? 1 : 0).replace(/\.0$/, '')}k`;
  return String(n);
}

const MODEL_ALIASES = ['OPUS', 'SONNET', 'HAIKU', 'FABLE'];

function normalizeModelId(value) {
  return String(value || '')
    .toLowerCase()
    .replace(/\[[^\]]*\]/g, '')
    .trim();
}

/**
 * 接入第三方模型后，Claude Code 的 model.display_name 仍是 Opus/Sonnet 等别名；
 * 真实上游名称由 ANTHROPIC_DEFAULT_<ALIAS>_MODEL_NAME 提供。
 */
function resolveModelName(d) {
  const id = first(d.model?.id, d.model?.display_name, d.model?.name);
  const normId = normalizeModelId(id);

  for (const alias of MODEL_ALIASES) {
    const name = env[`ANTHROPIC_DEFAULT_${alias}_MODEL_NAME`];
    const aliasId = env[`ANTHROPIC_DEFAULT_${alias}_MODEL`];
    if (name && aliasId && normId && normId === normalizeModelId(aliasId)) return name;
  }
  for (const alias of MODEL_ALIASES) {
    const name = env[`ANTHROPIC_DEFAULT_${alias}_MODEL_NAME`];
    if (name && normId.includes(alias.toLowerCase())) return name;
  }
  return (
    first(d.model?.display_name, d.model?.name, d.model?.id, env.QODER_MODEL, env.CLAUDE_MODEL) ||
    'unknown'
  );
}

/**
 * 返回 { used, size, pct }，缺失的项为 null。
 *
 * T57/R1 校准（对齐 claude code 官方 statusline stdin schema）：
 * - claude 的 `context_window.total_input_tokens` = 当前上下文窗口 token 数
 *   （input 含 cache 读+写）；`used_percentage` 为官方预计算，口径=input-only
 *   （input + cache_creation + cache_read，不含 output_tokens）。
 * - 未上报形态：total_input_tokens=0 且 current_usage=null（首个 API 响应前）
 *   ——此时 0 不是真实用量，返回 null（渲染为 `--`，guard 解析失败显式未知，
 *   不再产出假 0/1M 让 guard ratio 恒 0 静默失效）。
 */
function contextUsage(d) {
  const ctx = d.context_window || d.context || d.usage || {};

  const size = Number(first(ctx.context_window_size, ctx.max_tokens, ctx.window_size));
  const pctRaw = Number(first(ctx.used_percentage, ctx.percent, ctx.context_percent));

  // cu 非 null 时为权威分类计数（官方：/compact 后直到下次 API 前为 null）
  const cu = ctx.current_usage;
  let used;
  if (cu && typeof cu === 'object') {
    const keys = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'];
    const vals = keys.map((k) => Number(cu[k])).filter(Number.isFinite);
    used = vals.length ? vals.reduce((a, b) => a + b, 0) : NaN;
  } else {
    used = Number(first(ctx.used_tokens, ctx.tokens_used, ctx.context_used));
    if (!Number.isFinite(used)) {
      const ti = Number(ctx.total_input_tokens);
      const to = Number(ctx.total_output_tokens);
      if (Number.isFinite(ti) && ti !== 0) used = ti;
      else if (Number.isFinite(ti) || Number.isFinite(to)) {
        used = (Number.isFinite(ti) ? ti : 0) + (Number.isFinite(to) ? to : 0);
      }
    }
    // 0 但官方预计算 pct>0 → 0 是未上报假象，用 pct 反推
    if (used === 0 && Number.isFinite(pctRaw) && pctRaw > 0
        && Number.isFinite(size) && size > 0) {
      used = Math.round((pctRaw / 100) * size);
    }
  }
  let pct = pctRaw;
  if (!Number.isFinite(pct) && Number.isFinite(used) && Number.isFinite(size) && size > 0) {
    pct = (used / size) * 100;
  }

  // 未上报/异常上报判定（T57/R1）：真实 ctx 永远 > 0（system prompt 起步即数千
  // token），所以「used=0 且无任何非零信号」都是网关未带 usage 的全零上报
  //（实测 payload：cu 全零 + total=0 + pct=0，渲染假 `0/1M`）→ unknown，
  // 渲染 `--`，guard parse_ctx_k 失败显式未知，不产出假 0 让交接静默失效。
  const hasSignal = (Number.isFinite(used) && used > 0)
    || (Number.isFinite(pctRaw) && pctRaw > 0)
    || (cu && typeof cu === 'object' && Number(cu.input_tokens) > 0)
    || (Number.isFinite(Number(ctx.total_input_tokens))
        && Number(ctx.total_input_tokens) > 0);

  return {
    used: hasSignal && Number.isFinite(used) ? used : null,
    size: Number.isFinite(size) ? size : null,
    pct: hasSignal && Number.isFinite(pct) ? pct : null,
  };
}

function render(d) {
  const model = resolveModelName(d);

  const dir =
    first(
      d.workspace?.current_dir,
      d.workspace?.cwd,
      d.cwd,
      d.working_directory,
      env.QODER_WORKING_DIR,
    ) || process.cwd();

  const fromPayload = first(d.workspace?.repo?.name, d.repo?.name, d.repository?.name);
  const git = gitInfo(dir);
  const repo = fromPayload || git.repo || path.basename(dir);
  const branch =
    first(d.git?.branch, d.workspace?.git_branch, d.git_branch, env.GIT_BRANCH) || git.branch;

  const { used, size, pct } = contextUsage(d);
  const ctxColor = pct === null ? '36' : pct >= 80 ? '31' : pct >= 50 ? '33' : '32';
  let ctxText = '--';
  if (used !== null && size !== null) ctxText = `${formatTokens(used)}/${formatTokens(size)}`;
  else if (used !== null) ctxText = formatTokens(used);
  else if (pct !== null) ctxText = `${Math.round(pct)}%`;

  const parts = [
    paint('36', `[${model}]`),
    `ctx ${paint(ctxColor, ctxText)}`,
    `📦 ${paint('34', repo)}`,
  ];
  if (branch) parts.push(`\uf418 ${paint('32', branch)}`);
  return parts.join('  ');
}

try {
  const raw = readStdin().trim();
  const data = raw ? JSON.parse(raw) : {};
  dumpPayload(raw, data);
  process.stdout.write(render(data) + '\n');
} catch {
  // 状态栏异常不能打断主流程
  process.stdout.write('\n');
}
