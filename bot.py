"""
StockMan Telegram Bot
─────────────────────────────────────────────────────────────
长驻 Telegram Bot，两种功能：
  1. 持仓截图 → Gemini 识图 → PA 分析 → 返回报告 → 自动启动价格监控
  2. 普通消息 → Gemini 聊天回复（支持对话添加/删除监控标的）

运行：python bot.py
"""

import os
import sys
import time
import logging
import threading
import requests

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import TG_BOT_TOKEN, TG_CHAT_ID, YF_PROXY, GEMINI_API_KEY, GEMINI_MODEL
from lib.positions import fetch_positions_from_image
from lib.monitor import PriceMonitor
from lib.watchlist import WatchlistManager
from premarket_grid_calculator import run_calculator, _build_symbols

# ── 全局实例 ──────────────────────────────────────────────────────────
watchlist = WatchlistManager()

# ── Gemini 聊天 ──────────────────────────────────────────────────────

_has_gemini = bool(GEMINI_API_KEY)
if _has_gemini:
    from google import genai as gemini_client
    from google.genai import types as gemini_types


# ── Gemini Function Calling ─────────────────────────────────────────

_WATCHLIST_TOOLS = [
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
            return "📭 监控列表为空，你可以发送持仓截图或告诉我股票代码来添加"
        lines = [f"📋 监控列表（共 {len(stocks)} 只）"]
        for sym, info in stocks.items():
            lines.append(f"  · {sym} ({info.get('display_name', sym)})")
        return "\n".join(lines)
    return f"未知函数: {name}"


def _chat_with_gemini(text: str, context: str = "") -> str:
    """用 Gemini 回复普通消息，支持 function calling 管理监控列表。"""
    if not _has_gemini:
        return "⚠️ 未配置 GEMINI_API_KEY，无法聊天"
    try:
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
                tools=_WATCHLIST_TOOLS,
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
                    tools=_WATCHLIST_TOOLS,
                ),
            )
            return resp2.text

        return resp.text

    except Exception as e:
        logger.error(f"Gemini 聊天失败: {e}")
        return f"❌ Gemini 回复出错：{e}"


# ── 日志配置 ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
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


def tg_download_file(file_id: str) -> bytes | None:
    """下载 Telegram 服务器上的文件。"""
    try:
        # 获取文件路径
        resp = requests.get(
            f"{API_BASE}/getFile",
            params={"file_id": file_id},
            timeout=10,
            proxies=PROXIES,
        )
        resp.raise_for_status()
        file_path = resp.json().get("result", {}).get("file_path")
        if not file_path:
            logger.error(f"getFile 未返回 file_path")
            return None

        # 下载文件内容
        download_url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
        resp = requests.get(download_url, timeout=30, proxies=PROXIES)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error(f"文件下载失败: {e}")
        return None


# ── 持仓截图处理 ──────────────────────────────────────────────────────

SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")


def _add_positions_to_watchlist(positions: dict) -> None:
    """将截图识别的持仓自动加入监控列表。"""
    added = []
    for symbol, pos in positions.items():
        display_name = pos.get("display_name", symbol)
        watchlist.add(symbol, display_name=display_name)
        added.append(symbol)
    if added:
        logger.info(f"已自动加入监控列表: {', '.join(added)}")


