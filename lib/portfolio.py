"""Validated, separate IBKR/image snapshots and deterministic PA reports."""
import json
import math
import os
import re
import time
from .agents import CURRENT
from .ibkr import fetch_ibkr_positions
from .pa_strategy import analyze_pa
from .position_decision import build_position_decision
from .positions import fetch_positions_from_image
from .yahoo_data import fetch_yahoo_bars


def valid_symbol(symbol):
    value=str(symbol).strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,14}',value) or value.endswith(('.T','.HK','.SW','.SS','.SZ')):
        raise ValueError('第一版仅支持美股和美股 ETF 代码')
    return value.replace('.','-')


def normalize_positions(positions):
    if not isinstance(positions,dict):raise ValueError('持仓结构必须是对象')
    result={}
    for symbol,item in positions.items():
        symbol=valid_symbol(symbol)
        if not isinstance(item,dict):raise ValueError('持仓条目必须是对象')
        if item.get('currency','USD')!='USD' or item.get('asset_type','STK')!='STK':
            raise ValueError('当前组合包含不适用的资产／币种，未保存不完整快照')
        row={**item,'currency':'USD','asset_type':'STK'}
        for field in ['shares','avg_cost']:
            value=item.get(field)
            if value is None:row[field]=None;continue
            if isinstance(value,bool):raise ValueError('股数／成本不能是布尔值')
            try:value=float(value)
            except (ValueError,TypeError):raise ValueError('股数／成本必须是有效数字') from None
            if not math.isfinite(value) or (field=='avg_cost' and value<=0):raise ValueError('股数／成本数值无效')
            row[field]=value
        if symbol in result:raise ValueError('重复股票代码，不能自动合并股数')
        result[symbol]=row
    return result


class PortfolioService:
    def __init__(self, store, ibkr=fetch_ibkr_positions, image=fetch_positions_from_image, bars=fetch_yahoo_bars,broker=None):
        self.store=store;self.ibkr=ibkr;self.image=image;self.bars=bars;self.broker=broker

    def import_image(self, path, session='local'):
        positions=self.image(path)
        if not positions:raise ValueError('截图未识别到持仓，请补充清晰截图；未改用其他持仓')
        clean=normalize_positions(positions)
        return self.store.snapshot('image',session,clean)

    def load(self, session='local', snapshot=None):
        if snapshot:
            row=self.store.get_snapshot(snapshot)
            if not row or row['source']!='image' or row['scope']!=session:raise ValueError('截图快照不存在或不属于本会话')
            row['cached']=False;return row
        if self.broker is not None:return self.broker.load()
        account=os.getenv('IBKR_ACCOUNT','')
        try:
            positions=normalize_positions(self.ibkr(account))
            self.store.snapshot('ibkr',account,positions)
            row=self.store.latest('ibkr',account);row['cached']=False
        except Exception as exc:
            row=self.store.latest('ibkr',account)
            if not row:raise RuntimeError('IBKR 持仓未同步，且没有此账户的缓存；可发送持仓截图') from None
            row['cached']=True;row['sync_error']=type(exc).__name__
        row['age_seconds']=max(0,time.time()-row['asof'])
        row['stale']=row['age_seconds']>86400
        return row

    def report(self, request: str):
        context=CURRENT.get()
        snapshot=self.load(context.session if context else 'local',context.snapshot if context else None)
        rows=[]
        for symbol,position in snapshot['positions'].items():
            row={'symbol':symbol,'currency_sign':'$','shares':position.get('shares'),'avg_cost':position.get('avg_cost')}
            bars=self.bars(symbol)
            row['bars_asof']=bars[-1].get('date') if bars else None
            row['price_basis']='Yahoo adjusted daily close; not a live quote'
            pa=analyze_pa(bars,'$');row['pa']=pa
            if pa.get('pa_error'):row['error']=pa['pa_error']
            shares=position.get('shares')
            if shares is None:row['decision']={'headline':'持仓数量未知，暂不生成仓位比例或盈亏','action':'wait_position'}
            elif shares<0:row['decision']={'headline':'空头仓位：现有多头 PA 仓位规则不适用','action':'unsupported_short'}
            else:row['decision']=build_position_decision(row)
            rows.append(row)
        # Raw account identifiers are intentionally omitted from model-visible output.
        return json.dumps({'source':snapshot['source'],'asof':snapshot['asof'],'cached':snapshot['cached'],
                           'stale':snapshot.get('stale',False),'positions':rows,
                           'note':'空组合' if not rows else '规则参考；未下单'},ensure_ascii=False)
