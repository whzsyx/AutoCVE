#!/bin/bash
#
# AuditAI 安全工具一键安装脚本 (增强版)
# 自动安装沙盒和外部安全扫描工具
#
# 特性:
# - 多种安装方式自动回退
# - 网络问题自动重试
# - 详细的错误诊断
# - 支持代理设置
# - 虚拟环境兼容
#

set -e

# ============================================================
# 配置
# ============================================================

# 版本配置
GITLEAKS_VERSION="8.18.4"
OSV_SCANNER_VERSION="1.8.3"
TRUFFLEHOG_VERSION="3.80.0"

# 重试配置
MAX_RETRIES=3
RETRY_DELAY=2

# 超时配置
DOWNLOAD_TIMEOUT=60
INSTALL_TIMEOUT=120

# 获取脚本目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 工具安装目录
TOOLS_DIR="$HOME/.local/bin"
mkdir -p "$TOOLS_DIR"

# ============================================================
# 颜色和日志
# ============================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}→${NC} $1"; }
log_success() { echo -e "${GREEN}✓${NC} $1"; }
log_warning() { echo -e "${YELLOW}!${NC} $1"; }
log_error()   { echo -e "${RED}✗${NC} $1"; }
log_debug()   { [[ "$VERBOSE" == "1" ]] && echo -e "${CYAN}  $1${NC}"; }

log_header() {
    echo ""
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  $1${NC}"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ============================================================
# 工具函数
# ============================================================

# 检查命令是否存在且可执行
command_exists() {
    command -v "$1" &> /dev/null || return 1
    # 额外检查：确保命令真的能运行（排除 pyenv shim 等假阳性）
    case "$1" in
        semgrep)  "$1" --version &> /dev/null ;;
        bandit)   "$1" --version &> /dev/null ;;
        safety)   "$1" --version &> /dev/null ;;
        gitleaks) "$1" version &> /dev/null ;;
        osv-scanner) "$1" --version &> /dev/null ;;
        trufflehog)  "$1" --version &> /dev/null ;;
        *) return 0 ;;
    esac
}

# 检测操作系统
detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        OS="macos"
        ARCH=$(uname -m)
        if [[ "$ARCH" == "arm64" ]]; then
            ARCH_TYPE="arm64"
        else
            ARCH_TYPE="x64"
        fi
    elif [[ -f /etc/debian_version ]]; then
        OS="debian"
        ARCH_TYPE=$(dpkg --print-architecture 2>/dev/null || echo "amd64")
    elif [[ -f /etc/redhat-release ]]; then
        OS="redhat"
        ARCH_TYPE=$(uname -m)
    else
        OS="linux"
        ARCH_TYPE=$(uname -m)
    fi

    # 标准化架构名称
    case "$ARCH_TYPE" in
        x86_64|amd64) ARCH_TYPE="x64" ;;
        aarch64|arm64) ARCH_TYPE="arm64" ;;
    esac

    log_info "检测到系统: $OS ($ARCH_TYPE)"
}

