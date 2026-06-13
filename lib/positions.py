"""
持仓数据获取模块
─────────────────────────────────────────────────────────────
三层降级方案：
  1. fetch_positions_from_ibkr   IBKR Flex Query API（主方案）
  2. fetch_positions_from_image  大模型 Vision 读图（降级）
  3. WATCHLIST 中的静态配置      （兜底）

入口函数：fetch_positions()
"""

import os
import logging

from .config import (
    YF_PROXY,
    IBKR_FLEX_TOKEN, IBKR_FLEX_QUERY_ID,
    VISION_API_URL, VISION_API_KEY, VISION_MODEL,
    POSITION_IMAGE_PATH,
    WATCHLIST,
)


def fetch_positions_from_ibkr() -> dict:
    """
    主方案：通过 IBKR Flex Query HTTP API 获取持仓。

    Flex Query 不占用 TWS/Gateway API 会话，不影响手机登录。
    配置方法：Account Management → Flex Queries → 创建 Position 查询

    返回：{symbol: {"shares": float, "avg_cost": float}} 或 None
    """
    if not IBKR_FLEX_TOKEN or not IBKR_FLEX_QUERY_ID:
        print("  ℹ️  IBKR Flex Query 未配置，跳过 API 获取持仓")
        return None

    try:
        import requests
        proxies = {"http": YF_PROXY, "https": YF_PROXY} if YF_PROXY else None

        url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest"
        params = {
            "t": IBKR_FLEX_TOKEN,
            "q": IBKR_FLEX_QUERY_ID,
            "v": "3",
        }
        resp = requests.get(url, params=params, timeout=30, proxies=proxies)
        if resp.status_code != 200:
            print(f"  ⚠️  IBKR Flex Query 请求失败: HTTP {resp.status_code}")
            return None

        # 解析响应获取 query_id
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        status = root.find("Status")
        if status is not None and status.text == "Warn":
            error_code = root.find("ErrorCode")
            error_msg = root.find("ErrorMessage")
            print(f"  ⚠️  IBKR Flex Query 错误: {error_code.text if error_code is not None else ''} {error_msg.text if error_msg is not None else ''}")
            return None

        query_id_el = root.find("QueryId")
        if query_id_el is None:
            print("  ⚠️  IBKR Flex Query 未返回 QueryId")
            return None

        query_id = query_id_el.text
        print(f"  📡 IBKR Flex Query 已提交，QueryId: {query_id}")

        # 等待后拉取结果
        import time
        time.sleep(3)

        result_url = "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement"
        result_params = {"q": query_id, "t": IBKR_FLEX_TOKEN, "v": "3"}
        result_resp = requests.get(result_url, params=result_params, timeout=30, proxies=proxies)
        if result_resp.status_code != 200:
            print(f"  ⚠️  IBKR Flex Query 结果获取失败: HTTP {result_resp.status_code}")
            return None

        # 解析 XML 持仓数据
        result_root = ET.fromstring(result_resp.text)
        positions = {}
        for pos in result_root.iter("OpenPosition"):
            symbol = pos.find("symbol")
            shares = pos.find("position")
            avg_price = pos.find("avgPrice")
            currency = pos.find("currency")

            if symbol is not None and shares is not None:
                sym = symbol.text
                # 跳过现金和非股票持仓
                asset_category = pos.find("assetCategory")
                if asset_category is not None and asset_category.text not in ("STK", "ETF"):
                    continue

                positions[sym] = {
                    "shares": float(shares.text) if shares.text else 0,
                    "avg_cost": float(avg_price.text) if avg_price is not None else None,
                    "currency": currency.text if currency is not None else "USD",
                }

        if positions:
            print(f"  ✅ IBKR Flex Query 获取到 {len(positions)} 个持仓")
            logging.info(f"IBKR Flex Query 获取到 {len(positions)} 个持仓")
            return positions
        else:
            print("  ⚠️  IBKR Flex Query 未获取到持仓数据")
            return None

    except Exception as e:
        print(f"  ❌ IBKR Flex Query 异常: {e}")
        logging.error(f"IBKR Flex Query 异常: {e}")
        return None


