#!/bin/bash
# Reels 分析工作台 —— 一键安装（macOS / Linux）
# 用法：bash install.sh
set -e
cd "$(dirname "$0")"
echo "==> 创建虚拟环境 .venv"
python3 -m venv .venv
echo "==> 安装 Python 依赖"
./.venv/bin/pip install -U pip >/dev/null
./.venv/bin/pip install -r requirements.txt
echo "==> 安装浏览器内核（用于「登录 Facebook」弹窗功能）"
./.venv/bin/python -m playwright install chromium || echo "（跳过：不影响核心抓取，仅登录按钮不可用）"
echo ""
echo "安装完成！启动方式："
echo "  bash start.sh     # 或双击 启动服务.command"
echo "然后浏览器打开 http://127.0.0.1:8756"