# 检测 Python 环境
detect_python() {
    PYTHON_CMD=""
    PIP_CMD=""

    # 优先使用虚拟环境
    if [[ -n "$VIRTUAL_ENV" ]]; then
        log_info "检测到虚拟环境: $VIRTUAL_ENV"
        PYTHON_CMD="python"
        PIP_CMD="pip"
    # 检查 python3
    elif command_exists python3; then
        PYTHON_CMD="python3"
        if command_exists pip3; then
            PIP_CMD="pip3"
        else
            PIP_CMD="python3 -m pip"
        fi
    # 检查 python
    elif command_exists python; then
        PYTHON_CMD="python"
        if command_exists pip; then
            PIP_CMD="pip"
        else
            PIP_CMD="python -m pip"
        fi
    else
        log_error "未找到 Python！请先安装 Python 3.8+"
        return 1
    fi

    # 验证 Python 版本
    PYTHON_VERSION=$($PYTHON_CMD --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
    log_info "Python 版本: $PYTHON_VERSION (命令: $PYTHON_CMD)"
    log_debug "pip 命令: $PIP_CMD"

    # 确保 pip 可用
    if ! $PIP_CMD --version &>/dev/null; then
        log_warning "pip 不可用，尝试安装..."
        $PYTHON_CMD -m ensurepip --upgrade 2>/dev/null || true
    fi

    return 0
}

# 带重试的下载函数
download_with_retry() {
    local url="$1"
    local output="$2"
    local description="$3"

    for attempt in $(seq 1 $MAX_RETRIES); do
        log_info "下载 $description (尝试 $attempt/$MAX_RETRIES)..."

        if command_exists curl; then
            if curl -fsSL --connect-timeout 10 --max-time $DOWNLOAD_TIMEOUT -o "$output" "$url" 2>/dev/null; then
                log_success "$description 下载成功"
                return 0
            fi
        elif command_exists wget; then
            if wget -q --timeout=$DOWNLOAD_TIMEOUT -O "$output" "$url" 2>/dev/null; then
                log_success "$description 下载成功"
                return 0
            fi
        else
            log_error "未找到 curl 或 wget"
            return 1
        fi

        log_warning "下载失败，${RETRY_DELAY}秒后重试..."
        sleep $RETRY_DELAY
    done

    log_error "$description 下载失败 (已重试 $MAX_RETRIES 次)"
    return 1
}

# 带重试的 pip 安装
pip_install_with_retry() {
    local package="$1"

    for attempt in $(seq 1 $MAX_RETRIES); do
        log_info "安装 $package (尝试 $attempt/$MAX_RETRIES)..."

        # 尝试方式 1: 普通安装
        if $PIP_CMD install "$package" --timeout 60 2>&1; then
            log_success "$package 安装成功"
            return 0
        fi

        # 尝试方式 2: --user 标志
        log_debug "尝试 --user 安装..."
        if $PIP_CMD install "$package" --user --timeout 60 2>&1; then
            log_success "$package 安装成功 (--user)"
            return 0
        fi

        # 尝试方式 3: --break-system-packages (Python 3.11+ PEP 668)
        log_debug "尝试 --break-system-packages..."
        if $PIP_CMD install "$package" --break-system-packages --timeout 60 2>&1; then
            log_success "$package 安装成功 (--break-system-packages)"
            return 0
        fi

        # 尝试升级 pip 后重试
        if [[ $attempt -eq 1 ]]; then
            log_debug "升级 pip 后重试..."
            $PIP_CMD install --upgrade pip --quiet 2>/dev/null || true
        fi

        sleep $RETRY_DELAY
    done

    # 尝试方式 4: 使用 pipx (推荐的 CLI 工具安装方式)
    if command -v pipx &> /dev/null; then
        log_info "尝试使用 pipx 安装 $package..."
        if pipx install "$package" 2>&1; then
            log_success "$package 安装成功 (pipx)"
            return 0
        fi
    fi

    log_error "$package 安装失败"
    return 1
}

# 添加到 PATH
add_to_path() {
    local dir="$1"

    # 当前会话
    if [[ ":$PATH:" != *":$dir:"* ]]; then
        export PATH="$dir:$PATH"
    fi

    # 持久化到 shell 配置
    local shell_rc=""
    if [[ -f "$HOME/.zshrc" ]]; then
        shell_rc="$HOME/.zshrc"
    elif [[ -f "$HOME/.bashrc" ]]; then
        shell_rc="$HOME/.bashrc"
    elif [[ -f "$HOME/.bash_profile" ]]; then
        shell_rc="$HOME/.bash_profile"
    fi

    if [[ -n "$shell_rc" ]]; then
        if ! grep -q "$dir" "$shell_rc" 2>/dev/null; then
            echo "export PATH=\"$dir:\$PATH\"" >> "$shell_rc"
            log_debug "已添加 $dir 到 $shell_rc"
        fi
    fi
}

# ============================================================
# Python 工具安装
# ============================================================

install_python_tools() {
    log_header "安装 Python 安全工具"

    detect_python || return 1

    local tools=("bandit" "safety")
    local failed=()
    local installed=()

    # Semgrep 单独处理（较大）
    log_info "检查 semgrep..."
    if command_exists semgrep; then
        log_success "semgrep 已安装: $(semgrep --version 2>&1 | head -1)"
    else
        # 尝试 pip 安装
        if pip_install_with_retry "semgrep"; then
            installed+=("semgrep")
        # macOS: 尝试 brew 安装
        elif [[ "$OS" == "macos" ]] && command -v brew &> /dev/null; then
            log_info "pip 安装失败，尝试 brew install semgrep..."
            if brew install semgrep 2>&1; then
                installed+=("semgrep")
                log_success "semgrep 安装成功 (brew)"
            else
                failed+=("semgrep")
            fi
        else
            failed+=("semgrep")
            log_warning "semgrep 安装失败，可尝试: brew install semgrep (macOS)"
        fi
    fi

    # 安装其他工具
    for tool in "${tools[@]}"; do
        log_info "检查 $tool..."
        if command_exists "$tool"; then
            log_success "$tool 已安装"
        else
            if pip_install_with_retry "$tool"; then
                installed+=("$tool")
            else
                failed+=("$tool")
            fi
        fi
    done

    # 可选: TruffleHog
    if [[ "$INSTALL_OPTIONAL" == "1" ]] || [[ "$INTERACTIVE" == "1" ]]; then
        if [[ "$INTERACTIVE" == "1" ]]; then
            read -p "是否安装 TruffleHog (高级密钥扫描，约100MB)? [y/N] " -n 1 -r
            echo
        fi
        if [[ "$INSTALL_OPTIONAL" == "1" ]] || [[ $REPLY =~ ^[Yy]$ ]]; then
            if command_exists trufflehog; then
                log_success "trufflehog 已安装"
            else
                pip_install_with_retry "trufflehog" || failed+=("trufflehog")
            fi
        fi
    fi

    # 报告结果
    echo ""
    if [[ ${#installed[@]} -gt 0 ]]; then
        log_success "已安装: ${installed[*]}"
    fi
    if [[ ${#failed[@]} -gt 0 ]]; then
        log_warning "安装失败: ${failed[*]}"
        log_info "💡 提示: 可尝试使用 pipx 安装: pipx install <package>"
        log_info "   或使用虚拟环境: python3 -m venv venv && source venv/bin/activate && pip install <package>"
        return 1
    fi

    return 0
}

# ============================================================
# 系统工具安装 (macOS)
# ============================================================

install_macos_tools() {
    log_header "安装 macOS 系统工具"

    local failed=()
    local installed=()

    # 检查/安装 Homebrew
    if ! command_exists brew; then
        log_warning "Homebrew 未安装"

        if [[ "$INTERACTIVE" == "1" ]]; then
            read -p "是否安装 Homebrew? [Y/n] " -n 1 -r
            echo
            [[ $REPLY =~ ^[Nn]$ ]] && return 1
        fi

        log_info "安装 Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" || {
            log_error "Homebrew 安装失败"
            log_info "尝试使用二进制方式安装工具..."
            install_binary_tools
            return $?
        }

        # 配置 Homebrew PATH (Apple Silicon)
        if [[ -f "/opt/homebrew/bin/brew" ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        fi
    fi

    log_success "Homebrew 可用: $(brew --version | head -1)"

    # Gitleaks
    log_info "检查 gitleaks..."
    if command_exists gitleaks; then
        log_success "gitleaks 已安装: $(gitleaks version 2>&1 | head -1)"
    else
        log_info "安装 gitleaks..."
        if brew install gitleaks 2>/dev/null; then
            installed+=("gitleaks")
            log_success "gitleaks 安装成功"
        else
            log_warning "brew 安装失败，尝试二进制安装..."
            install_gitleaks_binary || failed+=("gitleaks")
        fi
    fi

    # OSV-Scanner
    log_info "检查 osv-scanner..."
    if command_exists osv-scanner; then
        log_success "osv-scanner 已安装"
    else
        log_info "安装 osv-scanner..."
        if brew install osv-scanner 2>/dev/null; then
            installed+=("osv-scanner")
            log_success "osv-scanner 安装成功"
        else
            log_warning "brew 安装失败，尝试二进制安装..."
            install_osv_scanner_binary || failed+=("osv-scanner")
        fi
    fi

    # 可选: TruffleHog (brew)
    if [[ "$INSTALL_OPTIONAL" == "1" ]]; then
        if ! command_exists trufflehog; then
            log_info "安装 trufflehog (brew)..."
            brew install trufflehog 2>/dev/null || log_warning "trufflehog brew 安装失败"
        fi
    fi

    # 报告结果
    echo ""
    if [[ ${#installed[@]} -gt 0 ]]; then
        log_success "已安装: ${installed[*]}"
    fi
    if [[ ${#failed[@]} -gt 0 ]]; then
        log_warning "安装失败: ${failed[*]}"
        return 1
    fi

    return 0
}

# ============================================================
# 二进制工具安装 (回退方案)
# ============================================================

install_binary_tools() {
    log_info "使用二进制方式安装工具..."

    install_gitleaks_binary
    install_osv_scanner_binary
}

install_gitleaks_binary() {
    log_info "下载 Gitleaks 二进制..."

    local arch_suffix=""
    case "$OS-$ARCH_TYPE" in
        macos-x64)   arch_suffix="darwin_x64" ;;
        macos-arm64) arch_suffix="darwin_arm64" ;;
        *-x64)       arch_suffix="linux_x64" ;;
        *-arm64)     arch_suffix="linux_arm64" ;;
        *)           arch_suffix="linux_x64" ;;
    esac

    local url="https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_${arch_suffix}.tar.gz"
    local tmp_file="/tmp/gitleaks.tar.gz"

    if download_with_retry "$url" "$tmp_file" "Gitleaks"; then
        tar -xzf "$tmp_file" -C "$TOOLS_DIR" gitleaks 2>/dev/null || {
            # 某些版本可能没有子目录
            tar -xzf "$tmp_file" -C "/tmp" 2>/dev/null
            mv /tmp/gitleaks "$TOOLS_DIR/" 2>/dev/null || true
        }
        chmod +x "$TOOLS_DIR/gitleaks"
        rm -f "$tmp_file"
        add_to_path "$TOOLS_DIR"
        log_success "Gitleaks 二进制安装成功"
        return 0
    fi

    return 1
}

install_osv_scanner_binary() {
    log_info "下载 OSV-Scanner 二进制..."

    local arch_suffix=""
    case "$OS-$ARCH_TYPE" in
        macos-x64)   arch_suffix="darwin_amd64" ;;
        macos-arm64) arch_suffix="darwin_arm64" ;;
        *-x64)       arch_suffix="linux_amd64" ;;
        *-arm64)     arch_suffix="linux_arm64" ;;
        *)           arch_suffix="linux_amd64" ;;
    esac

    local url="https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_${arch_suffix}"
    local target="$TOOLS_DIR/osv-scanner"

    if download_with_retry "$url" "$target" "OSV-Scanner"; then
        chmod +x "$target"
        add_to_path "$TOOLS_DIR"
        log_success "OSV-Scanner 二进制安装成功"
        return 0
    fi

    return 1
}

install_trufflehog_binary() {
    log_info "下载 TruffleHog 二进制..."

    local arch_suffix=""
    case "$OS-$ARCH_TYPE" in
        macos-x64)   arch_suffix="darwin_amd64" ;;
        macos-arm64) arch_suffix="darwin_arm64" ;;
        *-x64)       arch_suffix="linux_amd64" ;;
        *-arm64)     arch_suffix="linux_arm64" ;;
        *)           arch_suffix="linux_amd64" ;;
    esac

    local url="https://github.com/trufflesecurity/trufflehog/releases/download/v${TRUFFLEHOG_VERSION}/trufflehog_${TRUFFLEHOG_VERSION}_${arch_suffix}.tar.gz"
    local tmp_file="/tmp/trufflehog.tar.gz"

    if download_with_retry "$url" "$tmp_file" "TruffleHog"; then
        tar -xzf "$tmp_file" -C "$TOOLS_DIR" trufflehog 2>/dev/null || {
            tar -xzf "$tmp_file" -C "/tmp" 2>/dev/null
            mv /tmp/trufflehog "$TOOLS_DIR/" 2>/dev/null || true
        }
        chmod +x "$TOOLS_DIR/trufflehog"
        rm -f "$tmp_file"
        add_to_path "$TOOLS_DIR"
        log_success "TruffleHog 二进制安装成功"
        return 0
    fi

    return 1
}

