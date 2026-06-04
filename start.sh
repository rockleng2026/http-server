#!/bin/bash
nohup python3 $(dirname "$0")/server.py --port 10888 --dir /opt > server.log 2>&1 &
echo "Server started on port 10888, PID: $!"
