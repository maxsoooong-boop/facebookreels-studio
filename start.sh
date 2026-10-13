#!/bin/bash
# Reels 分析工作台 —— 一键启动（系统 Python + 用户包目录，不依赖豆包沙箱）
cd "$(dirname "$0")"
export PYTHONPATH="/Users/mac/Library/Python/3.9/lib/python/site-packages${PYTHONPATH:+:$PYTHONPATH}"
echo "啟動 Reels 分析工作台..."
exec /usr/bin/python3 app.py
