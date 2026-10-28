"""
StockMan Telegram Bot
─────────────────────────────────────────────────────────────
长驻 Telegram Bot：主动文字问答、只读券商持仓复盘与价格监控。
配置驱动的自动同步及日报请优先使用 service.py。

运行：python bot.py
"""

from __future__ import annotations

import os
import sys
import time
import logging
import threading
import requests

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import TG_BOT_TOKEN, TG_CHAT_ID, YF_PROXY, GEMINI_API_KEY, GEMINI_MODEL
from lib.monitor import PriceMonitor
from lib.watchlist import WatchlistManager
from premarket_grid_calculator import run_calculator

# ── 全局实例 ──────────────────────────────────────────────────────────
watchlist = WatchlistManager()

# ── Gemini 聊天 ──────────────────────────────────────────────────────

def _watchlist_tools():
    from google.genai import types as gemini_types
    return [
        gemini_types.Tool(
            function_declarations=[
                gemini_types.FunctionDeclaration(
                    name="add_to_watchlist",
                    description="将一只股票加入监控列表。用户说「加上茅台」「监控英伟达」时调用。",
                    parameters=gemini_types.Schema(
                        type=gemini_types.Type.OBJECT,
                        properties={
                            "symbol": gemini_types.Schema(
                                type=gemini_types.Type.STRING,
                                description="股票代码。美股直接写代码如 NVDA, AAPL；上海用 .SS 如 600519.SS；深圳用 .SZ 如 000858.SZ",
                            ),
                            "display_name": gemini_types.Schema(
                                type=gemini_types.Type.STRING,
                                description="显示名称，如 英伟达、贵州茅台。可选，留空则自动使用代码。",
                            ),
                        },
                        required=["symbol"],
                    ),
                ),
                gemini_types.FunctionDeclaration(
                    name="remove_from_watchlist",
                    description="将一只股票从监控列表移除。用户说「删掉茅台」「移除英伟达」时调用。",
                    parameters=gemini_types.Schema(
                        type=gemini_types.Type.OBJECT,
                        properties={
                            "symbol": gemini_types.Schema(
                                type=gemini_types.Type.STRING,
                                description="股票代码，如 NVDA, 600519.SS",
                            ),
                        },
                        required=["symbol"],
                    ),
                ),
                gemini_types.FunctionDeclaration(
                    name="list_watchlist",
                    description="查询当前监控列表里的所有股票。用户问「我监控了哪些」「有什么股票」时调用。",
                    parameters=gemini_types.Schema(
                        type=gemini_types.Type.OBJECT,
                        properties={},
                    ),
                ),
            ],
        ),
    ]


def _execute_function(fc: gemini_types.FunctionCall) -> str:
    """执行 Gemini 请求的函数调用，返回结果文本。"""
    name = fc.name
    args = fc.args or {}

    if name == "add_to_watchlist":
        symbol = args.get("symbol", "").upper()
        display_name = args.get("display_name", "")
        info = watchlist.add(symbol, display_name=display_name or None)
        return f"✅ 已将 {symbol}（{info.get('display_name', symbol)}）加入监控列表，当前共 {len(watchlist)} 只标的"
    elif name == "remove_from_watchlist":
        symbol = args.get("symbol", "").upper()
        if watchlist.remove(symbol):
            return f"✅ 已将 {symbol} 从监控列表移除，当前共 {len(watchlist)} 只标的"
        else:
            return f"⚠️ {symbol} 不在监控列表中"
    elif name == "list_watchlist":
        stocks = watchlist.get_all()
        if not stocks:
            return "📭 监控列表为空，你可以告诉我股票代码来添加"
        lines = [f"📋 监控列表（共 {len(stocks)} 只）"]
        for sym, info in stocks.items():
            lines.append(f"  · {sym} ({info.get('display_name', sym)})")
        return "\n".join(lines)
    return f"未知函数: {name}"


