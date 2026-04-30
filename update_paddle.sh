#!/bin/bash
echo "🔴 由于 PaddlePaddle 3.3.0 存在底层 OneDNN 指令不支持引发 Crash 的 Bug，执行降级到 2.6.2"
source .venv/bin/activate
pip uninstall -y paddlepaddle paddleocr paddlex
pip install paddlepaddle==2.6.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
pip install "paddleocr>=2.9.1" -i https://pypi.tuna.tsinghua.edu.cn/simple