def fetch_positions_from_image(image_path: str) -> dict:
    """
    降级方案：通过大模型 Vision API 读取持仓截图。

    将图片 base64 编码后发送给支持视觉的 LLM，提取持仓信息。
    返回：{symbol: {"shares": float, "avg_cost": float}} 或 None
    """
    if not VISION_API_KEY:
        print("  ℹ️  Vision API Key 未配置，跳过大模型读图")
        return None

    if not os.path.exists(image_path):
        print(f"  ℹ️  持仓截图不存在: {image_path}")
        return None

    try:
        import requests
        import base64

        # 读取图片并 base64 编码
        with open(image_path, "rb") as f:
            img_data = f.read()
        img_b64 = base64.b64encode(img_data).decode("utf-8")

        # 判断图片类型
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")

        print(f"  🖼️  正在通过 Vision API 读取持仓截图...")

        proxies = {"http": YF_PROXY, "https": YF_PROXY} if YF_PROXY else None

        payload = {
            "model": VISION_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "请读取这张投资组合截图中的持仓信息，输出严格的 JSON 格式。\n"
                                "格式如下，不要输出其他内容：\n"
                                '{"positions": [{"symbol": "AAPL", "shares": None, "avg_cost": None}]}' + "\n"
                                "注意：\n"
                                "1. symbol 必须是股票代码（如 AAPL, NVDA, QQQ）\n"
                                "2. shares 是持仓数量（小数也可以，如 0.5）\n"
                                "3. avg_cost 是平均买入价格\n"
                                "4. 只输出 JSON，不要任何解释文字\n"
                                "5. ETF 也包含在内（如 QQQ, EWY, 1321）\n"
                                "6. 如果是日元标的，价格保持原数字\n"
                            )
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime_type};base64,{img_b64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 2048,
            "temperature": 0.1,
        }

        headers = {
            "Authorization": f"Bearer {VISION_API_KEY}",
            "Content-Type": "application/json",
        }

        resp = requests.post(
            VISION_API_URL,
            json=payload,
            headers=headers,
            timeout=60,
            proxies=proxies,
        )

        if resp.status_code != 200:
            print(f"  ❌ Vision API 调用失败: HTTP {resp.status_code} - {resp.text[:200]}")
            logging.error(f"Vision API 调用失败: HTTP {resp.status_code}")
            return None

        # 解析响应
        data = resp.json()
        content = data["choices"][0]["message"]["content"]

        # 提取 JSON（可能被 markdown 代码块包裹）
        import json
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        if not json_match:
            print(f"  ⚠️  Vision API 返回内容无法解析为 JSON: {content[:200]}")
            return None

        parsed = json.loads(json_match.group())
        positions = {}
        for item in parsed.get("positions", []):
            sym = item.get("symbol", "")
            if sym:
                positions[sym] = {
                    "shares": item.get("shares", 0),
                    "avg_cost": item.get("avg_cost"),
                }

        if positions:
            print(f"  ✅ Vision API 识别到 {len(positions)} 个持仓")
            logging.info(f"Vision API 识别到 {len(positions)} 个持仓: {list(positions.keys())}")
            return positions
        else:
            print("  ⚠️  Vision API 未识别到任何持仓")
            return None

    except Exception as e:
        print(f"  ❌ Vision API 读图异常: {e}")
        logging.error(f"Vision API 读图异常: {e}")
        return None


def fetch_positions() -> dict:
    """
    获取持仓数据，支持降级：
      1. 优先 IBKR Flex Query API
      2. 降级到大模型读图
      3. 都失败则使用 WATCHLIST 中的静态配置

    返回：{symbol: {"shares": float, "avg_cost": float}}
    """
    print("📋 获取持仓数据...")

    # 方案1: IBKR Flex Query
    positions = fetch_positions_from_ibkr()
    if positions:
        return positions

    # 方案2: 大模型读图
    positions = fetch_positions_from_image(POSITION_IMAGE_PATH)
    if positions:
        return positions

    # 方案3: 使用 WATCHLIST 中的静态配置
    print("  ℹ️  使用 WATCHLIST 静态持仓配置")
    static = {}
    for sym, cfg in WATCHLIST.items():
        if cfg.get("avg_cost"):
            static[sym] = {
                "shares": cfg.get("shares", 0),
                "avg_cost": cfg["avg_cost"],
            }
    return static if static else {}