# ============================================================
# Linux 工具安装
# ============================================================

install_linux_tools() {
    log_header "安装 Linux 系统工具"

    # 直接使用二进制安装（最可靠）
    install_binary_tools
}

# ============================================================
# Docker 沙盒安装
# ============================================================

install_docker_sandbox() {
    log_header "配置 Docker 沙盒"

    # 检查 Docker
    if ! command_exists docker; then
        log_error "Docker 未安装！"
        log_info "macOS: brew install --cask docker"
        log_info "Linux: https://docs.docker.com/engine/install/"
        return 1
    fi

    # 检查 Docker 是否运行
    if ! docker info &> /dev/null; then
        log_error "Docker 未运行！请启动 Docker。"

        # macOS: 尝试启动 Docker Desktop
        if [[ "$OS" == "macos" ]]; then
            log_info "尝试启动 Docker Desktop..."
            open -a Docker 2>/dev/null || true

            log_info "等待 Docker 启动 (最多 60 秒)..."
            for i in {1..12}; do
                sleep 5
                if docker info &> /dev/null; then
                    log_success "Docker 已启动"
                    break
                fi
                echo -n "."
            done
            echo ""

            if ! docker info &> /dev/null; then
                log_error "Docker 启动超时，请手动启动 Docker Desktop"
                return 1
            fi
        else
            return 1
        fi
    fi

    log_success "Docker 已运行"

    # 构建沙盒镜像
    local sandbox_dir="$PROJECT_ROOT/docker/sandbox"
    local dockerfile="$sandbox_dir/Dockerfile"

    if [[ ! -f "$dockerfile" ]]; then
        log_warning "沙盒 Dockerfile 不存在，创建默认配置..."
        mkdir -p "$sandbox_dir"
        create_sandbox_dockerfile "$sandbox_dir"
    fi

    log_info "构建 AuditAI 沙盒镜像..."

    cd "$sandbox_dir"

    # 带重试的构建
    for attempt in $(seq 1 $MAX_RETRIES); do
        log_info "构建镜像 (尝试 $attempt/$MAX_RETRIES)..."

        if docker build -t auditai-sandbox:latest -f Dockerfile . 2>&1; then
            log_success "沙盒镜像构建成功: auditai-sandbox:latest"

            # 验证
            log_info "验证沙盒镜像..."
            if docker run --rm auditai-sandbox:latest python3 --version; then
                log_success "Python 环境正常"
            fi
            if docker run --rm auditai-sandbox:latest node --version 2>/dev/null; then
                log_success "Node.js 环境正常"
            fi

            return 0
        fi

        log_warning "构建失败，重试..."
        sleep $RETRY_DELAY
    done

    log_error "沙盒镜像构建失败"
    return 1
}

