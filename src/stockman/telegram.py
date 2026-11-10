"""Owner-only Telegram transport; explicit calls are the only network side effects."""
import html
import io
import logging
import requests
from .config import TG_BOT_TOKEN,TG_CHAT_ID,YF_PROXY
from .research import news_html


def message_chunks(text,size=1500):
    # Escape each complete chunk; never split an HTML entity or formatting tag.
    return [html.escape(text[i:i+size]) for i in range(0,len(text),size)] or ['（无输出）']


class _HideBotURL(logging.Filter):
    def filter(self, record):
        # urllib3 can include the bot token in its request path at debug level.
        return '/bot' not in record.getMessage()


_hide_bot_url = _HideBotURL()


class TelegramReceiveError(RuntimeError):
    pass


def _delivery_result(response, delivered):
    # A rejected later chunk can leave the overall message partially delivered.
    if 400 <= response.status_code < 500 and response.status_code != 408:
        return 'unknown' if delivered else 'failed'
    if response.status_code != 200:
        return 'unknown'
    payload = response.json()
    if isinstance(payload, dict) and payload.get('ok') is True:
        return 'sent'
    if isinstance(payload, dict) and payload.get('ok') is False and not delivered:
        return 'failed'
    return 'unknown'


class Telegram:
    def __init__(self,token=TG_BOT_TOKEN,chat=TG_CHAT_ID,proxy=None):
        if not token or not str(chat).lstrip('-').isdigit():raise ValueError('请配置 TG_BOT_TOKEN 和 TG_CHAT_ID')
        for logger_name in ('urllib3.connectionpool', 'urllib3.util.retry'):
            logging.getLogger(logger_name).addFilter(_hide_bot_url)
        self.base='https://api.telegram.org/bot'+token
        self.chat=int(chat);proxy=YF_PROXY if proxy is None else proxy
        self.proxies={'http':proxy,'https':proxy} if proxy else None
    def updates(self,offset):
        try:
            response=requests.get(self.base+'/getUpdates',params={'offset':offset,'timeout':25},timeout=35,proxies=self.proxies)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get('result'), list):
                raise ValueError('invalid Telegram response')
            return payload['result']
        except (requests.RequestException, ValueError):
            # The original exception may include the URL with the bot token.
            raise TelegramReceiveError('Telegram update request failed') from None
    def deliver(self,result):
        delivered=False
        text = result['text']
        if result.get('task_id'):
            text += '\n任务编号：' + result['task_id']
        try:
            for chunk in message_chunks(text):
                response=requests.post(self.base+'/sendMessage',json={'chat_id':self.chat,'text':chunk,'parse_mode':'HTML'},timeout=20,proxies=self.proxies)
                status = _delivery_result(response, delivered)
                if status != 'sent':
                    return status
                delivered=True
            for bundle in result.get('news',[]):
                document=io.BytesIO(news_html(bundle).encode())
                response=requests.post(self.base+'/sendDocument',data={'chat_id':self.chat,'caption':'本次新闻检索原文、来源与关联搜索建议'},
                                       files={'document':('news-report.html',document,'text/html')},timeout=20,proxies=self.proxies)
                status = _delivery_result(response, delivered)
                if status != 'sent':
                    return status
                delivered=True
            return 'sent'
        except (requests.RequestException,ValueError):return 'unknown'
