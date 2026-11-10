"""Standalone backend lifecycle. IM input is optional and independent of scheduled jobs."""
import logging
import threading
import time
from .application import Application
from .agents import AgentRuntime
from .database import Database, DatabaseUnavailable
from .brokers import BrokerService,create_broker
from .channels import create_channel
from .portfolio import PortfolioService
from .research import ResearchService,SECClient,GoogleNews
from .sec_filings import FilingReader
from .market import YahooMarket
from .monitoring import MonitoringService
from .briefing.schedule import Scheduler
from .briefing.operations import DailyOperations
from .stock_briefing.service import StockBriefing
from .stock_briefing.sources import PublicHTTP,load_registry
from .stock_briefing.agent import make_model

class BackendService(Application):
    def __init__(self,config,model=None,daily_model=None,broker=None,channel=None,market=None,research=None,sources=None):
        self.config=config;self.directory=config.directory;self.briefing_config=config.briefing
        self.logger=logging.getLogger('stockman.service');self.logger.setLevel(config.log_level)
        self.logger.propagate=False
        if not self.logger.handlers:self.logger.addHandler(logging.StreamHandler())
        self.stop_event=threading.Event();self.workers=[];self.closed=False;self.next_sync=0;self.next_price=0
        self.market=market or YahooMarket(proxy='',quote_age=config.monitor.quote_age)
        registry=load_registry(config.sources)
        http=PublicHTTP(config.sec_user_agent)
        endpoint = config.model.endpoint
        research_model = dict(config.model.roles).get('research',endpoint.name)
        self.research=research or ResearchService(self.market,SECClient(config.sec_user_agent),GoogleNews.for_endpoint(endpoint,research_model),
                                                filings=FilingReader(registry['issuers'],http))
        self.runtime=AgentRuntime(self.directory,model,services={
            'portfolio':lambda request:self.portfolio.report(request),
            'research':lambda request:self.research.collect(request),
            'monitor':lambda request:self.monitor.command(request)},settings=config.model,
            research=self.research if callable(getattr(self.research,'tools',None)) else None,
            database_url=config.database_url)
        try:
            store=self.runtime.store
            self.broker=BrokerService(store,broker or create_broker(config.broker),config.broker.sync_interval)
            self.transport=channel or create_channel(config.channel);self.allowed=set(config.channel.allowed)
            if str(self.transport.chat)!=str(config.channel.chat):raise ValueError('configured channel destination mismatch')
            if config.channel.interactive and 'text' not in self.transport.capabilities:raise ValueError('channel does not support incoming text')
            self.portfolio=PortfolioService(store,broker=self.broker,bars=self.market.bars)
            self.monitor=MonitoringService(store,self.portfolio,self.market,settings=config.monitor)
            self.sources=sources
            self.briefing=StockBriefing(store,config.briefing,daily_model,registry,http,
                model_factory=lambda:make_model(config.briefing,endpoint,research_model))
            self.daily=Scheduler(store,config.briefing,str(config.channel.chat),self.allowed,self.build_daily,self.deliver)
            self.daily.recover()
            send_alert = self.deliver if config.briefing.alerts_enabled else None
            self.daily_operations = DailyOperations(store, self.logger, self.briefing.budget, send_alert)
            store.execute('CREATE TABLE IF NOT EXISTS updates(id BIGINT PRIMARY KEY,status TEXT)')
            store.execute('CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)')
            store.execute("UPDATE updates SET status='interrupted' WHERE status='handling'")
            store.execute("UPDATE updates SET status='unknown' WHERE status IN ('processing','sending')")
            store.execute('CREATE TABLE IF NOT EXISTS service_health(job TEXT PRIMARY KEY,status TEXT,at DOUBLE PRECISION,error TEXT)')
        except BaseException:self.runtime.close();self.closed=True;raise

    def record(self,job,status,now,error=None):
        self.runtime.store.execute('INSERT INTO service_health VALUES(%s,%s,%s,%s) ON CONFLICT(job) DO UPDATE SET status=excluded.status,at=excluded.at,error=excluded.error',(job,status,now,error))

    def sync_once(self,now=None,force=False):
        self.runtime.database_lease.check()
        now=time.time() if now is None else now
        try:
            result=self.broker.load(now=now,force=force)
            health=self.broker.health();self.record('sync',health['status'],now,health.get('error'))
            return result
        except Exception as exc:self.record('sync','failed',now,type(exc).__name__);return None

    def build_daily(self,owner,now,day):
        snapshot=self.broker.load(now=now)
        return self.briefing.generate(owner,snapshot,now,day,sources=self.sources,
                                      observe_sources=self.daily_operations.observe_sources)

    def tick_daily(self, now):
        self.runtime.database_lease.check()
        try:
            result = self.daily.tick(now)
            self.record('daily', result['status'], now, result.get('error_type'))
            self.daily_operations.observe_daily(result, now)
            return result
        except Exception:
            self.daily_operations.observe_scheduler_error()
            raise

    def tick(self,now=None):
        now=time.time() if now is None else now
        if now>=self.next_sync:
            self.sync_once(now);self.next_sync=now+self.config.broker.sync_interval
        result=self.tick_daily(now)
        if now>=self.next_price:
            try:self.monitor.tick(self.runtime,self.deliver,session=str(self.transport.chat),now=now)
            except Exception as exc:self.record('prices','failed',now,type(exc).__name__)
            self.next_price=now+self.config.monitor.interval
        return result

    def _loop(self,job,interval,action):
        while not self.stop_event.is_set():
            now=time.time()
            try:
                self.runtime.database_lease.check()
                result=action(now)
                if job not in ('sync','daily'):self.record(job,result.get('status','ok') if isinstance(result,dict) else 'ok',now)
            except DatabaseUnavailable:
                self.logger.error('StockMan lost the database writer lease')
                self.stop_event.set()
                break
            except Exception as exc:self.record(job,'failed',now,type(exc).__name__)
            self.stop_event.wait(interval)

    def handle_event(self,event):
        if event.channel!=self.config.channel.provider:return 'unauthorized'
        return super().handle_event(event)

    def receive_once(self,offset):
        self.runtime.database_lease.check()
        for event in self.transport.receive(offset):
            if self.stop_event.is_set():break
            self.handle_event(event)
            offset=event.identity+1
            self.runtime.store.execute("INSERT INTO settings VALUES('telegram_offset',%s) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(offset),))
        return offset

    def run(self):
        try:
            self.sync_once(force=True)
            self.logger.info('Backend started; configured background jobs are active')
            jobs=[('sync',self.config.broker.sync_interval,self.sync_once),
                  ('daily',30,self.tick_daily),
                  ('prices',self.config.monitor.interval,lambda now:self.monitor.tick(self.runtime,self.deliver,str(self.transport.chat),now))]
            for name,interval,action in jobs:
                worker=threading.Thread(target=self._loop,args=(name,interval,action),name='service-'+name,daemon=True)
                self.workers.append(worker);worker.start()
            rows=self.runtime.store.execute("SELECT value FROM settings WHERE key='telegram_offset'")
            offset=int(rows[0]['value']) if rows else 0
            while not self.stop_event.is_set():
                if not self.config.channel.interactive:self.stop_event.wait(1);continue
                try:offset=self.receive_once(offset);self.record('incoming','ok',time.time())
                except DatabaseUnavailable:
                    self.logger.error('StockMan lost the database writer lease')
                    self.stop_event.set()
                except Exception as exc:self.record('incoming','failed',time.time(),type(exc).__name__);self.stop_event.wait(5)
        except KeyboardInterrupt:pass
        finally:self.close()

    def close(self):
        self.stop_event.set();deadline=time.monotonic()+30
        for worker in self.workers:worker.join(timeout=max(0,deadline-time.monotonic()))
        if not self.closed and not any(w.is_alive() for w in self.workers):
            self.runtime.close();self.closed=True
            self.logger.info('Backend stopped')

def read_status(config):
    """Read-only monitoring is safe while the service holds its writer lock."""
    with Database(config.directory/'stockman',config.database_url).read_only() as conn:
        tables={r['tablename'] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')}
        if 'tasks' not in tables:return {'status':'not_started','jobs':[],'daily':[]}
        jobs=[dict(r) for r in conn.execute('SELECT job,status,at FROM service_health ORDER BY job')] if 'service_health' in tables else []
        broker=[dict(r) for r in conn.execute('SELECT status,last_attempt,last_success FROM broker_health WHERE provider=%s AND scope=%s',(config.broker.provider,config.broker.account))] if 'broker_health' in tables else []
        daily=[dict(r) for r in conn.execute('SELECT day,status,report,attempts,scheduled,actual,next_try FROM brief_daily WHERE owner=%s ORDER BY day DESC LIMIT 10',(str(config.channel.chat),))] if 'brief_daily' in tables else []
        coverage=None
        if 'snapshots' in tables:
            rows=conn.execute('SELECT * FROM snapshots WHERE source=%s AND scope=%s ORDER BY asof DESC,seq DESC LIMIT 1',(config.broker.provider,config.broker.account)).fetchall()
            if rows:
                from .storage import decode_snapshot
                from .brokers.coverage import coverage_info
                info=coverage_info(decode_snapshot(dict(rows[0])))
                # Keep aggregate coverage counts while withholding the excluded tickers.
                coverage={key:value for key,value in info.items() if key!='excluded'}
        return {'status':'recorded_state','jobs':jobs,'broker':broker,'daily':daily,'holdings_coverage':coverage,'note':'记录时间请自行核对；unknown 需人工核实，记录不证明进程仍存活。'}
