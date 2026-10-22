"""Public-source research tools; Google-grounded news remains a display-only bundle."""
from datetime import date,datetime
import html
import json
import math
import os
import re
import threading
import time
import requests
from .config import GEMINI_API_KEY,GEMINI_MODEL
from .portfolio import valid_symbol
from .pa_strategy import analyze_pa
from .market import YahooMarket
from .agents import CURRENT,CallLimit


def symbol_from_request(request):
    matches=re.findall(r'(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9.-]{0,14})(?![A-Za-z0-9])',request)
    ignore={'ETF','PA','IBKR','USD','SEC'}
    symbols=[s for s in matches if s not in ignore]
    if len(set(symbols))!=1:raise ValueError('请明确提供一只美股／ETF代码，例如：研究 AAPL')
    return valid_symbol(symbols[0])


def financial_facts(payload,cik):
    fields={'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','Revenue'],
            'net_income':['NetIncomeLoss','ProfitLoss'],
            'operating_cash_flow':['NetCashProvidedByUsedInOperatingActivities','CashFlowsFromUsedInOperatingActivities'],
            'assets':['Assets'],'liabilities':['Liabilities']}
    result={}
    for metric,tags in fields.items():
        eligible=[]
        for namespace in ['us-gaap','ifrs-full']:
            facts=payload.get('facts',{}).get(namespace,{})
            for tag in tags:
                for point in facts.get(tag,{}).get('units',{}).get('USD',[]):
                    try:
                        if point.get('form') not in ('10-K','10-Q','20-F','40-F'):continue
                        start=point.get('start');end=point['end'];filed=point['filed'];value=point['val']
                        date.fromisoformat(end);date.fromisoformat(filed)
                        if date.fromisoformat(filed)>date.today() or isinstance(value,bool) or not math.isfinite(float(value)):continue
                        days=(date.fromisoformat(end)-date.fromisoformat(start)).days if start else None
                        period='instant' if days is None else 'quarter' if 70<=days<=110 else 'annual' if 330<=days<=380 else None
                        if period is None:continue
                        accession=point['accn']
                        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession):continue
                        eligible.append({'value':value,'unit':'USD','start':start,'end':end,'filed':filed,'period':period,
                                         'tag':namespace+':'+tag,'source':f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/'})
                    except (ValueError,TypeError,KeyError):continue
        result[metric]=max(eligible,key=lambda p:(p['end'],p['filed'])) if eligible else {'missing':True}
    return result


class SECClient:
    def __init__(self):self.lock=threading.Lock();self.last=0;self.tickers=None
    def get(self,url):
        identity=os.getenv('SEC_USER_AGENT','')
        if '@' not in identity:raise ValueError('请配置 SEC_USER_AGENT：应用名及可联系邮箱')
        with self.lock:
            time.sleep(max(0,0.15-(time.monotonic()-self.last)))
            response=requests.get(url,headers={'User-Agent':identity,'Accept-Encoding':'gzip, deflate'},timeout=15)
            self.last=time.monotonic();response.raise_for_status();return response.json()
    def facts(self,symbol):
        if self.tickers is None:self.tickers=self.get('https://www.sec.gov/files/company_tickers.json')
        company=next((row for row in self.tickers.values() if row['ticker']==symbol),None)
        if not company:raise ValueError('SEC 未找到匹配公司标识')
        cik=company['cik_str']
        payload=self.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json')
        return financial_facts(payload,cik)


def parse_grounding(response):
    if not response.candidates:raise ValueError('搜索没有可用输出')
    candidate=response.candidates[0];metadata=candidate.grounding_metadata
    if metadata is None:raise ValueError('没有搜索接地证据，不能当作已核实新闻')
    chunks=metadata.grounding_chunks or []
    supports=metadata.grounding_supports or []
    if not supports:raise ValueError('新闻输出没有引用支持')
    for support in supports:
        if not support.grounding_chunk_indices or any(i<0 or i>=len(chunks) for i in support.grounding_chunk_indices):
            raise ValueError('新闻引用索引无效')
    suggestions=getattr(metadata.search_entry_point,'rendered_content',None)
    if not suggestions:raise ValueError('缺少关联搜索建议，新闻展示未验证')
    sources=[]
    for chunk in chunks:
        web=getattr(chunk,'web',None)
        if web and web.uri and web.uri.startswith('https://'):
            sources.append({'title':web.title or web.uri,'url':web.uri,'published_at':None})
    if not sources:raise ValueError('没有可显示来源')
    return {'text':response.text or '', 'sources':sources,'suggestions_html':suggestions,
            'queried_at':time.time(),'queries':metadata.web_search_queries or [],
            'usage':response.usage_metadata.model_dump(exclude_none=True) if response.usage_metadata else {}}


class GoogleNews:
    def fetch(self,symbol):
        if not GEMINI_API_KEY:raise ValueError('GEMINI_API_KEY 未配置')
        from google import genai
        from google.genai import types
        context=CURRENT.get()
        if context:
            with context.lock:
                context.models+=1
                if context.models>int(os.getenv('STOCKMAN_MAX_MODEL_CALLS','8')):raise CallLimit('搜索模型调用次数达到上限')
        with genai.Client(api_key=GEMINI_API_KEY,http_options=types.HttpOptions(timeout=45000)) as client:
            response=client.models.generate_content(model=GEMINI_MODEL,
                contents=f'研究美股或ETF {symbol} 最近7天的重要公开事件。今天为 {date.today().isoformat()}。用中文说明事实、事件日期与发布日期，并给出来源。无法证实时明确说明，不做下单建议。',
                config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],max_output_tokens=2048))
        return parse_grounding(response)


