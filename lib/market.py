"""Timestamp-aware Yahoo quotes and regular-session checks."""
import math
import os
import time
from datetime import datetime,timezone


def choose_quote(info, now=None):
    now=time.time() if now is None else now
    choices=[]
    for kind,prefix in [('regular','regularMarket'),('pre','preMarket'),('post','postMarket')]:
        price=info.get(prefix+'Price');stamp=info.get(prefix+'Time')
        if isinstance(price,bool) or not isinstance(price,(int,float)) or not math.isfinite(price) or price<=0:continue
        if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp>now+30:continue
        choices.append({'price':float(price),'asof':float(stamp),'kind':kind})
    if not choices:return {'source':'Yahoo Finance','valid':False,'reason':'缺少可信价格或行情事件时间','fetched_at':now}
    quote=max(choices,key=lambda row:row['asof'])
    quote.update(source='Yahoo Finance',fetched_at=now,valid=(0<=now-quote['asof']<=float(os.getenv('STOCKMAN_QUOTE_MAX_AGE','900'))))
    if not quote['valid']:quote['reason']='行情已过期，不能触发提醒'
    return quote


def regular_session(now):
    import exchange_calendars as calendars
    import pandas as pd
    calendar=calendars.get_calendar('XNYS')
    return bool(calendar.is_open_on_minute(pd.Timestamp(now,unit='s',tz='UTC').floor('min'),ignore_breaks=True))


class YahooMarket:
    def info(self,symbol):
        import yfinance as yf
        proxy=os.getenv('YF_PROXY','')
        if proxy:os.environ['HTTPS_PROXY']=proxy
        return yf.Ticker(symbol).info
    def quote(self,symbol):return choose_quote(self.info(symbol))
    def bars(self,symbol):
        from .yahoo_data import fetch_yahoo_bars
        return fetch_yahoo_bars(symbol)
