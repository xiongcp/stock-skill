#!/usr/bin/env bash
# stock-skill 一键安装脚本（macOS / Linux）
#
#   curl -fsSL https://raw.githubusercontent.com/xiongcp/stock-skill/main/install.sh | bash
#
# 重复执行即更新；--uninstall 只删除本脚本创建的软链、复制目录和代码目录。
# 带参数运行：curl -fsSL .../install.sh | bash -s -- --agents claude,codex --dry-run
set -euo pipefail

REPO_URL="https://github.com/xiongcp/stock-skill.git"
SKILL_NAME="kline-structure-chart"
# 代码目录与复制安装目录中的归属标记：卸载时只删除带标记的目录
MARKER_NAME=".stock-skill-installer"
ALL_AGENTS="claude codex gemini cursor opencode"

INSTALL_DIR="${STOCK_SKILL_DIR:-$HOME/.local/share/stock-skill}"
SOURCE="$REPO_URL"
AGENTS=""
PROJECT_DIR=""
COPY=0
DRY_RUN=0
UNINSTALL=0
RUN_TEST=1
INSTALL_UV=0

usage() {
  cat <<'EOF'
用法: install.sh [选项]

  --dir <目录>        代码安装目录（默认 ~/.local/share/stock-skill，也可用环境变量 STOCK_SKILL_DIR）
  --source <地址>     克隆来源：Git 地址或本地检出路径（默认 GitHub xiongcp/stock-skill）
  --agents <列表>     逗号分隔：claude,codex,gemini,cursor,opencode 或 all（默认自动探测）
  --project <目录>    只注册到某个项目（Claude Code 用 <目录>/.claude/skills，其余用 <目录>/.agents/skills）
  --copy              复制技能目录而不是软链（更新后需重新执行本脚本）
  --install-uv        缺少 uv 时直接用官方安装器安装（非交互环境必须显式传入）
  --no-test           跳过安装后的离线自检与 doctor
  --dry-run           只打印将要执行的操作，不做任何修改
  --update            更新（等同重新执行安装）
  --uninstall         卸载：删除本脚本创建的软链、复制目录和代码目录
  -h, --help          显示帮助

技能目录（与 vercel-labs/skills 的约定一致）：
  claude   ~/.claude/skills            codex    ~/.agents/skills
  gemini   ~/.gemini/skills            cursor   ~/.cursor/skills
  opencode ~/.config/opencode/skills
EOF
}

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[警告]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[错误]\033[0m %s\n' "$*" >&2; exit 1; }

# 打印并执行；--dry-run 时只打印
run() {
  printf '    + %s\n' "$*"
  if [ "$DRY_RUN" -eq 0 ]; then "$@"; fi
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir) INSTALL_DIR="${2:?--dir 需要参数}"; shift 2 ;;
    --source) SOURCE="${2:?--source 需要参数}"; shift 2 ;;
    --agents) AGENTS="${2:?--agents 需要参数}"; shift 2 ;;
    --project) PROJECT_DIR="${2:?--project 需要参数}"; shift 2 ;;
    --copy) COPY=1; shift ;;
    --install-uv) INSTALL_UV=1; shift ;;
    --no-test) RUN_TEST=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --update) shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "未知参数: $1" ;;
  esac
done

abspath() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    \~*) printf '%s\n' "$HOME${1#\~}" ;;
    *) printf '%s\n' "$(pwd)/$1" ;;
  esac
}

INSTALL_DIR="$(abspath "$INSTALL_DIR")"
SKILL_SRC="$INSTALL_DIR/$SKILL_NAME"
if [ -n "$PROJECT_DIR" ]; then
  PROJECT_DIR="$(abspath "$PROJECT_DIR")"
  [ -d "$PROJECT_DIR" ] || die "项目目录不存在: $PROJECT_DIR"
fi
# 本地检出路径转为绝对路径，便于之后 git pull
if [ -d "$SOURCE" ]; then SOURCE="$(cd "$SOURCE" && pwd)"; fi

skills_dir_for() {
  local agent="$1"
  if [ -n "$PROJECT_DIR" ]; then
    case "$agent" in
      claude) printf '%s\n' "$PROJECT_DIR/.claude/skills" ;;
      *) printf '%s\n' "$PROJECT_DIR/.agents/skills" ;;
    esac
    return
  fi
  case "$agent" in
    claude) printf '%s\n' "$HOME/.claude/skills" ;;
    codex) printf '%s\n' "$HOME/.agents/skills" ;;
    gemini) printf '%s\n' "$HOME/.gemini/skills" ;;
    cursor) printf '%s\n' "$HOME/.cursor/skills" ;;
    opencode) printf '%s\n' "$HOME/.config/opencode/skills" ;;
    *) die "不支持的 agent: $agent（可选 $ALL_AGENTS）" ;;
  esac
}

