#!/usr/bin/env python3
"""
已废弃的旧入口别名（2026-10-28 清理双启动路线）。

唯一受支持的启动路线是配置驱动的后台服务：

    venv/bin/python service.py --config stock.conf

本文件不再包含独立的 .env 聊天机器人实现（旧的直连 Gemini 聊天、
watchlist 工具和 PriceMonitor 均已删除）；它只把参数原样转发给
service.py，使旧命令 `python bot.py ...` 与 `python service.py ...`
行为完全一致。导入本模块没有网络、线程或写文件副作用，真实服务
只在显式调用 main() 时启动。
"""

from __future__ import annotations

import os
import sys

# 确保项目根目录在 path 中（service.py 位于同目录）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main(argv=None):
    """转发到 service.py 的入口；参数与 service.py 完全相同。"""
    from service import main as service_main

    return service_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