create_sandbox_dockerfile() {
    local dir="$1"

    cat > "$dir/Dockerfile" << 'EOF'
# AuditAI 安全沙盒
FROM python:3.11-slim-bookworm

# 安装基础工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl wget netcat-openbsd dnsutils iputils-ping ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# 创建非特权用户
RUN groupadd -g 1000 sandbox \
    && useradd -u 1000 -g sandbox -m -s /bin/bash sandbox

# 安装 Python 安全测试库
RUN pip install --no-cache-dir \
    requests httpx aiohttp beautifulsoup4 lxml \
    pycryptodome paramiko pyjwt python-jose sqlparse

# 设置工作目录
WORKDIR /workspace
RUN mkdir -p /workspace /tmp/sandbox \
    && chown -R sandbox:sandbox /workspace /tmp/sandbox

USER sandbox
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/home/sandbox
CMD ["/bin/bash"]
EOF
    log_success "已创建沙盒 Dockerfile"
}

# ============================================================
# 验证安装
# ============================================================

verify_installation() {
    log_header "验证安装结果"

    local tools=(
        "semgrep:Semgrep 静态分析"
        "bandit:Bandit Python安全"
        "safety:Safety 依赖漏洞"
        "gitleaks:Gitleaks 密钥检测"
        "osv-scanner:OSV-Scanner 漏洞"
        "trufflehog:TruffleHog 密钥"
        "npm:NPM Audit"
        "docker:Docker"
    )

    local installed=0
    local total=${#tools[@]}

    echo ""
    printf "%-18s %-12s %-30s\n" "工具" "状态" "版本/路径"
    echo "────────────────────────────────────────────────────────────"

    for tool_info in "${tools[@]}"; do
        IFS=':' read -r tool desc <<< "$tool_info"

        if command_exists "$tool"; then
            local version=""
            case "$tool" in
                semgrep)     version=$(semgrep --version 2>&1 | head -1) ;;
                bandit)      version=$(bandit --version 2>&1 | head -1) ;;
                safety)      version=$(safety --version 2>&1 | head -1) ;;
                gitleaks)    version=$(gitleaks version 2>&1 | head -1) ;;
                osv-scanner) version=$(osv-scanner --version 2>&1 | head -1) ;;
                trufflehog)  version=$(trufflehog --version 2>&1 | head -1) ;;
                npm)         version=$(npm --version 2>&1) ;;
                docker)      version=$(docker --version 2>&1 | cut -d' ' -f3) ;;
            esac
            version="${version:0:28}"
            printf "%-18s ${GREEN}%-12s${NC} %-30s\n" "$tool" "已安装" "$version"
            ((installed++))
        else
            printf "%-18s ${YELLOW}%-12s${NC} %-30s\n" "$tool" "未安装" "-"
        fi
    done

    echo "────────────────────────────────────────────────────────────"

    # Docker 沙盒检查
    if command_exists docker && docker info &>/dev/null; then
        if docker image inspect auditai-sandbox:latest &>/dev/null; then
            echo ""
            log_success "Docker 沙盒镜像: auditai-sandbox:latest ✓"
        else
            echo ""
            log_warning "Docker 沙盒镜像未构建"
        fi
    fi

    echo ""
    log_info "安装统计: $installed/$total 个工具可用"

    # 检查 PATH
    if [[ ":$PATH:" != *":$TOOLS_DIR:"* ]]; then
        log_warning "请重启终端或运行: source ~/.zshrc (或 ~/.bashrc)"
    fi

    if [[ $installed -ge 5 ]]; then
        log_success "核心安全工具已就绪！"
        return 0
    else
        log_warning "部分工具未安装，某些功能可能受限"
        return 1
    fi
}