detect_agents() {
  local found=""
  command -v claude >/dev/null 2>&1 && found="$found claude"
  command -v codex >/dev/null 2>&1 && found="$found codex"
  command -v gemini >/dev/null 2>&1 && found="$found gemini"
  command -v cursor-agent >/dev/null 2>&1 && found="$found cursor"
  command -v opencode >/dev/null 2>&1 && found="$found opencode"
  printf '%s\n' "${found# }"
}

resolve_agents() {
  if [ -z "$AGENTS" ]; then
    AGENTS="$(detect_agents)"
    if [ -z "$AGENTS" ]; then
      AGENTS="claude codex"
      warn "未探测到 claude / codex / gemini / cursor-agent / opencode 命令，默认注册到 Claude Code 与 ~/.agents/skills"
    fi
  elif [ "$AGENTS" = "all" ]; then
    AGENTS="$ALL_AGENTS"
  else
    AGENTS="$(printf '%s' "$AGENTS" | tr ',' ' ')"
  fi
  local a
  for a in $AGENTS; do skills_dir_for "$a" >/dev/null; done
}

# 多个 agent 共用同一个技能目录时（例如 --project 下的 .agents/skills）只处理一次
unique_skill_dirs() {
  local a d seen=""
  for a in $AGENTS; do
    d="$(skills_dir_for "$a")"
    case " $seen " in *" $d "*) ;; *) seen="$seen $d"; printf '%s\n' "$d" ;; esac
  done
}

is_ours() {
  # 本脚本创建的链接（指向本次代码目录），或带归属标记的复制目录
  local target="$1"
  if [ -L "$target" ]; then
    [ "$(readlink "$target")" = "$SKILL_SRC" ]
  else
    [ -f "$target/$MARKER_NAME" ]
  fi
}

# ---------------------------------------------------------------------------
# 卸载
# ---------------------------------------------------------------------------
uninstall() {
  say "卸载 $SKILL_NAME（只删除本脚本创建的内容）"
  local a d target removed=0
  if [ -n "$AGENTS" ]; then resolve_agents; else AGENTS="$ALL_AGENTS"; fi
  while IFS= read -r d; do
    target="$d/$SKILL_NAME"
    if [ -L "$target" ] || [ -e "$target" ]; then
      if is_ours "$target"; then
        run rm -rf "$target"
        removed=$((removed + 1))
      else
        warn "跳过 $target：不是本脚本创建的（未删除）"
      fi
    fi
    for a in "$d/$SKILL_NAME".bak.*; do
      if [ -e "$a" ]; then warn "保留了安装时的备份 $a，如不需要请手动删除"; fi
    done
  done < <(unique_skill_dirs)
  if [ -d "$INSTALL_DIR" ]; then
    if [ -f "$INSTALL_DIR/.git/$MARKER_NAME" ]; then
      run rm -rf "$INSTALL_DIR"
    else
      warn "跳过 $INSTALL_DIR：不是本脚本克隆的代码目录（未删除）"
    fi
  fi
  say "卸载完成，删除技能入口 $removed 个"
}

# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------
ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    say "uv 已安装：$(uv --version)"
    return
  fi
  if [ "$INSTALL_UV" -eq 0 ]; then
    if (: </dev/tty) 2>/dev/null; then
      local ans=""
      printf '未找到 uv。是否用官方安装器安装（curl -LsSf https://astral.sh/uv/install.sh | sh）？[y/N] ' >/dev/tty
      read -r ans </dev/tty || ans=""
      case "$ans" in y|Y|yes|YES) INSTALL_UV=1 ;; esac
    fi
    [ "$INSTALL_UV" -eq 1 ] || die "需要 uv：请先安装（https://docs.astral.sh/uv/），或加 --install-uv 让本脚本安装"
  fi
  command -v curl >/dev/null 2>&1 || die "安装 uv 需要 curl"
  say "安装 uv"
  printf '    + curl -LsSf https://astral.sh/uv/install.sh | sh\n'
  if [ "$DRY_RUN" -eq 0 ]; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    command -v uv >/dev/null 2>&1 || die "uv 安装后仍不在 PATH 中，请新开终端后重试"
  fi
}

