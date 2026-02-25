#!/bin/bash
# Start backend with correct conda environment

cd /nlp/data/karthik9/Sprint1/Dental/eviStream/backend
source activate topics
python -m app.main