# ============================================================
# 更新环境配置
# ============================================================

update_env_config() {
    log_header "更新环境配置"

    local env_file="$PROJECT_ROOT/backend/.env"

    if [[ ! -f "$env_file" ]]; then
        log_warning ".env 文件不存在，跳过配置更新"
        return 0
    fi

    if grep -q "SANDBOX_IMAGE" "$env_file"; then
        log_info "沙盒配置已存在于 .env 文件中"
    else
        log_info "添加沙盒配置到 .env 文件..."
        cat >> "$env_file" << 'EOF'

# =============================================
# 沙盒配置 (自动添加)
# =============================================
SANDBOX_IMAGE=auditai-sandbox:latest
SANDBOX_MEMORY_LIMIT=512m
SANDBOX_CPU_LIMIT=1.0
SANDBOX_TIMEOUT=60
SANDBOX_NETWORK_MODE=none
EOF
        log_success "沙盒配置已添加到 .env"
    fi
}

# ============================================================
# 显示帮助
# ============================================================

show_help() {
    cat << 'EOF'
AuditAI 安全工具一键安装脚本

用法:
    ./setup_security_tools.sh [选项]

选项:
    -a, --all           全部安装 (默认交互式)
    -p, --python        仅安装 Python 工具
    -s, --system        仅安装系统工具
    -d, --docker        仅构建 Docker 沙盒
    -v, --verify        仅验证安装状态
    -o, --optional      包含可选工具 (TruffleHog)
    --verbose           显示详细输出
    -h, --help          显示帮助

示例:
    ./setup_security_tools.sh              # 交互式安装
    ./setup_security_tools.sh -a           # 自动全部安装
    ./setup_security_tools.sh -a -o        # 全部安装 + 可选工具
    ./setup_security_tools.sh -v           # 仅检查状态
EOF
}