fetch_code() {
  if [ -d "$INSTALL_DIR/.git" ]; then
    say "更新代码：$INSTALL_DIR"
    run git -C "$INSTALL_DIR" pull --ff-only
  elif [ -e "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    die "$INSTALL_DIR 已存在且不是 Git 仓库；请换一个 --dir 或手动处理"
  else
    say "克隆代码：$SOURCE -> $INSTALL_DIR"
    run mkdir -p "$(dirname "$INSTALL_DIR")"
    run git clone --depth 1 "$SOURCE" "$INSTALL_DIR"
    if [ "$DRY_RUN" -eq 0 ]; then
      printf 'installed-by=install.sh\nsource=%s\n' "$SOURCE" >"$INSTALL_DIR/.git/$MARKER_NAME"
    fi
  fi
  if [ "$DRY_RUN" -eq 0 ] && [ ! -f "$SKILL_SRC/SKILL.md" ]; then
    die "$SKILL_SRC/SKILL.md 不存在，代码目录不完整"
  fi
}

sync_deps() {
  say "安装 Python 3.12 与锁定依赖（uv.lock）"
  run uv sync --frozen --python 3.12 --directory "$SKILL_SRC"
}

check_fonts() {
  [ "$(uname -s)" = "Linux" ] || return 0
  # 与 scripts/render_structure_chart.py 的 FONT_CANDIDATES 保持一致
  local f
  for f in /usr/share/fonts/truetype/wqy/wqy-zenhei.ttc \
    /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc \
    /usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf \
    /usr/share/fonts/wqy-zenhei/wqy-zenhei.ttc \
    /usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc \
    /usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc; do
    if [ -f "$f" ]; then say "中文字体：$f"; return 0; fi
  done
  warn "未找到中文字体，PNG 无法绘制。请按发行版手动安装（本脚本不会执行 sudo）："
  if command -v apt-get >/dev/null 2>&1; then
    printf '    sudo apt-get install -y fonts-noto-cjk\n'
  elif command -v dnf >/dev/null 2>&1; then
    printf '    sudo dnf install -y google-noto-sans-cjk-ttc-fonts\n'
  elif command -v pacman >/dev/null 2>&1; then
    printf '    sudo pacman -S noto-fonts-cjk\n'
  else
    printf '    安装 Noto Sans CJK 或文泉驿正黑（wqy-zenhei）\n'
  fi
}

link_one() {
  local dir="$1" target="$1/$SKILL_NAME" backup
  run mkdir -p "$dir"
  if [ -L "$target" ]; then
    if [ "$(readlink "$target")" != "$SKILL_SRC" ]; then
      warn "$target 原本指向 $(readlink "$target")，将改为指向本次安装"
    fi
    run rm -f "$target"
  elif [ -e "$target" ]; then
    if is_ours "$target"; then
      run rm -rf "$target"
    else
      backup="$target.bak.$(date +%Y%m%d%H%M%S)"
      warn "$target 已存在且不是软链，先备份到 $backup"
      run mv "$target" "$backup"
    fi
  fi
  if [ "$COPY" -eq 1 ]; then
    run mkdir -p "$target"
    printf '    + (cd %s && tar --exclude .venv --exclude artifacts --exclude __pycache__ --exclude .pytest_cache -cf - .) | (cd %s && tar -xf -)\n' "$SKILL_SRC" "$target"
    if [ "$DRY_RUN" -eq 0 ]; then
      (cd "$SKILL_SRC" && tar --exclude .venv --exclude artifacts --exclude __pycache__ --exclude .pytest_cache -cf - .) | (cd "$target" && tar -xf -)
      printf 'installed-by=install.sh\nsource=%s\n' "$SKILL_SRC" >"$target/$MARKER_NAME"
    fi
  else
    run ln -s "$SKILL_SRC" "$target"
  fi
}

register_agents() {
  resolve_agents
  say "注册到 agent：$AGENTS$([ -n "$PROJECT_DIR" ] && printf '（项目 %s）' "$PROJECT_DIR")"
  local d
  while IFS= read -r d; do link_one "$d"; done < <(unique_skill_dirs)
}

self_test() {
  [ "$RUN_TEST" -eq 1 ] || { say "跳过自检（--no-test）"; return 0; }
  say "离线快速自检（文档契约 + 工具测试，不访问网络）"
  run uv run --frozen --directory "$SKILL_SRC" pytest -q tests/test_docs_contract.py tests/test_tools.py
  say "环境自检 doctor（数据源连通项需要联网，失败不影响安装）"
  local doctor_args=()
  [ -n "$PROJECT_DIR" ] && doctor_args=(--project "$PROJECT_DIR")
  if [ "$DRY_RUN" -eq 0 ]; then
    run uv run --frozen --directory "$SKILL_SRC" python scripts/doctor.py ${doctor_args[@]+"${doctor_args[@]}"} || warn "doctor 有未通过项，请按上面的建议处理"
  else
    run uv run --frozen --directory "$SKILL_SRC" python scripts/doctor.py ${doctor_args[@]+"${doctor_args[@]}"}
  fi
}

install() {
  [ "$DRY_RUN" -eq 1 ] && say "演练模式（--dry-run）：只打印，不修改任何文件"
  resolve_agents
  command -v git >/dev/null 2>&1 || die "需要 git：macOS 运行 xcode-select --install，Linux 用包管理器安装 git"
  say "git 已安装：$(git --version)"
  ensure_uv
  fetch_code
  sync_deps
  check_fonts
  register_agents
  self_test
  if [ "$DRY_RUN" -eq 1 ]; then
    say "演练结束：以上操作均未执行"
    return 0
  fi
  cat <<EOF

安装完成。
  代码目录：$INSTALL_DIR
  更新：    重新执行同一条安装命令（或 install.sh --update）
  卸载：    bash "$INSTALL_DIR/install.sh" --uninstall

下一步：新开一个 Claude Code / Codex / Gemini CLI 会话，试问"帮我分析 600519.SH"或"看看茅台的 K 线"。
EOF
}

if [ "$UNINSTALL" -eq 1 ]; then uninstall; else install; fi
