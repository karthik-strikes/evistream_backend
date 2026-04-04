#!/bin/bash
# Start all Celery workers for eviStream backend
# Run this script from the backend directory

echo "Starting eviStream Celery Workers..."
echo "======================================"
echo ""
echo "This will start 3 workers in the background:"
echo "  1. PDF Processing Worker (2 concurrent)"
echo "  2. Code Generation Worker (2 concurrent)"
echo "  3. Extraction Worker (4 concurrent)"
echo ""
echo "Workers will run in the background and log to files."
echo "Press Ctrl+C to view instructions for stopping workers."
echo ""

# Load environment variables from .env (dev) or rely on AWS Secrets Manager (production)
if [ -f .env ]; then
    set -a
    source .env
    set +a
elif [ -z "$AWS_SECRETS_NAME" ]; then
    echo "ERROR: No .env file found and AWS_SECRETS_NAME is not set."
    echo "Set AWS_SECRETS_NAME for production or provide a .env for local development."
    exit 1
fi
# If AWS_SECRETS_NAME is set, Python secrets_loader will fetch secrets at worker startup

# Create logs directory if it doesn't exist
mkdir -p logs

# Clear stale Python bytecode cache to prevent loading old code
echo "Clearing Python bytecode cache..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
find "$SCRIPT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find "$SCRIPT_DIR" -name "*.pyc" -delete 2>/dev/null

# Kill any existing workers
echo "Stopping any existing workers..."
pkill -9 -f "celery.*app.workers" 2>/dev/null
sleep 2

# Start PDF processing worker
echo "Starting PDF Processing Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q pdf_processing \
    -c 2 \
    -n pdf_worker@%h \
    --logfile=/home/ubuntu/evistream/logs/pdf_worker.log \
    --detach
sleep 1 && echo "[start_workers] pdf_worker started, PID=$(pgrep -f 'celery.*-n pdf_worker' | head -1)" >> /home/ubuntu/evistream/logs/pdf_worker.log

# Start code generation worker
echo "Starting Code Generation Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q code_generation \
    -c 2 \
    -n codegen_worker@%h \
    --logfile=/home/ubuntu/evistream/logs/codegen_worker.log \
    --detach
sleep 1 && echo "[start_workers] codegen_worker started, PID=$(pgrep -f 'celery.*-n codegen_worker' | head -1)" >> /home/ubuntu/evistream/logs/codegen_worker.log

# Start extraction worker
echo "Starting Extraction Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q extraction \
    -c 4 \
    -n extraction_worker@%h \
    --logfile=/home/ubuntu/evistream/logs/extraction_worker.log \
    --detach
sleep 1 && echo "[start_workers] extraction_worker started, PID=$(pgrep -f 'celery.*-n extraction_worker' | head -1)" >> /home/ubuntu/evistream/logs/extraction_worker.log

echo ""
echo "✅ All workers started!"
echo ""
echo "Logs:"
echo "  - PDF Worker: logs/pdf_worker.log"
echo "  - Code Gen Worker: logs/codegen_worker.log"
echo "  - Extraction Worker: logs/extraction_worker.log"
echo ""
echo "To view logs in real-time:"
echo "  tail -f logs/pdf_worker.log"
echo "  tail -f logs/codegen_worker.log"
echo "  tail -f logs/extraction_worker.log"
echo ""
echo "To stop all workers:"
echo "  ./stop_workers.sh"
echo ""
echo "To check worker status:"
echo "  celery -A app.workers.celery_app inspect active"
echo ""
