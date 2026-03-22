#!/bin/bash
# eviStream - Stop entire stack
# Kills the tmux session and stops Celery workers

# Script is at backend/deploy/stop.sh — resolve to project root (parent of backend/)
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"

echo "Stopping eviStream stack..."

# Stop Celery workers
cd "$PROJECT_DIR/backend"
bash stop_workers.sh 2>/dev/null

# Kill tmux session (stops frontend + backend)
tmux kill-session -t evistream 2>/dev/null

echo "eviStream stopped."