# ============================================================
# 主函数
# ============================================================

main() {
    # 解析参数
    INTERACTIVE="1"
    INSTALL_ALL=""
    INSTALL_PYTHON=""
    INSTALL_SYSTEM=""
    INSTALL_DOCKER=""
    VERIFY_ONLY=""
    INSTALL_OPTIONAL=""
    VERBOSE=""

    while [[ $# -gt 0 ]]; do
        case $1 in
            -a|--all)     INSTALL_ALL="1"; INTERACTIVE="" ;;
            -p|--python)  INSTALL_PYTHON="1"; INTERACTIVE="" ;;
            -s|--system)  INSTALL_SYSTEM="1"; INTERACTIVE="" ;;
            -d|--docker)  INSTALL_DOCKER="1"; INTERACTIVE="" ;;
            -v|--verify)  VERIFY_ONLY="1"; INTERACTIVE="" ;;
            -o|--optional) INSTALL_OPTIONAL="1" ;;
            --verbose)    VERBOSE="1" ;;
            -h|--help)    show_help; exit 0 ;;
            *)            log_error "未知选项: $1"; show_help; exit 1 ;;
        esac
        shift
    done

    # 显示标题
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║                                                               ║${NC}"
    echo -e "${BLUE}║     🔐 AuditAI 安全工具一键安装脚本 (增强版)               ║${NC}"
    echo -e "${BLUE}║                                                               ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""

    # 检测系统
    detect_os

    # 仅验证模式
    if [[ "$VERIFY_ONLY" == "1" ]]; then
        verify_installation
        exit $?
    fi

    # 自动安装模式
    if [[ "$INSTALL_ALL" == "1" ]]; then
        install_python_tools
        if [[ "$OS" == "macos" ]]; then
            install_macos_tools
        else
            install_linux_tools
        fi
        install_docker_sandbox
        update_env_config
        verify_installation
        exit $?
    fi

    # 单独安装模式
    if [[ "$INSTALL_PYTHON" == "1" ]]; then
        install_python_tools
        verify_installation
        exit $?
    fi

    if [[ "$INSTALL_SYSTEM" == "1" ]]; then
        if [[ "$OS" == "macos" ]]; then
            install_macos_tools
        else
            install_linux_tools
        fi
        verify_installation
        exit $?
    fi

    if [[ "$INSTALL_DOCKER" == "1" ]]; then
        install_docker_sandbox
        update_env_config
        verify_installation
        exit $?
    fi

    # 交互式模式
    echo "请选择要安装的组件:"
    echo "  1) 全部安装 (推荐)"
    echo "  2) 仅 Python 工具 (pip)"
    echo "  3) 仅系统工具 (brew/binary)"
    echo "  4) 仅 Docker 沙盒"
    echo "  5) 仅验证安装状态"
    echo "  6) 退出"
    echo ""
    read -p "请输入选项 [1-6]: " choice

    case $choice in
        1)
            install_python_tools
            if [[ "$OS" == "macos" ]]; then
                install_macos_tools
            else
                install_linux_tools
            fi
            install_docker_sandbox
            update_env_config
            verify_installation
            ;;
        2) install_python_tools; verify_installation ;;
        3)
            if [[ "$OS" == "macos" ]]; then
                install_macos_tools
            else
                install_linux_tools
            fi
            verify_installation
            ;;
        4) install_docker_sandbox; update_env_config; verify_installation ;;
        5) verify_installation ;;
        6) echo "退出"; exit 0 ;;
        *) log_error "无效选项"; exit 1 ;;
    esac

    log_header "安装完成"
    echo ""
    echo "下一步操作:"
    echo "  1. 重启终端使 PATH 生效"
    echo "  2. 启动后端: cd backend && uvicorn app.main:app --reload"
    echo "  3. 在 Agent 审计中测试工具"
    echo ""
}

main "$@"
