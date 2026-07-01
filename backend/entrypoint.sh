#!/bin/sh
set -e

pip install --no-cache-dir -r requirements.txt

exec uvicorn main:app --host 0.0.0.0 --port 8002 --reload
