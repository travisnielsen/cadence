#!/usr/bin/env bash
#
# Development Environment Setup Script for Cadence
# Usage: ./devsetup.sh [python_version]
# Example: ./devsetup.sh 3.12
#

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_banner() {
    echo -e "${CYAN}"
    cat << 'EOF'
  ██████╗ █████╗ ██████╗ ███████╗███╗   ██╗ ██████╗███████╗
 ██╔════╝██╔══██╗██╔══██╗██╔════╝████╗  ██║██╔════╝██╔════╝
 ██║     ███████║██║  ██║█████╗  ██╔██╗ ██║██║     █████╗
 ██║     ██╔══██║██║  ██║██╔══╝  ██║╚██╗██║██║     ██╔══╝
 ╚██████╗██║  ██║██████╔╝███████╗██║ ╚████║╚██████╗███████╗
  ╚═════╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚══════╝
EOF
    echo -e "${NC}"
    echo -e "${BLUE}  Cadence - Development Environment Setup${NC}"
    echo ""
}

# Default Python version
DEFAULT_PYTHON_VERSION="3.11"
PYTHON_VERSION="${1:-$DEFAULT_PYTHON_VERSION}"

logger() {
    local level=$1 color=$2 msg=$3
    echo -e "${color}$(date '+%Y-%m-%d %H:%M:%S') [${level}]${NC} ${msg}"
}
info()    { logger "INF" "$BLUE"   "$1"; }
success() { logger "SUC" "$GREEN"  "$1"; }
warn()    { logger "WRN" "$YELLOW" "$1"; }
error()   { logger "ERR" "$RED"    "$1" >&2; }

cleanup() {
    local exit_code=$?
    if [ $exit_code -ne 0 ]; then
        error "Setup failed with exit code $exit_code"
        error "Check the output above for details"
    fi
    exit $exit_code
}
trap cleanup EXIT

command_exists() {
    command -v "$1" &> /dev/null
}

validate_python_version() {
    if [[ ! "$1" =~ ^3\.1[1-4]$ ]]; then
        error "Invalid Python version: $1"
        error "Supported versions: 3.11, 3.12, 3.13, 3.14"
        exit 1
    fi
}

check_prerequisites() {
    info "Checking prerequisites..."

    if ! command_exists uv; then
        error "uv is not installed"
        echo ""
        echo "Install uv with:"
        echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
        echo ""
        exit 1
    fi

    success "uv found: $(uv --version)"
}

install_python_versions() {
    info "Installing Python versions (3.11, 3.12, 3.13)..."

    if ! uv python install 3.11 3.12 3.13; then
        error "Failed to install Python versions"
        exit 1
    fi

    success "Python versions installed"
}

create_virtual_environment() {
    info "Creating virtual environment with Python $PYTHON_VERSION..."

    if [ -d ".venv" ]; then
        warn "Existing .venv directory found, removing..."
        rm -rf .venv
    fi

    if ! uv venv --python "$PYTHON_VERSION"; then
        error "Failed to create virtual environment"
        exit 1
    fi

    success "Virtual environment created"
}

install_dependencies() {
    info "Installing dependencies..."

    if ! uv sync --all-extras --dev; then
        error "Failed to install dependencies"
        exit 1
    fi

    success "Dependencies installed"
}

setup_git_hooks() {
    info "Configuring git hooks..."

    if [ -d ".githooks" ]; then
        chmod +x .githooks/* 2>/dev/null || true

        if git rev-parse --git-dir &> /dev/null; then
            git config core.hooksPath .githooks
            success "Git hooks configured (.githooks/)"
        else
            warn "Not a git repository, skipping git hooks configuration"
        fi
    else
        warn ".githooks/ directory not found, skipping custom hooks"
    fi
}

install_beads() {
    info "Setting up Beads task tracker..."

    if command_exists bd; then
        success "bd already installed: $(bd version 2>/dev/null || echo 'unknown')"
    else
        info "Installing beads (bd)..."
        local install_dir="$HOME/.local/bin"
        mkdir -p "$install_dir"

        if curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash 2>/dev/null; then
            success "Beads installed via install script"
            if [[ ":$PATH:" != *":$install_dir:"* ]]; then
                warn "Add $install_dir to your PATH if bd command is not found"
            fi
        else
            warn "Failed to install beads (non-critical, can be installed manually)"
            echo "  Install: curl -fsSL https://raw.githubusercontent.com/steveyegge/beads/main/scripts/install.sh | bash"
        fi
    fi

    if [ ! -d ".beads" ]; then
        if command_exists bd; then
            info "Initializing beads in workspace..."
            if echo N | bd init --quiet --prefix cad --branch beads-sync 2>/dev/null; then
                success "Beads initialized with prefix 'cad' and sync branch 'beads-sync'"
            else
                warn "Failed to initialize beads (non-critical)"
            fi
        fi
    fi
}

setup_frontend() {
    info "Checking frontend dependencies..."

    if command_exists node; then
        success "Node.js found: $(node --version)"
        if [ -d "src/frontend" ]; then
            info "Installing frontend dependencies..."
            cd src/frontend && npm install && cd ../..
            success "Frontend dependencies installed"
        fi
    else
        warn "Node.js not found - frontend dependencies not installed"
        echo "  Install Node.js: https://nodejs.org/"
    fi
}

# ============================================================================
# Main
# ============================================================================

main() {
    print_banner
    validate_python_version "$PYTHON_VERSION"
    check_prerequisites
    install_python_versions
    create_virtual_environment
    install_dependencies
    setup_git_hooks
    install_beads
    setup_frontend

    echo ""
    success "================================================"
    success "  Development environment setup complete!"
    success "================================================"
    echo ""
    echo -e "  Python:    ${GREEN}$PYTHON_VERSION${NC}"
    echo -e "  Venv:      ${GREEN}.venv${NC}"
    echo -e "  Run:       ${CYAN}uv run poe <task>${NC}"
    echo ""
    echo -e "  ${YELLOW}Available tasks:${NC}"
    echo "    uv run poe check     - Run all quality checks"
    echo "    uv run poe test      - Run tests"
    echo "    uv run poe format    - Format and lint"
    echo "    uv run poe dev-api   - Start FastAPI dev server"
    echo ""
}

main "$@"