def handle_screenshot(chat_id: int, file_id: str, message_id: int,
                      monitor: PriceMonitor = None) -> None:
    """处理持仓截图：下载 → Gemini 识别 → 加入监控 → PA 分析 → 返回报告。"""
    # 发送处理中提示
    tg_send_message(chat_id, "🖼️ 收到持仓截图，正在识别分析...", reply_to=message_id)

    # 确保截图目录存在
    os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

    # 下载图片
    logger.info("正在下载截图...")
    img_data = tg_download_file(file_id)
    if not img_data:
        tg_send_message(chat_id, "❌ 截图下载失败，请重试")
        return

    # 保存到本地
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    img_path = os.path.join(SCREENSHOTS_DIR, f"positions_{timestamp}.jpg")
    with open(img_path, "wb") as f:
        f.write(img_data)
    logger.info(f"截图已保存: {img_path}")

    # Gemini 识别持仓
    logger.info("正在通过 Gemini 识别持仓...")
    positions = fetch_positions_from_image(img_path)
    if not positions:
        tg_send_message(chat_id, "⚠️ 未能从截图中识别到持仓，请确认截图包含持仓信息")
        return

    symbols_str = ", ".join(positions.keys())
    logger.info(f"识别到持仓: {symbols_str}")
    tg_send_message(chat_id, f"✅ 识别到 {len(positions)} 个持仓：{symbols_str}\n⏳ 正在拉取K线并进行PA分析...")

    # 自动加入监控列表
    _add_positions_to_watchlist(positions)

    # 构建 symbols 字典传给 run_calculator
    symbols = _build_symbols(image_path=img_path)

    # 在后台线程执行分析，不阻塞主循环
    threading.Thread(
        target=_run_analysis,
        args=(chat_id, symbols, monitor),
        daemon=True,
    ).start()




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
    if not TG_BOT_TOKEN:
        print("❌ TG_BOT_TOKEN 未配置，无法启动 bot")
        sys.exit(1)

    print("🤖 StockMan Bot 启动中...")
    logger.info("StockMan Bot 启动")

    # 验证 bot token
    try:
        resp = requests.get(f"{API_BASE}/getMe", timeout=10, proxies=PROXIES)
        resp.raise_for_status()
        bot_info = resp.json().get("result", {})
        bot_name = bot_info.get("username", "unknown")
        print(f"✅ 已连接：@{bot_name}")
        logger.info(f"Bot 已连接：@{bot_name}")
    except Exception as e:
        print(f"❌ Bot 连接失败：{e}")
        sys.exit(1)

    # ⚠️ 冲突检测
    try:
        resp = requests.get(f"{API_BASE}/getWebhookInfo", timeout=10, proxies=PROXIES)
        webhook_info = resp.json().get("result", {})
        if webhook_info.get("url"):
            logger.warning(f"检测到 webhook：{webhook_info['url']}，删除以切换 polling")
            requests.post(f"{API_BASE}/deleteWebhook", timeout=10, proxies=PROXIES)
    except:
        pass

    # 加载监控列表
    stock_count = len(watchlist)
    print(f"📋 已加载监控列表，共 {stock_count} 只标的")
    logger.info(f"监控列表加载完成，共 {stock_count} 只标的")

    offset = _load_offset()
    logger.info(f"从文件加载 offset={offset}")
    print(f"📡 开始轮询消息 (offset={offset})...")

    # 初始化实时价格监控器
    chat_id_owner = int(TG_CHAT_ID) if TG_CHAT_ID else 0
    monitor = PriceMonitor(chat_id_owner)

    while True:
        try:
            updates = tg_get_updates(offset=offset, timeout=30)

            for update in updates:
                offset = update["update_id"] + 1
                _save_offset(offset)

                message = update.get("message")
                if not message:
                    continue

                chat_id = message["chat"]["id"]
                user_id = message["from"]["id"]
                message_id = message.get("message_id")

                # 权限检查
                if ALLOWED_USERS and user_id not in ALLOWED_USERS:
                    logger.warning(f"未授权用户 {user_id}，忽略")
                    continue

                # 处理图片
                photos = message.get("photo")
                if photos:
                    # 取最大尺寸的图片
                    largest_photo = photos[-1]
                    file_id = largest_photo["file_id"]
                    logger.info(f"收到图片 from {user_id}, file_id={file_id}")
                    handle_screenshot(chat_id, file_id, message_id, monitor=monitor)
                    continue

                # 处理文本命令
                text = message.get("text", "").strip()
                if not text:
                    continue

                logger.info(f"收到消息 from {user_id}: {text}")

                # 所有文本消息 → Gemini 聊天（带持仓 + 监控上下文）
                context = monitor.context_summary()
                tg_send_message(chat_id, "💬 正在思考...", reply_to=message_id)
                reply = _chat_with_gemini(text, context=context)
                tg_send_message(chat_id, reply, reply_to=message_id)

        except KeyboardInterrupt:
            print("\n🛑 Bot 已停止")
            logger.info("Bot 收到 KeyboardInterrupt，退出")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
