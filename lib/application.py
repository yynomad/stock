"""Composition root and owner-only Telegram event loop."""
import json
import os
from pathlib import Path
import threading
import time
import uuid
from .config import TG_CHAT_ID,MONITOR_INTERVAL,configure_logging
from .agents import AgentRuntime
from .portfolio import PortfolioService
from .research import ResearchService
from .monitoring import MonitoringService
from .telegram import Telegram


class Application:
    def __init__(self,directory=None,model=None,portfolio_factory=PortfolioService,research=None,market=None,transport=None):
        self.directory=Path(directory or os.getenv('STOCKMAN_DATA_DIR','runtime'))
        self.runtime=AgentRuntime(self.directory,model,services={
            'portfolio':lambda request:self.portfolio.report(request),
            'research':lambda request:self.research.collect(request),
            'monitor':lambda request:self.monitor.command(request)})
        self.portfolio=portfolio_factory(self.runtime.store)
        self.research=research or ResearchService(market=market)
        self.monitor=MonitoringService(self.runtime.store,self.portfolio,market=market)
        self.transport=transport
        self.stop_event=threading.Event()
        self.runtime.store.execute('CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY,status TEXT)')
        self.runtime.store.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
        self.runtime.store.execute("UPDATE updates SET status='interrupted' WHERE status='processing'")
        configured=os.getenv('TG_ALLOWED_USERS','')
        self.allowed={int(x.strip()) for x in configured.split(',') if x.strip().isdigit()}
        if not self.allowed and str(TG_CHAT_ID).isdigit() and int(TG_CHAT_ID)>0:self.allowed={int(TG_CHAT_ID)}

    def handle(self,text,session='local',image_path=None):
        snapshot=self.portfolio.import_image(image_path,session) if image_path else None
        return self.runtime.run(text,session,snapshot)

    def handle_update(self,update):
        identity=update['update_id'];message=update.get('message',{})
        user=message.get('from',{}).get('id');chat=message.get('chat',{}).get('id')
        with self.runtime.store.lock,self.runtime.store.conn:
            cursor=self.runtime.store.conn.execute("INSERT OR IGNORE INTO updates VALUES(?,'processing')",(identity,))
            if not cursor.rowcount:return 'duplicate'
        if user not in self.allowed or chat!=self.transport.chat:
            self.runtime.store.execute("UPDATE updates SET status='ignored' WHERE id=?",(identity,));return 'unauthorized'
        try:
            photos=message.get('photo',[])
            if photos:
                data=self.transport.photo(photos[-1]['file_id'])
                folder=self.directory/'screenshots';folder.mkdir(exist_ok=True)
                path=folder/(uuid.uuid4().hex+'.jpg');path.write_bytes(data)
                result=self.handle('请检查这张截图中的持仓',str(chat),str(path))
            elif message.get('text'):
                result=self.handle(message['text'].strip(),str(chat))
            else:
                self.runtime.store.execute("UPDATE updates SET status='ignored' WHERE id=?",(identity,))
                return 'ignored'
            status=self.transport.deliver(result)
            self.runtime.store.execute('UPDATE updates SET status=? WHERE id=?',(status,identity))
            return status
        except Exception as exc:
            self.runtime.store.execute("UPDATE updates SET status='failed' WHERE id=?",(identity,))
            self.transport.deliver({'text':'本次任务未完成：'+type(exc).__name__+'。请检查本机配置与任务记录。'})
            return 'failed'

    def poll_prices(self):
        while not self.stop_event.is_set():
            self.monitor.tick(self.runtime,self.transport.deliver,session=str(self.transport.chat))
            self.stop_event.wait(MONITOR_INTERVAL*60)

    def run(self):
        if not self.transport or not self.allowed:raise ValueError('需要明确配置 TG_ALLOWED_USERS；私聊可使用正数 TG_CHAT_ID')
        worker=threading.Thread(target=self.poll_prices,name='price-events',daemon=True);worker.start()
        row=self.runtime.store.execute("SELECT value FROM settings WHERE key='telegram_offset'")
        offset=int(row[0]['value']) if row else 0
        try:
            while not self.stop_event.is_set():
                try:
                    for update in self.transport.updates(offset):
                        self.handle_update(update)
                        offset=update['update_id']+1
                        self.runtime.store.execute("INSERT OR REPLACE INTO settings VALUES('telegram_offset',?)",(str(offset),))
                except Exception as exc:
                    self.runtime.store.trace('telegram','transport','poll','failed',{'error_type':type(exc).__name__})
                    self.stop_event.wait(5)
        except KeyboardInterrupt:pass
        finally:
            self.stop_event.set();worker.join(timeout=60)
            # Do not close SQLite underneath a still-running network/tool call.
            if not worker.is_alive():self.runtime.close()


def main():
    configure_logging()
    app=Application(transport=Telegram())
    app.run()
