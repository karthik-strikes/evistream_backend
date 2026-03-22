#!/bin/bash
# Start backend with correct conda environment

cd "$(dirname "$0")"
source activate topics
python -m app.main