def news_html(bundle):
    # Original grounded text and provider suggestions are shown together, without model rewriting.
    sources=''.join('<li><a href="'+html.escape(s['url'],quote=True)+'">'+html.escape(s['title'])+'</a></li>' for s in bundle['sources'])
    return '<!doctype html><html><meta charset="utf-8"><title>新闻检索原文与来源</title><body><pre style="white-space:pre-wrap">'+html.escape(bundle['text'])+'</pre><ul>'+sources+'</ul>'+bundle['suggestions_html']+'</body></html>'


class ResearchService:
    def __init__(self,market=None,sec=None,news=None):
        self.market=market or YahooMarket();self.sec=sec or SECClient();self.news=news or GoogleNews()
    def collect(self,request: str):
        symbol=symbol_from_request(request)
        evidence={'symbol':symbol,'fetched_at':time.time(),'missing':[]}
        try:info=self.market.info(symbol)
        except Exception as exc:info={};evidence['missing'].append('Yahoo资料：'+type(exc).__name__)
        evidence['kind']='ETF' if info.get('quoteType')=='ETF' else 'stock'
        from .market import choose_quote
        evidence['quote']=choose_quote(info)
        try:evidence['technical']=analyze_pa(self.market.bars(symbol),'$')
        except Exception as exc:evidence['missing'].append('技术数据：'+type(exc).__name__)
        if evidence['kind']=='ETF':
            evidence['fund']={k:info.get(k) for k in ['longName','category','fundFamily','annualReportExpenseRatio','totalAssets']}
            evidence['financials']={'not_applicable':'ETF 不使用发行公司营收指标'}
            evidence['missing'].append('基金完整持仓结构尚未取得')
        else:
            try:evidence['financials']=self.sec.facts(symbol)
            except Exception as exc:evidence['financials']={'missing':True};evidence['missing'].append('SEC财务：'+type(exc).__name__)
        context=CURRENT.get()
        try:
            bundle=self.news.fetch(symbol)
            if context:
                with context.lock:context.news.append(bundle)
                evidence['news']='新闻原文与关联搜索建议将直接显示给用户，不在此重写或建立新闻缓存库。'
            else:evidence['news']='需要应用投递上下文，未存储新闻内容'
        except CallLimit:raise
        except Exception as exc:evidence['missing'].append('新闻：'+type(exc).__name__)
        return json.dumps(evidence,ensure_ascii=False,default=str)
