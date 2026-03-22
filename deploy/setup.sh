#!/bin/bash
# =============================================================================
# eviStream EC2 Setup Script
# Target: Ubuntu 22.04 LTS on AWS EC2 (t3.xlarge recommended)
#
# This script installs all system dependencies, creates the conda environment,
# installs Python and Node.js packages, and configures Redis + Nginx.
#
# Usage:
#   sudo bash backend/deploy/setup.sh
#
# Prerequisites:
#   - Ubuntu 22.04 EC2 instance
#   - Run as root or with sudo
#   - Repo already cloned (this script runs from repo root)
# =============================================================================

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[eviStream Setup]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# Detect repo root (script is at backend/deploy/setup.sh)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
log "Repository root: $REPO_DIR"

# Detect the non-root user (who cloned the repo)
if [ -n "${SUDO_USER:-}" ]; then
    DEPLOY_USER="$SUDO_USER"
else
    DEPLOY_USER="$(stat -c '%U' "$REPO_DIR")"
fi
DEPLOY_HOME=$(eval echo "~$DEPLOY_USER")
log "Deploy user: $DEPLOY_USER (home: $DEPLOY_HOME)"

# ============================================================
# Step 1: System packages
# ============================================================
log "Step 1/7: Installing system packages..."
apt update -qq
apt install -y \
    build-essential \
    libffi-dev \
    libssl-dev \
    tmux \
    nginx \
    redis-server \
    poppler-utils \
    libgl1-mesa-glx \
    libglib2.0-0 \
    curl \
    wget \
    git \
    software-properties-common

log "System packages installed."

# ============================================================
# Step 2: Miniconda (if not already installed)
# ============================================================
CONDA_PATH="$DEPLOY_HOME/miniconda3"
if [ ! -d "$CONDA_PATH" ]; then
    log "Step 2/7: Installing Miniconda..."
    wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
    sudo -u "$DEPLOY_USER" bash /tmp/miniconda.sh -b -p "$CONDA_PATH"
    sudo -u "$DEPLOY_USER" "$CONDA_PATH/bin/conda" init bash
    rm /tmp/miniconda.sh
    log "Miniconda installed at $CONDA_PATH"
else
    log "Step 2/7: Miniconda already installed at $CONDA_PATH, skipping."
fi

# ============================================================
# Step 3: Conda environment + Python packages
# ============================================================
CONDA_BIN="$CONDA_PATH/bin/conda"
if ! sudo -u "$DEPLOY_USER" "$CONDA_BIN" env list | grep -q "topics"; then
    log "Step 3/7: Creating conda env 'topics' (Python 3.11)..."
    sudo -u "$DEPLOY_USER" "$CONDA_BIN" create -n topics python=3.11 -y
else
    log "Step 3/7: Conda env 'topics' already exists, skipping creation."
fi

# Install pip packages
log "Installing Python dependencies from requirements.lock.txt (this may take 10-15 minutes)..."
CONDA_ENV_PATH="$CONDA_PATH/envs/topics"
sudo -u "$DEPLOY_USER" "$CONDA_ENV_PATH/bin/pip" install --no-cache-dir \
    -r "$REPO_DIR/backend/requirements.lock.txt"
log "Python dependencies installed."

# ============================================================
# Step 4: Node.js 22 (if not already installed)
# ============================================================
if ! command -v node &> /dev/null || [[ "$(node --version)" != v22* ]]; then
    log "Step 4/7: Installing Node.js 22..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt install -y nodejs
    log "Node.js $(node --version) installed."
else
    log "Step 4/7: Node.js $(node --version) already installed, skipping."
fi

# ============================================================
# Step 5: Frontend npm packages
# ============================================================
log "Step 5/7: Installing frontend npm packages..."
cd "$REPO_DIR/frontend"
sudo -u "$DEPLOY_USER" npm install
log "Frontend packages installed."

# ============================================================
# Step 6: Redis configuration
# ============================================================
log "Step 6/7: Configuring Redis on port 6380..."
cp "$REPO_DIR/backend/deploy/redis/redis-evistream.conf" /etc/redis/redis.conf
mkdir -p /var/run/redis /var/log/redis /var/lib/redis
chown redis:redis /var/run/redis /var/log/redis /var/lib/redis
systemctl restart redis-server
systemctl enable redis-server

# Verify Redis
if redis-cli -p 6380 ping | grep -q "PONG"; then
    log "Redis is running on port 6380."
else
    error "Redis failed to start on port 6380. Check: sudo systemctl status redis-server"
fi

# ============================================================
# Step 7: Nginx configuration
# ============================================================
log "Step 7/7: Configuring Nginx reverse proxy..."
cp "$REPO_DIR/backend/deploy/nginx/evistream.conf" /etc/nginx/sites-available/evistream
ln -sf /etc/nginx/sites-available/evistream /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test nginx config
if nginx -t 2>/dev/null; then
    systemctl restart nginx
    systemctl enable nginx
    log "Nginx configured and running."
else
    warn "Nginx config test failed. You need to replace YOUR_EC2_PUBLIC_IP in:"
    warn "  /etc/nginx/sites-available/evistream"
fi

# ============================================================
# Done
# ============================================================
echo ""
echo "============================================================"
echo -e "${GREEN}eviStream setup complete!${NC}"
echo "============================================================"
echo ""
echo "Remaining steps:"
echo ""
echo "  1. Update Nginx config with your EC2 public IP:"
echo "     sudo sed -i 's/YOUR_EC2_PUBLIC_IP/<YOUR_IP>/g' /etc/nginx/sites-available/evistream"
echo "     sudo nginx -t && sudo systemctl restart nginx"
echo ""
echo "  2. Create backend .env file:"
echo "     cp $REPO_DIR/backend/.env.example $REPO_DIR/backend/.env"
echo "     # Then edit with your real secrets"
echo ""
echo "  3. Create frontend .env.local file:"
echo "     cp $REPO_DIR/frontend/.env.local.example $REPO_DIR/frontend/.env.local"
echo "     # Set NEXT_PUBLIC_API_URL=http://<YOUR_IP>"
echo ""
echo "  4. Start the stack:"
echo "     source ~/.bashrc && conda activate topics"
echo "     cd $REPO_DIR && bash backend/deploy/start.sh"
echo ""
echo "  5. Verify:"
echo "     curl http://localhost:8001/health"
echo "     curl http://<YOUR_IP>/health"
echo "============================================================"
