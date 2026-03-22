#!/bin/bash
# Stop all Celery workers for eviStream backend

echo "Stopping eviStream Celery Workers..."
echo "======================================"

pkill -TERM -f "celery.*app.workers" 2>/dev/null
sleep 2

# Force-kill anything left
pkill -9 -f "celery.*app.workers" 2>/dev/null

echo ""
echo "✅ All workers stopped!"
echo ""
