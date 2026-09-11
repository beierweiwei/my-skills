#!/usr/bin/env bash
set -uo pipefail

# Linux/macOS equivalent of update-skills.ps1. Repositories are shallow clones;
# skill entries are symbolic links into the clone cache.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPOS_DIR="$ROOT/repos"
SKILLS_DIR="$ROOT/skills"
PLUGINS_DIR="$ROOT/plugins"
PROXY="${HTTPS_PROXY:-http://127.0.0.1:7890}"
NO_PROXY_MODE=0

usage() {
  printf 'Usage: %s [--proxy URL] [--no-proxy]\n' "$0"
}

while (($#)); do
  case "$1" in
    --proxy)
      (($# >= 2)) || { usage >&2; exit 2; }
      PROXY="$2"
      shift 2
      ;;
    --no-proxy)
      NO_PROXY_MODE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$REPOS_DIR" "$SKILLS_DIR" "$PLUGINS_DIR"

update_repo() {
  local name="$1" url="$2" ref="$3" dest="$REPOS_DIR/$1"
  local -a git_args=(git)
  if ((NO_PROXY_MODE == 0)); then
    git_args+=( -c "http.proxy=$PROXY" -c "https.proxy=$PROXY" )
  fi

  if [[ -d "$dest/.git" ]]; then
    printf '[%s] updating to %s...\n' "$name" "$ref"
    "${git_args[@]}" -C "$dest" fetch --depth 1 origin "$ref" || return 1
    git -C "$dest" reset --hard FETCH_HEAD || return 1
    git -C "$dest" clean -fdq || return 1
  else
    [[ ! -e "$dest" ]] || rm -rf -- "$dest"
    printf '[%s] cloning...\n' "$name"
    "${git_args[@]}" clone --depth 1 --branch "$ref" "$url" "$dest" || return 1
  fi
}

link_entry() {
  local link="$1" target="$2"
  if [[ ! -e "$target" ]]; then
    printf 'Warning: target does not exist, skipped: %s\n' "$target" >&2
    return 0
  fi
  if [[ -e "$link" || -L "$link" ]]; then
    [[ -L "$link" ]] || { printf 'Error: %s exists and is not a symlink\n' "$link" >&2; return 1; }
    rm -- "$link"
  fi
  ln -s -- "$target" "$link"
  printf '  + %s -> %s\n' "$link" "$target"
}

failed=()
update_repo mattpocock-skills https://github.com/mattpocock/skills.git main || failed+=(mattpocock-skills)
if [[ -d "$REPOS_DIR/mattpocock-skills" ]]; then
  while IFS=$'\t' read -r name path; do
    link_entry "$SKILLS_DIR/$name" "$REPOS_DIR/mattpocock-skills/$path" || failed+=("skill:$name")
  done <<'EOF'
ask-matt	skills/engineering/ask-matt
code-review	skills/engineering/code-review
codebase-design	skills/engineering/codebase-design
diagnosing-bugs	skills/engineering/diagnosing-bugs
domain-modeling	skills/engineering/domain-modeling
grill-with-docs	skills/engineering/grill-with-docs
implement	skills/engineering/implement
improve-codebase-architecture	skills/engineering/improve-codebase-architecture
prototype	skills/engineering/prototype
research	skills/engineering/research
resolving-merge-conflicts	skills/engineering/resolving-merge-conflicts
setup-matt-pocock-skills	skills/engineering/setup-matt-pocock-skills
tdd	skills/engineering/tdd
to-spec	skills/engineering/to-spec
to-tickets	skills/engineering/to-tickets
triage	skills/engineering/triage
wayfinder	skills/engineering/wayfinder
wizard	skills/engineering/wizard
grill-me	skills/productivity/grill-me
grilling	skills/productivity/grilling
handoff	skills/productivity/handoff
teach	skills/productivity/teach
to-questionnaire	skills/productivity/to-questionnaire
wait-what	skills/productivity/wait-what
writing-for-agents	skills/productivity/writing-for-agents
EOF
fi

update_and_link() {
  local name="$1" url="$2" ref="$3" entries="$4"
  update_repo "$name" "$url" "$ref" || { failed+=("$name"); return; }
  while IFS=$'\t' read -r skill path; do
    [[ -n "$skill" ]] || continue
    link_entry "$SKILLS_DIR/$skill" "$REPOS_DIR/$name/$path" || failed+=("skill:$skill")
  done <<< "$entries"
}

update_and_link ponytail https://github.com/DietrichGebert/ponytail.git main $'ponytail\tskills/ponytail\nponytail-audit\tskills/ponytail-audit\nponytail-debt\tskills/ponytail-debt\nponytail-gain\tskills/ponytail-gain\nponytail-help\tskills/ponytail-help\nponytail-review\tskills/ponytail-review'
update_and_link caveman https://github.com/JuliusBrussee/caveman.git main $'caveman\tskills/caveman'
update_and_link ui-ux-pro-max-skill https://github.com/nextlevelbuilder/ui-ux-pro-max-skill.git main $'ui-ux-pro-max\t.claude/skills/ui-ux-pro-max'
update_and_link planning-with-files https://github.com/OthmanAdi/planning-with-files.git master $'planning-with-files\tskills/planning-with-files'

update_repo claude-plugins-official https://github.com/anthropics/claude-plugins-official.git main || failed+=(claude-plugins-official)
if [[ -d "$REPOS_DIR/claude-plugins-official" ]]; then
  while IFS= read -r plugin; do
    link_entry "$PLUGINS_DIR/$plugin" "$REPOS_DIR/claude-plugins-official/plugins/$plugin" || failed+=("plugin:$plugin")
  done <<'EOF'
claude-code-setup
claude-md-management
code-review
commit-commands
feature-dev
frontend-design
hookify
ralph-loop
typescript-lsp
EOF
fi

if ((${#failed[@]})); then
  printf 'Completed with failures: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf 'Open-source skills updated.\n'
