"""Public-source research tools; Google-grounded news remains a display-only bundle."""
from contextlib import nullcontext
from datetime import date,datetime,timezone
import html
import json
import math
import os
import re
import threading
import time
from typing import Literal
import requests
from .config import GEMINI_API_KEY,GEMINI_MODEL
from .harness import fresh
from .portfolio import valid_symbol
from .pa_strategy import analyze_pa
from .market import YahooMarket
from .agents import CURRENT,CallLimit,check_task_cost
from .sec_access import SEC_GATE
from .sec_filings import filing_request

FILING_FORM = re.compile(r'(?<![A-Za-z0-9])10-[KQ](?:/A)?(?![A-Za-z0-9])',re.IGNORECASE)


def mentioned_symbols(request):
    request=FILING_FORM.sub(' ',request)
    request=re.sub(r'(?<![A-Za-z0-9])MD&A(?![A-Za-z0-9])',' ',request,flags=re.IGNORECASE)
    matches=re.findall(r'(?<![A-Za-z0-9])\$?([A-Z][A-Z0-9.-]{0,14})(?![A-Za-z0-9])',request)
    ignore={'ETF','PA','IBKR','USD','SEC','ADR','EPS','CEO','AI','RSS','GDP','CPI','EDGAR','CIK'}
    return {match.rstrip('.-') for match in matches}-ignore


def symbol_from_request(request):
    symbols=mentioned_symbols(request)
    if len(symbols)!=1:raise ValueError('请明确提供一只美股／ETF代码，例如：研究 AAPL')
    return valid_symbol(next(iter(symbols)))


def task_symbol(store, task, session, request, now, ttl):
    """Use the owner's text and the same session's prior owner text as scope."""
    if mentioned_symbols(request):
        try:return symbol_from_request(request)
        except ValueError:return None
    if not re.search(r'它|这只|该股|刚才|上一|上个|呢|\bit\b|\bits\b',request,re.IGNORECASE):
        return None
    rows=store.execute("""SELECT input,created,status FROM tasks
        WHERE session=%s AND seq<(SELECT seq FROM tasks WHERE id=%s)
        ORDER BY seq DESC LIMIT 20""", (session,task))
    for row in rows:
        previous=json.loads(row['input'])
        if isinstance(previous,str) and mentioned_symbols(previous):
            # A later correction or ambiguous request supersedes the previous symbol.
            if row['status'] not in ('completed','partial') or not fresh(row['created'], now, ttl):
                return None
            try:return symbol_from_request(previous)
            except ValueError:return None
    return None


