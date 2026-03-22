#!/bin/bash
# eviStream - Start entire stack in a single tmux session
# Usage: bash start.sh
#   - Starts frontend (Next.js), backend (FastAPI), and Celery workers
#   - All run in a single tmux session with named panes
#   - Use: tmux attach -t evistream   to re-attach
#   - Use: bash stop.sh               to stop everything

# Script is at backend/deploy/start.sh — resolve to project root (parent of backend/)
PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
SESSION="evistream"
CONDA_ENV="topics"

# Kill existing session if running
tmux kill-session -t "$SESSION" 2>/dev/null

echo "Starting eviStream stack..."

# Create tmux session with the frontend pane
tmux new-session -d -s "$SESSION" -n "stack" -c "$PROJECT_DIR/frontend"

# Pane 0: Frontend
tmux send-keys -t "$SESSION:stack.0" "npm run dev" C-m

# Pane 1: Backend (split horizontally)
tmux split-window -h -t "$SESSION:stack" -c "$PROJECT_DIR/backend"
tmux send-keys -t "$SESSION:stack.1" \
  "source ~/.bashrc && conda activate $CONDA_ENV && bash stop_workers.sh && bash start_workers.sh && python -m app.main" C-m

# Even out the panes
tmux select-layout -t "$SESSION:stack" even-horizontal

echo ""
echo "eviStream is starting in tmux session '$SESSION'"
echo ""
echo "  Attach:   tmux attach -t $SESSION"
echo "  Detach:   Ctrl+B then D"
echo "  Stop:     bash backend/deploy/stop.sh"
echo ""

# Auto-attach
tmux attach -t "$SESSION"
