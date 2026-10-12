#!/bin/bash
# Reels 分析工作台 - 一键启动
# 双击本文件即可启动服务，启动后浏览器会自动打开 http://127.0.0.1:8756
cd "$(dirname "$0")"

# 如果服务已在运行则直接打开
if curl -s -o /dev/null --max-time 3 http://127.0.0.1:8756/; then
  open "http://127.0.0.1:8756"
  echo "服务已在运行，已为你打开网页。"
  exit 0
fi

# 启动服务
nohup python3 app.py > server.log 2>&1 &
sleep 5
if curl -s -o /dev/null --max-time 5 http://127.0.0.1:8756/; then
  open "http://127.0.0.1:8756"
  echo "服务已启动，浏览器已打开。"
else
  echo "启动失败，请查看 server.log 或联系助手。"
fi
