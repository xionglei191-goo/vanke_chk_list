#!/bin/bash
set -e
trap "" SIGINT
echo "Installing opendataloader-pdf[hybrid]..."
.venv/bin/python -m pip install 'opendataloader-pdf[hybrid]' -i https://pypi.tuna.tsinghua.edu.cn/simple/
echo "Installing paddlepaddle 3.3.0..."
.venv/bin/python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
echo "Installing paddleocr[all]..."
.venv/bin/python -m pip install 'paddleocr[all]' -i https://pypi.tuna.tsinghua.edu.cn/simple/
echo "Done"
