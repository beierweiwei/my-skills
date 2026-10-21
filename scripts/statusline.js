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

/** 返回 { used, size, pct }，缺失的项为 null */
function contextUsage(d) {
  const ctx = d.context_window || d.context || d.usage || {};

  let size = Number(first(ctx.context_window_size, ctx.max_tokens, ctx.window_size));
  let pct = Number(first(ctx.used_percentage, ctx.percent, ctx.context_percent));

  let used = Number(first(ctx.used_tokens, ctx.tokens_used, ctx.context_used));
  if (!Number.isFinite(used)) {
    const cu = ctx.current_usage;
    if (cu) {
      const keys = ['input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens', 'output_tokens'];
      const vals = keys.map((k) => Number(cu[k])).filter(Number.isFinite);
      if (vals.length) used = vals.reduce((a, b) => a + b, 0);
    }
  }
  if (!Number.isFinite(used)) {
    const ti = Number(ctx.total_input_tokens);
    const to = Number(ctx.total_output_tokens);
    if (Number.isFinite(ti) || Number.isFinite(to)) {
      used = (Number.isFinite(ti) ? ti : 0) + (Number.isFinite(to) ? to : 0);
    }
  }
  if (!Number.isFinite(used) && Number.isFinite(pct) && Number.isFinite(size)) {
    used = Math.round((pct / 100) * size);
  }
  if (!Number.isFinite(pct) && Number.isFinite(used) && Number.isFinite(size) && size > 0) {
    pct = (used / size) * 100;
  }

  return {
    used: Number.isFinite(used) ? used : null,
    size: Number.isFinite(size) ? size : null,
    pct: Number.isFinite(pct) ? pct : null,
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
  process.stdout.write(render(raw ? JSON.parse(raw) : {}) + '\n');
} catch {
  // 状态栏异常不能打断主流程
  process.stdout.write('\n');
}
