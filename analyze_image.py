#!/usr/bin/env python3
"""
从本地图片路径执行完整的持仓分析流程：
  Gemini Vision 识别持仓 → Yahoo Finance K线 → PA 分析 → 输出报告

用法：
  python analyze_image.py <图片路径>

由 OpenClaw (TARS) 调用，不依赖 Telegram 长轮询。
"""

import sys
import os

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from premarket_grid_calculator import run_calculator


def main():
    if len(sys.argv) < 2:
        print("用法: python analyze_image.py <持仓截图路径>")
        sys.exit(1)

    image_path = sys.argv[1]
    if not os.path.exists(image_path):
        print(f"❌ 图片不存在: {image_path}")
        sys.exit(1)

    print(f"🖼️  持仓截图: {image_path}")
    run_calculator(image_path=image_path)


if __name__ == "__main__":
    main()