def _chat_with_gemini(text: str, context: str = "") -> str:
    """用 Gemini 回复普通消息，支持 function calling 管理监控列表。"""
    if not GEMINI_API_KEY:
        return "⚠️ 未配置 GEMINI_API_KEY，无法聊天"
    try:
        from google import genai as gemini_client
        from google.genai import types as gemini_types
        client = gemini_client.Client(api_key=GEMINI_API_KEY)
        system_prompt = (
            "你是一个专业的股票投资助手，名叫 StockMan。用中文回答。\n\n"
            "【功能说明】\n"
            "1. 用户发来股票代码或名称时，调用 add_to_watchlist 加入监控。\n"
            "2. 用户要求删除时，调用 remove_from_watchlist。\n"
            "3. 用户查询监控列表时，调用 list_watchlist。\n"
            "4. 用户问股票相关问题时，基于持仓上下文回答。\n"
            "5. 不相关的话题正常聊天。\n\n"
            "【股票代码格式】\n"
            "· 美股：直接写代码，如 NVDA、AAPL、QQQ\n"
            "· 上海 A 股：代码后加 .SS，如 600519.SS\n"
            "· 深圳 A 股：代码后加 .SZ，如 000858.SZ\n"
        )
        if context:
            system_prompt += f"\n\n【当前持仓上下文】\n{context}"

        # 构建对话：用户消息
        contents = [
            gemini_types.Content(
                role="user",
                parts=[gemini_types.Part(text=text)],
            ),
        ]

        # 第一轮：带 tools 发送
        resp = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=gemini_types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=_watchlist_tools(),
            ),
        )

        # 检查是否有 function call
        candidate = resp.candidates[0]
        part = candidate.content.parts[0]

        if part.function_call:
            fc = part.function_call
            result_text = _execute_function(fc)

            # 把 function_call 和 function_response 加入对话
            contents.append(
                gemini_types.Content(
                    role="model",
                    parts=[gemini_types.Part(function_call=fc)],
                )
            )
            contents.append(
                gemini_types.Content(
                    role="user",
                    parts=[gemini_types.Part(
                        function_response=gemini_types.FunctionResponse(
                            name=fc.name,
                            response={"output": result_text},
                        ),
                    )],
                )
            )

            # 第二轮：让 Gemini 基于 function 结果生成回复
            resp2 = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=contents,
                config=gemini_types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=_watchlist_tools(),
                ),
            )
            return resp2.text

        return resp.text

    except Exception as e:
        logger.error(f"Gemini 聊天失败: {e}")
        return f"❌ Gemini 回复出错：{e}"


# ── 日志配置 ──────────────────────────────────────────────────────────
logger = logging.getLogger("stockman")


# ── offset 持久化 ──────────────────────────────────────────────────────

OFFSET_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot_offset")


def _load_offset() -> int:
    try:
        return int(open(OFFSET_FILE).read().strip())
    except:
        return 0


def _save_offset(offset: int) -> None:
    try:
        open(OFFSET_FILE, "w").write(str(offset))
    except Exception as e:
        logger.error(f"保存 offset 失败: {e}")


# ── Telegram API 封装 ────────────────────────────────────────────────

PROXIES = {"http": YF_PROXY, "https": YF_PROXY} if YF_PROXY else None
API_BASE = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"


def tg_get_updates(offset: int = None, timeout: int = 30) -> list:
    """长轮询获取更新。"""
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    try:
        resp = requests.get(
            f"{API_BASE}/getUpdates",
            params=params,
            timeout=timeout + 10,
            proxies=PROXIES,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])
    except Exception as e:
        logger.error(f"getUpdates 失败: {e}")
        return []


def tg_send_message(chat_id: int, text: str, reply_to: int = None) -> bool:
    """发送文本消息。"""
    try:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        resp = requests.post(
            f"{API_BASE}/sendMessage",
            json=payload,
            timeout=15,
            proxies=PROXIES,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"sendMessage 失败: {e}")
        return False


def _run_analysis(chat_id: int, symbols: dict = None,
                   monitor: PriceMonitor = None) -> None:
    """在后台线程中执行 PA 分析并发送报告。"""
    try:
        results, report = run_calculator(symbols=symbols)

        # Telegram 消息长度限制 4096，超长则分段
        if len(report) > 4000:
            chunks = []
            current = ""
            for line in report.split("\n"):
                if len(current) + len(line) + 1 > 3900:
                    chunks.append(current)
                    current = line
                else:
                    current += "\n" + line if current else line
            if current:
                chunks.append(current)
            for chunk in chunks:
                tg_send_message(chat_id, chunk)
        else:
            tg_send_message(chat_id, report)

        # 分析成功后自动启动实时监控
        if monitor and results:
            monitor.update_from_results(results)
            monitor.start()

        logger.info("PA 分析报告已发送")
    except Exception as e:
        logger.error(f"PA 分析异常: {e}", exc_info=True)
        tg_send_message(chat_id, f"❌ PA 分析出错：{e}")


# ── 主循环 ────────────────────────────────────────────────────────────

ALLOWED_USERS = set()
if TG_CHAT_ID:
    ALLOWED_USERS.add(int(TG_CHAT_ID))


def main():
    from lib.application import main as run_agent_application
    run_agent_application()


if __name__ == "__main__":
    main()
