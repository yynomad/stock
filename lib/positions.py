"""
持仓数据获取模块（Gemini Vision 版）
─────────────────────────────────────────────────────────────
通过 Gemini Vision API 读取持仓截图，提取持仓数据。

入口函数：fetch_positions()
"""

import os
import json
import re
import logging

from google import genai
from google.genai import types

from .config import GEMINI_API_KEY, GEMINI_MODEL


def fetch_positions_from_image(image_path: str) -> dict:
    """
    通过 Gemini Vision API 读取持仓截图。

    将图片发送给 Gemini，提取持仓信息。
    返回：{symbol: {"shares": float, "avg_cost": float}} 或 None
    """
    if not GEMINI_API_KEY:
        print("  ℹ️  GEMINI_API_KEY 未配置，跳过 Gemini 读图")
        return None

    if not image_path or not os.path.exists(image_path):
        print(f"  ℹ️  持仓截图不存在: {image_path or '(未提供)'}")
        return None

    try:
        with open(image_path, "rb") as f:
            img_data = f.read()

        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(ext, "image/jpeg")

        print(f"  🖼️  正在通过 Gemini Vision 读取持仓截图...")

        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = (
            "请读取这张投资组合截图中的持仓信息，输出严格的 JSON 格式。\n"
            '格式如下，不要输出其他内容：\n'
            '{"positions": [{"symbol": "AAPL", "shares": null, "avg_cost": null}]}\n'
            "注意：\n"
            "1. symbol 必须是股票代码（如 AAPL, NVDA, QQQ）\n"
            "2. shares 是持仓数量（小数也可以，如 0.5），看不到填 null\n"
            "3. avg_cost 是平均买入价格，看不到填 null\n"
            "4. 只输出 JSON，不要任何解释文字\n"
            "5. ETF 也包含在内（如 QQQ, EWY, 1321）\n"
            "6. 日元标的（如 1321）价格保持原数字\n"
            "7. 瑞士标的（如 XFAB.SW）保留 .SW 后缀\n"
        )

        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[
                types.Part.from_bytes(data=img_data, mime_type=mime_type),
                types.Part.from_text(text=prompt),
            ],
        )

        content = response.text.strip()

        # 提取 JSON（可能被 markdown 代码块包裹）
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            print(f"  ⚠️  Gemini 返回内容无法解析为 JSON: {content[:200]}")
            return None

        parsed = json.loads(json_match.group())
        positions = {}
        for item in parsed.get("positions", []):
            sym = item.get("symbol", "")
            if sym:
                positions[sym] = {
                    "shares": item.get("shares"),
                    "avg_cost": item.get("avg_cost"),
                }

        if positions:
            print(f"  ✅ Gemini 识别到 {len(positions)} 个持仓")
            logging.info(f"Gemini 识别到 {len(positions)} 个持仓: {list(positions.keys())}")
            return positions
        else:
            print("  ⚠️  Gemini 未识别到任何持仓")
            return None

    except Exception as e:
        print(f"  ❌ Gemini 读图异常: {e}")
        logging.error(f"Gemini 读图异常: {e}")
        return None


def fetch_positions(image_path: str = None) -> dict:
    """
    获取持仓数据（Gemini Vision 读图）。

    参数：
      image_path: 持仓截图文件路径

    返回：{symbol: {"shares": float|None, "avg_cost": float|None}} 或 {}
    """
    print("📋 获取持仓数据...")

    positions = fetch_positions_from_image(image_path)
    if positions:
        return positions

    print("  ⚠️  持仓数据获取失败")
    return {}
