#!/bin/bash
set -x
cd /home/xionglei/Project/vanke_chk_list/
echo "====== 彻底清理旧环境 ======"
pkill -f "start_all" 2>/dev/null
pkill -f "paddle" 2>/dev/null
pkill -f "streamlit" 2>/dev/null
pkill -f "start_vector_api" 2>/dev/null
# Do NOT kill all python, it might kill other things.
rm -rf .venv

echo "====== 重建 Python 3.12 环境 ======"
/usr/bin/python3.12 -m venv .venv

echo "====== 安装要求列表中的依赖包 ======"
.venv/bin/python -m pip install --upgrade pip
grep -vE 'paddle|opendataloader' requirements.txt > req_filter.txt
.venv/bin/python -m pip install -r req_filter.txt

echo "====== 安装特定的 PDF 解析引擎包 ======"
.venv/bin/python -m pip install opendataloader-pdf[hybrid]

echo "====== 安装飞桨 ======"
.venv/bin/python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv/bin/python -m pip install paddleocr[all]

echo "====== 降级完成 ======"
