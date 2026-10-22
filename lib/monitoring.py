"""Persistent price-event detection; normal polling never invokes a model."""
from datetime import datetime
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import os
import re
import time
from .agents import CURRENT
from .market import YahooMarket,regular_session
from .pa_strategy import analyze_pa
from .portfolio import valid_symbol


def monitor_intent(text):
    if re.match(r'^(?:请|帮我|麻烦)?(?:停止|取消|关闭)监控',text.strip()):return 'stop'
    if re.match(r'^(?:请|帮我|麻烦)?(?:(?:开始|添加|启动)监控|监控\s*[A-Z])',text.strip()):return 'start'
    return None


class MonitoringService:
    def __init__(self,store,portfolio,market=None,in_session=regular_session):
        self.store=store;self.portfolio=portfolio;self.market=market or YahooMarket();self.in_session=in_session
    def command(self,request: str):
        context=CURRENT.get();action=monitor_intent(context.request) if context else None
        symbols=[valid_symbol(s) for s in re.findall(r'(?<![A-Za-z0-9])([A-Z][A-Z0-9.-]{0,14})(?![A-Za-z0-9])',request) if s not in ('ETF','PA','IBKR','USD')]
        if action=='stop':
            if symbols:
                for symbol in symbols:self.store.execute('UPDATE rules SET enabled=0 WHERE symbol=?',(symbol,))
            else:self.store.execute('UPDATE rules SET enabled=0')
            return json.dumps({'status':'stopped','symbols':symbols or 'all'},ensure_ascii=False)
        if action=='start':
            source=None
            if not symbols:
                source=self.portfolio.load(context.session,context.snapshot)
                symbols=list(source['positions'])
            rules=[];errors=[]
            for symbol in dict.fromkeys(symbols):
                pa=analyze_pa(self.market.bars(symbol),'$')
                if pa.get('pa_error') or not pa.get('support') or not pa.get('resistance'):
                    errors.append({'symbol':symbol,'error':'历史K线不足，未创建监控规则'});continue
                spec={'symbol':symbol,'support':pa['support'],'resistance':pa['resistance'],
                      'buffer':float(os.getenv('MONITOR_BUFFER_PCT','0.02')),
                      'created_at':time.time(),'source':source['source'] if source else 'explicit-symbol',
                      'source_asof':source['asof'] if source else None}
                self.store.execute("INSERT INTO rules(symbol,spec,enabled) VALUES(?,?,1) ON CONFLICT(symbol) DO UPDATE SET spec=excluded.spec,enabled=1",(symbol,json.dumps(spec)))
                rules.append(spec)
            return json.dumps({'status':'configured' if rules else 'not_configured','rules':rules,'errors':errors},ensure_ascii=False)
        rules=self.store.execute('SELECT * FROM rules ORDER BY symbol')
        events=self.store.execute('SELECT symbol,status,payload,at FROM events ORDER BY at DESC LIMIT 5')
        return json.dumps({'status':'read_only_status','rules':rules,'recent_events':events,
                           'note':'只有用户本次明确要求开始／停止，才修改规则'},ensure_ascii=False)

    def check(self,symbol,quote,now=None):
        now=time.time() if now is None else now
        if not self.in_session(now) or quote.get('kind')!='regular':return None
        stamp=quote.get('asof');price=quote.get('price')
        if not quote.get('valid') or not isinstance(stamp,(float,int)) or not isinstance(price,(float,int)):return None
        if not math.isfinite(price) or price<=0 or not math.isfinite(stamp) or not 0<=now-stamp<=float(os.getenv('STOCKMAN_QUOTE_MAX_AGE','900')):return None
        with self.store.lock,self.store.conn:
            rule=self.store.conn.execute('SELECT * FROM rules WHERE symbol=? AND enabled=1',(symbol,)).fetchone()
            if not rule or stamp<=rule['last_time']:return None
            spec=json.loads(rule['spec'])
            state='below_support' if price<spec['support']*(1-spec['buffer']) else 'above_resistance' if price>spec['resistance']*(1+spec['buffer']) else 'normal'
            self.store.conn.execute('UPDATE rules SET state=?,last_time=? WHERE symbol=?',(state,stamp,symbol))
            if state=='normal' or state==rule['state']:return None
            day=datetime.fromtimestamp(now,ZoneInfo('America/New_York')).date().isoformat()
            count=self.store.conn.execute('SELECT COUNT(*) FROM events WHERE symbol=? AND day=?',(symbol,day)).fetchone()[0]
            if count>=int(os.getenv('MONITOR_MAX_DAILY_ALERTS','5')):return None
            identity=hashlib.sha256(f'{symbol}:{state}:{stamp}'.encode()).hexdigest()
            payload={'symbol':symbol,'event':state,'quote':quote,'levels':spec}
            cursor=self.store.conn.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,'pending',?,?)",(identity,symbol,day,json.dumps(payload),now))
            return {'id':identity,**payload} if cursor.rowcount else None

    def tick(self,runtime,send,session='owner',now=None):
        now=time.time() if now is None else now
        if not self.in_session(now):return []
        events=[]
        for row in self.store.execute('SELECT symbol FROM rules WHERE enabled=1'):
            try:
                event=self.check(row['symbol'],self.market.quote(row['symbol']),now)
                if not event:continue
                events.append(event)
                try:
                    result=runtime.run('监控价格事件：'+json.dumps(event,ensure_ascii=False)+'。请解释价格变化及需要关注的风险。',session=session)
                    status=send(result)
                    if status not in ('sent','failed','unknown'):status='unknown'
                except Exception as exc:
                    status='failed';self.store.trace(event['id'],'monitor','delivery','failed',{'error_type':type(exc).__name__})
                self.store.execute('UPDATE events SET status=? WHERE id=?',(status,event['id']))
            except Exception as exc:self.store.trace('poller','monitor','quote','failed',{'error_type':type(exc).__name__})
        return events