def financial_facts(payload,cik,period_filter=None,asof=None):
    today=date.today() if asof is None else datetime.fromtimestamp(asof,timezone.utc).date()
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
                        end_day=date.fromisoformat(end);filed_day=date.fromisoformat(filed)
                        if not end_day<=filed_day<=today or isinstance(value,bool) or not math.isfinite(float(value)):continue
                        days=(date.fromisoformat(end)-date.fromisoformat(start)).days if start else None
                        period='instant' if days is None else 'quarter' if 70<=days<=110 else 'annual' if 330<=days<=380 else None
                        if period is None or (period_filter and period!=period_filter):continue
                        accession=point['accn']
                        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}',accession):continue
                        eligible.append({'value':value,'unit':'USD','start':start,'end':end,'filed':filed,'period':period,
                                         'tag':namespace+':'+tag,'source':f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/'})
                    except (ValueError,TypeError,KeyError):continue
        result[metric]=max(eligible,key=lambda p:(p['end'],p['filed'])) if eligible else {'missing':True}
    return result


class SECClient:
    def __init__(self,user_agent=None):self.lock=threading.Lock();self.last=0;self.tickers=None;self.user_agent=user_agent
    def get(self,url):
        identity=os.getenv('SEC_USER_AGENT','') if self.user_agent is None else self.user_agent
        if '@' not in identity:raise ValueError('请配置 SEC_USER_AGENT：应用名及可联系邮箱')
        with self.lock:
            for attempt in range(3):
                SEC_GATE.wait()
                try:
                    response=requests.get(url,headers={'User-Agent':identity,'Accept-Encoding':'gzip, deflate'},timeout=15)
                    self.last=time.monotonic()
                    if response.status_code in (429,500,502,503,504) and attempt<2:
                        time.sleep(0.5*(2**attempt));continue
                    response.raise_for_status();return response.json()
                except (requests.Timeout,requests.ConnectionError):
                    self.last=time.monotonic()
                    if attempt==2:raise
                    time.sleep(0.5*(2**attempt))
    def facts(self,symbol,period=None):
        if self.tickers is None:self.tickers=self.get('https://www.sec.gov/files/company_tickers.json')
        company=next((row for row in self.tickers.values() if row['ticker']==symbol),None)
        if not company:raise ValueError('SEC 未找到匹配公司标识')
        cik=company['cik_str']
        payload=self.get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json')
        return financial_facts(payload,cik,period_filter=period)


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
    def __init__(self,api_key=None,model_name=None):self.key=api_key;self.name=model_name
    def fetch(self,symbol):
        key=GEMINI_API_KEY if self.key is None else self.key
        name=GEMINI_MODEL if self.name is None else self.name
        if not key:raise ValueError('GEMINI_API_KEY 未配置')
        from google import genai
        from google.genai import types
        context=CURRENT.get()
        if context:
            with context.lock:
                limit=context.max_models if context.max_models is not None else int(os.getenv('STOCKMAN_MAX_MODEL_CALLS','8'))
                if context.models>=limit:raise CallLimit('搜索模型调用次数达到上限')
                context.models+=1
        from .usage import price_for, record
        input_rate,output_rate=price_for(name,context.price_model,context.input_rate,context.output_rate) if context else (None,None)
        with context.lock if context and context.store and context.budget_micro is not None and input_rate is not None else nullcontext():
            if context and context.store:check_task_cost(context,input_rate,'research')
            if context and context.store:context.store.trace(context.task,'research','grounded_search','start',{})
            started=time.monotonic()
            try:
                attempts=1 if context and context.research_active else 3
                with genai.Client(api_key=key,http_options=types.HttpOptions(timeout=45000,retry_options=types.HttpRetryOptions(attempts=attempts))) as client:
                    response=client.models.generate_content(model=name,
                        contents=f'研究美股或ETF {symbol} 最近7天的重要公开事件。今天为 {date.today().isoformat()}。用中文说明事实、事件日期与发布日期，并给出来源。无法证实时明确说明，不做下单建议。',
                        config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())],max_output_tokens=2048))
                metadata=response.usage_metadata.model_dump(exclude_none=True) if response.usage_metadata else None
                if context and context.store:
                    record(context.store,context.task,'research','grounded_search',name,started,metadata,input_rate=input_rate,output_rate=output_rate,extra_unknown=True)
            except Exception:
                if context and context.store:record(context.store,context.task,'research','grounded_search',name,started,error=True,extra_unknown=True)
                raise
        return parse_grounding(response)


def news_html(bundle):
    # Original grounded text and provider suggestions are shown together, without model rewriting.
    sources=''.join('<li><a href="'+html.escape(s['url'],quote=True)+'">'+html.escape(s['title'])+'</a></li>' for s in bundle['sources'])
    return '<!doctype html><html><meta charset="utf-8"><title>新闻检索原文与来源</title><body><pre style="white-space:pre-wrap">'+html.escape(bundle['text'])+'</pre><ul>'+sources+'</ul>'+bundle['suggestions_html']+'</body></html>'


