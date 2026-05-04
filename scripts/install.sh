#!/usr/bin/env bash
# OpenSprite installer for fresh Linux machines.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/HsinPu/opensprite/main/scripts/install.sh | bash -s -- --start
#
# Installs source code into ~/.local/share/opensprite/opensprite by default
# and links the `opensprite` command into ~/.local/bin. Runtime config/data
# stays under ~/.opensprite.

set -euo pipefail

REPO_URL="${OPENSPRITE_REPO_URL:-https://github.com/HsinPu/opensprite.git}"
BRANCH="${OPENSPRITE_BRANCH:-main}"
INSTALL_DIR="${OPENSPRITE_INSTALL_DIR:-$HOME/.local/share/opensprite/opensprite}"
APP_HOME="${OPENSPRITE_HOME:-$HOME/.opensprite}"
PYTHON_VERSION_MIN="3.11"
INSTALL_DEV=0
CREATE_LINK=1
START_SERVICE=0
INSTALL_SYSTEM_PACKAGES=1

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { printf "%b==>%b %s\n" "$CYAN" "$NC" "$1"; }
log_success() { printf "%b✓%b %s\n" "$GREEN" "$NC" "$1"; }
log_warn() { printf "%b!%b %s\n" "$YELLOW" "$NC" "$1"; }
log_error() { printf "%bError:%b %s\n" "$RED" "$NC" "$1" >&2; }

usage() {
  cat <<'EOF'
OpenSprite installer

Usage: install.sh [options]

Options:
  --dir PATH       Install repository checkout to PATH.
                   Default: ~/.local/share/opensprite/opensprite
  --branch NAME    Git branch to install. Default: main
  --repo URL       Git repository URL. Default: https://github.com/HsinPu/opensprite.git
  --dev            Install development dependencies with -e ".[dev]".
  --start          Start the background gateway after installation.
  --no-link        Do not create ~/.local/bin/opensprite symlink.
  --no-system      Do not try to install system packages with apt.
  -h, --help       Show this help.

Environment overrides:
  OPENSPRITE_REPO_URL
  OPENSPRITE_BRANCH
  OPENSPRITE_INSTALL_DIR
  OPENSPRITE_HOME
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --branch)
      BRANCH="$2"
      shift 2
      ;;
    --repo)
      REPO_URL="$2"
      shift 2
      ;;
    --dev)
      INSTALL_DEV=1
      shift
      ;;
    --start)
      START_SERVICE=1
      shift
      ;;
    --no-link)
      CREATE_LINK=0
      shift
      ;;
    --no-system)
      INSTALL_SYSTEM_PACKAGES=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      log_error "Unknown option: $1"
      usage >&2
      exit 2
      ;;
  esac
done

detect_os() {
  case "$(uname -s)" in
    Linux*) ;;
    *)
      log_error "This installer currently supports Linux only. Use the manual venv install steps on other platforms."
      exit 1
      ;;
  esac
}

install_system_packages() {
  if [[ "$INSTALL_SYSTEM_PACKAGES" -ne 1 ]]; then
    return 0
  fi
  if ! command -v apt-get >/dev/null 2>&1; then
    log_warn "apt-get not found; skipping system package installation."
    return 0
  fi

  local sudo_cmd=()
  if [[ "$(id -u)" -ne 0 ]]; then
    if ! command -v sudo >/dev/null 2>&1; then
      log_warn "sudo not found; skipping system package installation."
      return 0
    fi
    sudo_cmd=(sudo)
  fi

  export DEBIAN_FRONTEND=noninteractive
  export NEEDRESTART_MODE=a
  log_info "Installing Debian/Ubuntu system packages"
  "${sudo_cmd[@]}" apt-get update
  "${sudo_cmd[@]}" apt-get install -y git python3 python3-venv python3-pip nodejs npm
}

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      if "$candidate" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
      then
        printf '%s' "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

ensure_git() {
  if command -v git >/dev/null 2>&1; then
    return 0
  fi
  log_error "git is required but was not found. Install git and re-run this installer."
  exit 1
}

clone_or_update_repo() {
  mkdir -p "$(dirname "$INSTALL_DIR")"
  if [[ -d "$INSTALL_DIR/.git" ]]; then
    log_info "Updating existing checkout: $INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch origin
    git -C "$INSTALL_DIR" checkout "$BRANCH"
    git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    return 0
  fi

  if [[ -e "$INSTALL_DIR" ]]; then
    log_error "Install path exists but is not a git checkout: $INSTALL_DIR"
    exit 1
  fi

  log_info "Cloning OpenSprite into $INSTALL_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
}

install_python_package() {
  local python_bin="$1"
  cd "$INSTALL_DIR"
  if [[ ! -d ".venv" ]]; then
    log_info "Creating virtual environment"
    "$python_bin" -m venv .venv
  fi

  log_info "Installing OpenSprite"
  .venv/bin/python -m pip install --upgrade pip
  if [[ "$INSTALL_DEV" -eq 1 ]]; then
    .venv/bin/python -m pip install -e ".[dev]"
  else
    .venv/bin/python -m pip install -e .
  fi
}

setup_command_link() {
  if [[ "$CREATE_LINK" -ne 1 ]]; then
    return 0
  fi
  local link_dir="$HOME/.local/bin"
  mkdir -p "$link_dir"
  ln -sfn "$INSTALL_DIR/.venv/bin/opensprite" "$link_dir/opensprite"
  log_success "Linked opensprite -> $link_dir/opensprite"

  case ":$PATH:" in
    *":$link_dir:"*) ;;
    *)
      log_warn "$link_dir is not on PATH for this shell."
      log_info 'Add this to your shell profile: export PATH="$HOME/.local/bin:$PATH"'
      ;;
  esac
}

verify_install() {
  log_info "Verifying CLI"
  "$INSTALL_DIR/.venv/bin/opensprite" --version
}

maybe_start_service() {
  if [[ "$START_SERVICE" -ne 1 ]]; then
    return 0
  fi
  log_info "Starting OpenSprite background gateway"
  "$INSTALL_DIR/.venv/bin/opensprite" service start
  "$INSTALL_DIR/.venv/bin/opensprite" service status
}

print_success() {
  cat <<EOF

OpenSprite installed successfully.

Code: $INSTALL_DIR
Data: $APP_HOME

Commands:
  opensprite service start
  opensprite service status
  opensprite service stop

Logs:
  tail -f ~/.opensprite/logs/gateway.log

If 'opensprite' is not found, run:
  export PATH="\$HOME/.local/bin:\$PATH"

EOF
}

main() {
  log_info "OpenSprite installer"
  detect_os
  install_system_packages
  ensure_git

  local python_bin
  if ! python_bin="$(find_python)"; then
    log_error "Python $PYTHON_VERSION_MIN+ is required. Install Python 3.11+ and re-run this installer."
    exit 1
  fi
  log_success "Using $($python_bin --version)"

  clone_or_update_repo
  install_python_package "$python_bin"
  setup_command_link
  verify_install
  maybe_start_service
  print_success
}

main
