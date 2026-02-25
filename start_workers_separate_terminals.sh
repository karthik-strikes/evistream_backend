#!/bin/bash
# Start Celery workers in separate terminal windows (for development/debugging)
# This script opens 3 terminal windows, each running one worker

echo "Starting workers in separate terminals..."
echo "=========================================="
echo ""
echo "Each worker will run in its own terminal window."
echo "Close the terminal windows to stop the workers."
echo ""

# PDF Processing Worker
gnome-terminal --title="PDF Worker" -- bash -c "
    echo 'Starting PDF Processing Worker...';
    echo 'Queue: pdf_processing | Concurrency: 2';
    echo '======================================';
    echo '';
    celery -A app.workers.celery_app worker \
        --loglevel=info \
        -Q pdf_processing \
        -c 2 \
        -n pdf_worker@%h;
    exec bash
" &

# Code Generation Worker
gnome-terminal --title="Code Gen Worker" -- bash -c "
    echo 'Starting Code Generation Worker...';
    echo 'Queue: code_generation | Concurrency: 1';
    echo '========================================';
    echo '';
    celery -A app.workers.celery_app worker \
        --loglevel=info \
        -Q code_generation \
        -c 1 \
        -n codegen_worker@%h;
    exec bash
" &

# Extraction Worker
gnome-terminal --title="Extraction Worker" -- bash -c "
    echo 'Starting Extraction Worker...';
    echo 'Queue: extraction | Concurrency: 2';
    echo '====================================';
    echo '';
    celery -A app.workers.celery_app worker \
        --loglevel=info \
        -Q extraction \
        -c 2 \
        -n extraction_worker@%h;
    exec bash
" &

echo "✅ Workers started in separate terminal windows!"
echo ""
echo "Close each terminal window to stop the corresponding worker."
echo ""