class ResearchService:
    def __init__(self,market=None,sec=None,news=None,filings=None):
        self.market=market or YahooMarket();self.sec=sec or SECClient();self.news=news or GoogleNews()
        self.filings=filings

    def tools(self):
        from langchain_core.tools import StructuredTool
        methods=[self.lookup_quote,self.lookup_daily_bars,self.lookup_financials,self.lookup_news]
        if self.filings is not None:methods.append(self.lookup_filing)
        return [StructuredTool.from_function(method) for method in methods]

    def _lookup(self,name,symbol,operation,period=None):
        current=CURRENT.get()
        if current is None or current.research is None:
            raise RuntimeError('Missing research task')
        try:symbol=valid_symbol(symbol)
        except ValueError:symbol=None
        if symbol is None or symbol!=current.research_symbol:
            result=current.research.reject(name,'scope_denied')
        else:
            key=(name,symbol) if period is None else (name,symbol,period)
            result=current.research.lookup(key,lambda:operation(symbol),once=name in ('lookup_news','lookup_filing'))
        return json.dumps(result,ensure_ascii=False,default=str)

    def lookup_quote(self,symbol: str):
        """Read the latest Yahoo quote, session and source time for an authorized US symbol."""
        from .market import choose_quote
        def fetch(ticker):
            info=self.market.info(ticker)
            quote=choose_quote(info,max_age=getattr(self.market,'quote_age',None))
            result={'status':'ok' if quote['valid'] else 'missing','symbol':ticker,'instrument':info.get('quoteType'),
                    'source':'Yahoo Finance','url':f'https://finance.yahoo.com/quote/{ticker}/','quote':quote}
            if info.get('quoteType')=='ETF':
                result['fund']={k:info.get(k) for k in ('longName','category','fundFamily','annualReportExpenseRatio','totalAssets')}
                result['holdings_note']='基金完整持仓结构尚未取得'
            return result
        return self._lookup('lookup_quote',symbol,fetch)

    def lookup_daily_bars(self,symbol: str):
        """Read Yahoo adjusted daily bars and compute price action indicators for an authorized symbol."""
        def fetch(ticker):
            bars=self.market.bars(ticker)
            indicators=analyze_pa(bars,'$')
            return {'status':'ok' if bars and not indicators.get('pa_error') else 'missing','symbol':ticker,
                    'source':'Yahoo Finance adjusted daily bars','url':f'https://finance.yahoo.com/quote/{ticker}/history/',
                    'asof':bars[-1].get('date') if bars else None,'fetched_at':time.time(),'technical':indicators}
        return self._lookup('lookup_daily_bars',symbol,fetch)

    def lookup_financials(self,symbol: str,period: Literal['latest','quarter']='latest'):
        """Read SEC company financial facts. Use quarter for latest comparable quarter, and skip this for ETF holdings."""
        def fetch(ticker):
            current=CURRENT.get()
            quote=current.research.cache.get(('lookup_quote',ticker),{})
            if quote.get('instrument')=='ETF' or re.search(r'\bETF\b',current.request,re.IGNORECASE):
                return {'status':'not_applicable','symbol':ticker,'note':'ETF 不使用发行公司营收指标'}
            facts=self.sec.facts(ticker,period='quarter') if period=='quarter' else self.sec.facts(ticker)
            available=any(isinstance(fact,dict) and 'value' in fact for fact in facts.values())
            return {'status':'ok' if available else 'missing','symbol':ticker,'source':'SEC Company Facts',
                    'fetched_at':time.time(),'financials':facts}
        return self._lookup('lookup_financials',symbol,fetch,period)

    def lookup_news(self,symbol: str):
        """Request a Gemini-grounded news attachment. Raw grounded content is delivered separately to the user."""
        def fetch(ticker):
            bundle=self.news.fetch(ticker)
            CURRENT.get().news.append(bundle)
            return {'status':'ok','symbol':ticker,'source':'Gemini Google Search','fetched_at':bundle.get('queried_at',time.time()),
                    'note':'原文、引用与关联搜索建议见单独附件。'}
        return self._lookup('lookup_news',symbol,fetch)

    def lookup_filing(self,symbol: str,form: Literal['10-K','10-Q'],
                      section: Literal['management','risks','business'],report_end: str|None=None):
        """Read a SEC primary filing section. report_end is the explicit ISO period end, or omit for the latest disclosed filing."""
        current=CURRENT.get()
        if current is None or current.research is None:
            raise RuntimeError('Missing research task')
        current.filing_requested=True
        dates=set(re.findall(r'(?<![0-9])[0-9]{4}-[0-9]{2}-[0-9]{2}(?![0-9])',current.request))
        forms=set(FILING_FORM.findall(current.request.upper()))
        if len(dates)>1 or len(forms)>1 or any(form.endswith('/A') for form in forms):
            return json.dumps(current.research.reject('lookup_filing','filing_request_invalid'))
        report_end=next(iter(dates),None)
        if forms:form=next(iter(forms))
        try:
            filing_request(form,section,report_end)
        except ValueError:
            return json.dumps(current.research.reject('lookup_filing','filing_request_invalid'))
        return self._lookup('lookup_filing',symbol,lambda ticker:self.filings.read(ticker,form,section,report_end,time.time()),
                            f'{form}:{section}:{report_end or "latest"}')
    def collect(self,request: str):
        symbol=symbol_from_request(request)
        evidence={'symbol':symbol,'fetched_at':time.time(),'missing':[]}
        try:info=self.market.info(symbol)
        except Exception as exc:info={};evidence['missing'].append('Yahoo资料：'+type(exc).__name__)
        evidence['kind']='ETF' if info.get('quoteType')=='ETF' else 'stock'
        from .market import choose_quote
        evidence['quote']=choose_quote(info,max_age=getattr(self.market,'quote_age',None))
        try:
            bars=self.market.bars(symbol)
            evidence['technical']=analyze_pa(bars,'$')
            evidence['technical_asof']=bars[-1].get('date') if bars else None
            evidence['technical_source']='Yahoo adjusted daily bars'
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
