"""Owner-only Telegram transport; explicit calls are the only network side effects."""
import html
import io
import requests
from .config import TG_BOT_TOKEN,TG_CHAT_ID,YF_PROXY
from .research import news_html


def message_chunks(text,size=1500):
    # Escape each complete chunk; never split an HTML entity or formatting tag.
    return [html.escape(text[i:i+size]) for i in range(0,len(text),size)] or ['（无输出）']


class Telegram:
    def __init__(self,token=TG_BOT_TOKEN,chat=TG_CHAT_ID,proxy=None):
        if not token or not str(chat).lstrip('-').isdigit():raise ValueError('请配置 TG_BOT_TOKEN 和 TG_CHAT_ID')
        self.base='https://api.telegram.org/bot'+token
        self.chat=int(chat);proxy=YF_PROXY if proxy is None else proxy
        self.proxies={'http':proxy,'https':proxy} if proxy else None
    def updates(self,offset):
        response=requests.get(self.base+'/getUpdates',params={'offset':offset,'timeout':25},timeout=35,proxies=self.proxies)
        response.raise_for_status();return response.json().get('result',[])
    def deliver(self,result):
        delivered=False
        try:
            for chunk in message_chunks(result['text']+'\n任务编号：'+result.get('task_id','')):
                response=requests.post(self.base+'/sendMessage',json={'chat_id':self.chat,'text':chunk,'parse_mode':'HTML'},timeout=20,proxies=self.proxies)
                if response.status_code!=200 or not response.json().get('ok',False):return 'unknown' if delivered else 'failed'
                delivered=True
            for bundle in result.get('news',[]):
                document=io.BytesIO(news_html(bundle).encode())
                response=requests.post(self.base+'/sendDocument',data={'chat_id':self.chat,'caption':'本次新闻检索原文、来源与关联搜索建议'},
                                       files={'document':('news-report.html',document,'text/html')},timeout=20,proxies=self.proxies)
                if response.status_code!=200 or not response.json().get('ok',False):return 'unknown' if delivered else 'failed'
                delivered=True
            return 'sent'
        except (requests.RequestException,ValueError):return 'unknown'
