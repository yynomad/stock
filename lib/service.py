"""Standalone backend lifecycle. IM input is optional and independent of scheduled jobs."""
from functools import partial
import threading
import time
from .application import Application
from .agents import AgentRuntime
from .brokers import BrokerService,create_broker
from .channels import create_channel
from .portfolio import PortfolioService
from .positions import fetch_positions_from_image
from .research import ResearchService,SECClient,GoogleNews
from .market import YahooMarket
from .monitoring import MonitoringService
from .briefing.schedule import Scheduler
from .stock_briefing.service import StockBriefing
from .stock_briefing.sources import PublicHTTP,load_registry
from .stock_briefing.agent import make_model

class BackendService(Application):
    def __init__(self,config,model=None,daily_model=None,broker=None,channel=None,market=None,research=None,image=None,sources=None):
        self.config=config;self.directory=config.directory;self.briefing_config=config.briefing
        self.stop_event=threading.Event();self.workers=[];self.closed=False;self.next_sync=0;self.next_price=0
        self.runtime=AgentRuntime(self.directory,model,services={
            'portfolio':lambda request:self.portfolio.report(request),
            'research':lambda request:self.research.collect(request),
            'monitor':lambda request:self.monitor.command(request)},settings=config.model)
        try:
            store=self.runtime.store
            self.broker=BrokerService(store,broker or create_broker(config.broker),config.broker.sync_interval)
            self.transport=channel or create_channel(config.channel);self.allowed=set(config.channel.allowed)
            if str(self.transport.chat)!=str(config.channel.chat):raise ValueError('configured channel destination mismatch')
            self.market=market or YahooMarket(proxy='',quote_age=config.monitor.quote_age)
            self.portfolio=PortfolioService(store,broker=self.broker,bars=self.market.bars,
                image=image or partial(fetch_positions_from_image,api_key=config.model.key,model_name=config.model.name))
            self.research=research or ResearchService(self.market,SECClient(config.sec_user_agent),GoogleNews(config.model.key,dict(config.model.roles)['research']))
            self.monitor=MonitoringService(store,self.portfolio,self.market,settings=config.monitor)
            self.sources=sources
            self.briefing=StockBriefing(store,config.briefing,daily_model,load_registry(config.sources),PublicHTTP(config.sec_user_agent),
                model_factory=lambda:make_model(config.briefing,config.model.key,dict(config.model.roles)['research']))
            self.daily=Scheduler(store,config.briefing,str(config.channel.chat),self.allowed,self.build_daily,self.transport.deliver)
            self.daily.recover()
            store.execute('CREATE TABLE IF NOT EXISTS updates(id INTEGER PRIMARY KEY,status TEXT)')
            store.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
            store.execute("UPDATE updates SET status='interrupted' WHERE status='processing'")
            store.execute('CREATE TABLE IF NOT EXISTS service_health(job TEXT PRIMARY KEY,status TEXT,at REAL,error TEXT)')
        except BaseException:self.runtime.close();self.closed=True;raise

    def record(self,job,status,now,error=None):
        self.runtime.store.execute('INSERT OR REPLACE INTO service_health VALUES(?,?,?,?)',(job,status,now,error))

    def sync_once(self,now=None,force=False):
        now=time.time() if now is None else now
        try:
            result=self.broker.load(now=now,force=force)
            health=self.broker.health();self.record('sync',health['status'],now,health.get('error'))
            return result
        except Exception as exc:self.record('sync','failed',now,type(exc).__name__);return None

    def build_daily(self,owner,now,day):
        snapshot=self.broker.load(now=now)
        return self.briefing.generate(owner,snapshot,now,day,sources=self.sources)

    def tick(self,now=None):
        now=time.time() if now is None else now
        if now>=self.next_sync:
            self.sync_once(now);self.next_sync=now+self.config.broker.sync_interval
        result=self.daily.tick(now)
        self.record('daily',result['status'],now,result.get('error_type'))
        if now>=self.next_price:
            try:self.monitor.tick(self.runtime,self.transport.deliver,session=str(self.transport.chat),now=now)
            except Exception as exc:self.record('prices','failed',now,type(exc).__name__)
            self.next_price=now+self.config.monitor.interval
        return result

    def _loop(self,job,interval,action):
        while not self.stop_event.is_set():
            now=time.time()
            try:
                result=action(now)
                if job!='sync':self.record(job,result.get('status','ok') if isinstance(result,dict) else 'ok',now)
            except Exception as exc:self.record(job,'failed',now,type(exc).__name__)
            self.stop_event.wait(interval)

    def receive_once(self,offset):
        for event in self.transport.receive(offset):
            if self.stop_event.is_set():break
            self.handle_event(event)
            offset=event.identity+1
            self.runtime.store.execute("INSERT OR REPLACE INTO settings VALUES('telegram_offset',?)",(str(offset),))
        return offset

    def run(self):
        try:
            self.sync_once(force=True)
            jobs=[('sync',self.config.broker.sync_interval,self.sync_once),
                  ('daily',30,self.daily.tick),
                  ('prices',self.config.monitor.interval,lambda now:self.monitor.tick(self.runtime,self.transport.deliver,str(self.transport.chat),now))]
            for name,interval,action in jobs:
                worker=threading.Thread(target=self._loop,args=(name,interval,action),name='service-'+name,daemon=True)
                self.workers.append(worker);worker.start()
            rows=self.runtime.store.execute("SELECT value FROM settings WHERE key='telegram_offset'")
            offset=int(rows[0]['value']) if rows else 0
            while not self.stop_event.is_set():
                if not self.config.channel.interactive:self.stop_event.wait(1);continue
                try:offset=self.receive_once(offset);self.record('incoming','ok',time.time())
                except Exception as exc:self.record('incoming','failed',time.time(),type(exc).__name__);self.stop_event.wait(5)
        except KeyboardInterrupt:pass
        finally:self.close()

    def close(self):
        self.stop_event.set();deadline=time.monotonic()+30
        for worker in self.workers:worker.join(timeout=max(0,deadline-time.monotonic()))
        if not self.closed and not any(w.is_alive() for w in self.workers):
            self.runtime.close();self.closed=True
