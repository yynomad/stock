"""
StockMan Telegram Bot
─────────────────────────────────────────────────────────────
长驻 Telegram Bot，接收用户消息和图片，执行 PA 分析并返回结果。

功能：
  · 接收持仓截图 → Gemini Vision 识别 → Yahoo Finance K线 → PA分析 → 返回报告
  · 接收文本命令 → 执行对应操作

运行：python bot.py
"""

import os
import sys
import json
import time
import logging
import threading
import requests

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.config import TG_BOT_TOKEN, TG_CHAT_ID, YF_PROXY
from lib.positions import fetch_positions_from_image
from premarket_grid_calculator import run_calculator, _build_symbols


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


def handle_screenshot(chat_id: int, file_id: str, message_id: int) -> None:
    """处理持仓截图：下载 → Gemini 识别 → PA 分析 → 返回报告。"""
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

    # 构建 symbols 字典传给 run_calculator
    symbols = _build_symbols(image_path=img_path)

    # 在后台线程执行分析，不阻塞主循环
    threading.Thread(
        target=_run_analysis,
        args=(chat_id, symbols),
        daemon=True,
    ).start()


def _run_analysis(chat_id: int, symbols: dict = None) -> None:
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

    # ⚠️ 冲突检测：如果 OpenClaw 也在用同一个 token polling，两边会抢消息
    # 先检查是否有 webhook
    try:
        resp = requests.get(f"{API_BASE}/getWebhookInfo", timeout=10, proxies=PROXIES)
        webhook_info = resp.json().get("result", {})
        if webhook_info.get("url"):
            logger.warning(f"检测到 webhook：{webhook_info['url']}，删除以切换 polling")
            requests.post(f"{API_BASE}/deleteWebhook", timeout=10, proxies=PROXIES)
    except:
        pass

    offset = _load_offset()
    logger.info(f"从文件加载 offset={offset}")
    print(f"📡 开始轮询消息 (offset={offset})...")

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
                    handle_screenshot(chat_id, file_id, message_id)
                    continue

                # 处理文本命令
                text = message.get("text", "").strip()
                if not text:
                    continue

                logger.info(f"收到消息 from {user_id}: {text}")

                if text in ("/start", "/help"):
                    tg_send_message(
                        chat_id,
                        "📊 <b>StockMan Bot</b>\n\n"
                        "功能：\n"
                        "📷 发送持仓截图 → 自动识别 + PA分析\n"
                        "/analyze — 分析默认 WATCHLIST 标的\n"
                        "/help — 显示帮助\n",
                        reply_to=message_id,
                    )

                elif text == "/analyze":
                    tg_send_message(chat_id, "⏳ 正在分析默认 WATCHLIST 标的...", reply_to=message_id)
                    threading.Thread(
                        target=_run_analysis,
                        args=(chat_id, None),
                        daemon=True,
                    ).start()

                else:
                    tg_send_message(chat_id, "📷 请发送持仓截图进行分析，或使用 /help 查看帮助")

        except KeyboardInterrupt:
            print("\n🛑 Bot 已停止")
            logger.info("Bot 收到 KeyboardInterrupt，退出")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
