#!/bin/bash
set -e

cd "$(dirname "$0")"

echo "============================================"
echo "  Ollama Relay Station — 一键部署"
echo "  基于 oai2ollama 引擎"
echo "============================================"

if [ ! -d "venv" ]; then
    echo ">>> 创建 Python 虚拟环境 ..."
    python3 -m venv venv
    echo "✅ 虚拟环境创建完成"
else
    echo "✅ 虚拟环境已存在"
fi

echo ">>> 安装依赖 (oai2ollama + uvicorn + httpx) ..."
venv/bin/pip install oai2ollama -q

if [ ! -f "password.txt" ]; then
    DEFAULT_PW="${RELAY_PASSWORD:-admin}"
    echo "$DEFAULT_PW" > password.txt
    echo ">>> 默认密码已生成: password.txt"
    echo "    请修改此文件设置你的密码"
fi

echo ""
echo ">>> 启动服务 ..."
echo "  管理面板: http://0.0.0.0:3456"
echo "  代理服务: http://0.0.0.0:11434 (Ollama 兼容)"
echo ""
exec venv/bin/python3 start.py
