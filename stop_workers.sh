#!/bin/bash
# Stop all Celery workers for eviStream backend

echo "Stopping eviStream Celery Workers..."
echo "======================================"

# Stop workers using PID files
if [ -f logs/pdf_worker.pid ]; then
    echo "Stopping PDF Processing Worker..."
    kill -TERM $(cat logs/pdf_worker.pid) 2>/dev/null
    rm logs/pdf_worker.pid
fi

if [ -f logs/codegen_worker.pid ]; then
    echo "Stopping Code Generation Worker..."
    kill -TERM $(cat logs/codegen_worker.pid) 2>/dev/null
    rm logs/codegen_worker.pid
fi

if [ -f logs/extraction_worker.pid ]; then
    echo "Stopping Extraction Worker..."
    kill -TERM $(cat logs/extraction_worker.pid) 2>/dev/null
    rm logs/extraction_worker.pid
fi

# Kill any remaining workers
echo "Cleaning up any remaining worker processes..."
pkill -f "celery.*evistream_workers" 2>/dev/null

sleep 2

echo ""
echo "✅ All workers stopped!"
echo ""
