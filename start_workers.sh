#!/bin/bash
# Start all Celery workers for eviStream backend
# Run this script from the backend directory

echo "Starting eviStream Celery Workers..."
echo "======================================"
echo ""
echo "This will start 3 workers in the background:"
echo "  1. PDF Processing Worker (2 concurrent)"
echo "  2. Code Generation Worker (1 concurrent)"
echo "  3. Extraction Worker (2 concurrent)"
echo ""
echo "Workers will run in the background and log to files."
echo "Press Ctrl+C to view instructions for stopping workers."
echo ""

# Create logs directory if it doesn't exist
mkdir -p logs

# Kill any existing workers
echo "Stopping any existing workers..."
pkill -f "celery.*evistream_workers" 2>/dev/null
sleep 2

# Start PDF processing worker
echo "Starting PDF Processing Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q pdf_processing \
    -c 2 \
    -n pdf_worker@%h \
    --logfile=logs/pdf_worker.log \
    --pidfile=logs/pdf_worker.pid \
    --detach

# Start code generation worker
echo "Starting Code Generation Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q code_generation \
    -c 1 \
    -n codegen_worker@%h \
    --logfile=logs/codegen_worker.log \
    --pidfile=logs/codegen_worker.pid \
    --detach

# Start extraction worker
echo "Starting Extraction Worker..."
celery -A app.workers.celery_app worker \
    --loglevel=info \
    -Q extraction \
    -c 2 \
    -n extraction_worker@%h \
    --logfile=logs/extraction_worker.log \
    --pidfile=logs/extraction_worker.pid \
    --detach

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
