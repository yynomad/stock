"""Official SEC metadata and explicitly reviewed RSS. No discovery via grounded search."""
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime
import html
import ipaddress
import json
import os
import re
import socket
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
import requests
from ..briefing.models import Evidence,SourcePolicy,canonical_url
from ..sec_access import SEC_GATE

SEC_POLICY='https://www.sec.gov/about/developer-resources'
FED_POLICY='https://www.federalreserve.gov/disclaimer.htm'

def timestamp(value):
    try:
        dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError:
        try:dt=parsedate_to_datetime(str(value))
        except (ValueError,TypeError):return 0
    return dt.timestamp() if dt.tzinfo else 0

def plain(value):return html.unescape(re.sub('<[^>]*>',' ',value or '')).strip()

class PublicHTTP:
    """Bounded, allow-listed HTTPS, no redirects, total SEC rate below 10/s."""
    def __init__(self,user_agent=None,get=requests.get,sleep=time.sleep):
        self.user_agent=user_agent if user_agent is not None else os.getenv('SEC_USER_AGENT','')
        self.get=get;self.sleep=sleep
    def read(self,url,hosts):
        url=canonical_url(url);host=urlsplit(url).hostname
        if host not in hosts:raise PermissionError('host not allowed')
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):raise PermissionError('public hosts only')
        is_sec=host in ('data.sec.gov','www.sec.gov')
        if is_sec and ('@' not in self.user_agent or len(self.user_agent)<8):raise ValueError('SEC_USER_AGENT must include a real contact email')
        for attempt in range(3):
            if is_sec:
                SEC_GATE.wait(sleep=self.sleep)
            try:
                with self.get(url,headers={'User-Agent':self.user_agent or 'StockMan public RSS reader','Accept':'application/json, application/xml, text/xml'},timeout=(5,10),allow_redirects=False,stream=True) as response:
                    if response.status_code in (429,500,502,503,504) and attempt<2:
                        self.sleep(0.5*(attempt+1));continue
                    if 300<=response.status_code<400:raise ValueError('redirect requires separate host review')
                    response.raise_for_status();parts=[];size=0
                    for part in response.iter_content(16384):
                        size+=len(part)
                        if size>2*1024*1024:raise ValueError('source response too large')
                        parts.append(part)
                    return b''.join(parts)
            except (requests.Timeout,requests.ConnectionError):
                if attempt==2:raise
                self.sleep(0.5*(attempt+1))
        raise RuntimeError('source unavailable')

class SECSource:
    def __init__(self,symbol,cik,http):
        if not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,14}',symbol) or not re.fullmatch(r'[0-9]{1,10}',str(cik)):raise ValueError('invalid SEC issuer')
        self.symbol=symbol;self.cik=str(int(cik));self.http=http
        # Only SEC-created filing metadata is retained; third-party filing bodies are not fetched.
        self.policy=SourcePolicy('sec:'+symbol,SEC_POLICY,True,True,True,False,True)
    def fetch(self,now):
        data=json.loads(self.http.read(f'https://data.sec.gov/submissions/CIK{int(self.cik):010d}.json',{'data.sec.gov'}))
        rows=data['filings']['recent'];result=[]
        fields=['form','accessionNumber','primaryDocument','acceptanceDateTime']
        if not all(isinstance(rows.get(k),list) for k in fields) or len({len(rows[k]) for k in fields})!=1:raise ValueError('incomplete SEC submission arrays')
        for form,accession,document,accepted in list(zip(*(rows[k] for k in fields)))[:100]:
            if form not in ('8-K','8-K/A','6-K','10-Q','10-Q/A','10-K','10-K/A','20-F'):continue
            if not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}',accession) or not re.fullmatch(r'[A-Za-z0-9_.-]+',document):raise ValueError('invalid filing link')
            url=f'https://www.sec.gov/Archives/edgar/data/{self.cik}/{accession.replace("-","")}/{document}'
            title=f'{self.symbol} SEC {form} {accession}'
            result.append(Evidence(self.policy.source,title,url,timestamp(accepted),now,
                f'Issuer {self.symbol} filed form {form}. Accession {accession}. Filing contents not retrieved.',(self.symbol,),accession))
        return result

class RSSSource:
    def __init__(self,source,url,policy,entities,http,macro=False):
        if policy.source!=source:raise ValueError('RSS policy identity mismatch')
        policy.validate();self.url=canonical_url(url);self.policy=policy;self.entities=tuple(entities);self.http=http;self.macro=macro
    def fetch(self,now):
        raw=self.http.read(self.url,{urlsplit(self.url).hostname})
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('RSS DTD forbidden')
        root=ET.fromstring(raw);result=[]
        entries=root.findall('./channel/item') or root.findall('{http://www.w3.org/2005/Atom}entry')
        if root.tag not in ('rss','{http://www.w3.org/2005/Atom}feed'):raise ValueError('unsupported feed')
        for entry in entries[:100]:
            def value(name):
                child=entry.find(name)
                if child is None:child=entry.find('{http://www.w3.org/2005/Atom}'+name)
                return child.text or '' if child is not None else ''
            link=value('link')
            if not link:
                links=entry.findall('{http://www.w3.org/2005/Atom}link')
                link=next((x.get('href','') for x in links if x.get('rel','alternate')=='alternate'),'')
            body=plain(value('description') or value('summary') or value('content'))[:12000]
            result.append(Evidence(self.policy.source,plain(value('title')),link,
                timestamp(value('pubDate') or value('published') or value('updated')),now,body,
                ('macro',) if self.macro else self.entities,value('guid') or value('id')))
        return result

def configured_sources(registry,symbols,http):
    sources=[];missing=[]
    for symbol in sorted(set(symbols)):
        issuer=registry.get('issuers',{}).get(symbol)
        if issuer and issuer.get('cik'):sources.append(SECSource(symbol,issuer['cik'],http))
        else:missing.append(symbol+': ETF/issuer coverage not configured; no company financial inference')
    for spec in registry.get('rss',[]):
        if spec.get('macro') or set(spec.get('symbols',[])) & set(symbols) or spec.get('relationships',False):
            policy=SourcePolicy(**spec['policy'])
            sources.append(RSSSource(policy.source,spec['url'],policy,spec.get('symbols',[]),http,spec.get('macro',False)))
    if len(sources)>20:raise ValueError('at most 20 sources per run')
    return sources,missing

def load_registry(path=None):
    path=path or os.getenv('BRIEFING_SOURCES_FILE')
    if path:
        with open(path) as handle:data=json.load(handle)
    else:
        from pathlib import Path
        data=json.loads((Path(__file__).resolve().parents[3]/'briefing.sources.json').read_text())
    if not isinstance(data,dict) or not isinstance(data.get('issuers'),dict) or not isinstance(data.get('rss'),list):raise ValueError('invalid source registry')
    return data
