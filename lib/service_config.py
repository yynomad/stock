"""Explicit INI configuration. Import/check never loads dotenv, connects or writes."""
import configparser
from dataclasses import dataclass,field
import math
import json
import os
from pathlib import Path
import re
from .briefing.config import BriefingConfig

class ConfigError(ValueError):pass

@dataclass(frozen=True)
class BrokerConfig:
    provider:str
    account:str=field(repr=False)
    host:str='127.0.0.1'
    port:int=4001
    client_id:int=17
    timeout:float=12
    sync_interval:float=900

@dataclass(frozen=True)
class ChannelConfig:
    provider:str
    token:str=field(repr=False)
    chat:int=0
    allowed:frozenset=frozenset()
    interactive:bool=True

@dataclass(frozen=True)
class ModelConfig:
    key:str=field(repr=False)
    name:str='gemini-3.5-flash-lite'
    max_calls:int=8
    max_tools:int=12
    roles:tuple=()

@dataclass(frozen=True)
class MonitorConfig:
    interval:float=300
    buffer:float=0.02
    max_alerts:int=5
    quote_age:float=900

@dataclass(frozen=True)
class ServiceConfig:
    path:Path
    directory:Path
    sources:Path
    log_level:str
    broker:BrokerConfig
    channel:ChannelConfig
    model:ModelConfig
    briefing:BriefingConfig
    monitor:MonitorConfig
    sec_user_agent:str=field(repr=False,default='')

SCHEMA={
 'service':{'data_dir','timezone','log_level'},
 'broker':{'provider','account_env','host','port','client_id','timeout_seconds','sync_interval_seconds'},
 'model':{'name','api_key_env','max_calls','max_tools','supervisor_model','portfolio_model','research_model','monitor_model'},
 'notification':{'provider','token_env','chat_id_env','allowed_users_env','interactive'},
 'briefing':{'enabled','time','daily_budget_usd','max_calls','max_output_tokens','max_events','lookback_hours','retention_days','input_rate','output_rate'},
 'monitor':{'interval_seconds','buffer_pct','max_daily_alerts','quote_max_age_seconds'},
 'sources':{'file','sec_user_agent_env'}}

def load_config(path,env=None):
    env=os.environ if env is None else env
    path=Path(path).expanduser().resolve()
    parser=configparser.ConfigParser(interpolation=None,strict=True)
    try:
        with path.open(encoding='utf-8') as handle:parser.read_file(handle)
    except (OSError,UnicodeError,configparser.Error):raise ConfigError('config: unreadable or invalid INI') from None
    if parser.defaults():raise ConfigError('DEFAULT section is not supported')
    for section in parser.sections():
        if section not in SCHEMA:raise ConfigError('unsupported config section')
        if set(parser[section])-SCHEMA[section]:raise ConfigError('unknown fields in '+section)
    def value(section,key,default=None):
        result=parser.get(section,key,fallback=default)
        if result is None or result=='':raise ConfigError('missing '+section+'.'+key)
        return result
    def number(section,key,default,minimum,maximum,integer=False):
        raw=value(section,key,str(default))
        try:result=int(raw) if integer else float(raw)
        except ValueError:raise ConfigError('invalid '+section+'.'+key) from None
        if not math.isfinite(result) or not minimum<=result<=maximum:raise ConfigError('invalid '+section+'.'+key)
        return result
    def boolean(section,key,default):
        raw=value(section,key,str(default).lower()).lower()
        if raw not in ('true','false'):raise ConfigError('invalid '+section+'.'+key)
        return raw=='true'
    def secret(section,key,default,required=True):
        reference=value(section,key,default)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',reference):raise ConfigError('invalid environment reference: '+section+'.'+key)
        result=env.get(reference,'')
        if required and not result.strip():raise ConfigError('unresolved environment reference: '+section+'.'+key)
        return result
    def relative(raw):return (path.parent/Path(raw).expanduser()).resolve()
    provider=value('broker','provider','ibkr')
    channel=value('notification','provider','telegram')
    if provider!='ibkr':raise ConfigError('unsupported broker.provider')
    if channel!='telegram':raise ConfigError('unsupported notification.provider')
    account=secret('broker','account_env','IBKR_ACCOUNT')
    if not re.fullmatch(r'U[0-9]{5,}',account):raise ConfigError('invalid broker.account_env')
    target=secret('notification','chat_id_env','TG_CHAT_ID')
    if not target.isdigit() or int(target)<=0:raise ConfigError('notification.chat_id_env requires a private owner')
    owners=secret('notification','allowed_users_env','TG_ALLOWED_USERS',False)
    if owners and any(not x.strip().isdigit() or int(x.strip())<=0 for x in owners.split(',')):raise ConfigError('invalid notification.allowed_users_env')
    allowed=frozenset(int(x.strip()) for x in owners.split(',') if x.strip()) or frozenset({int(target)})
    if int(target) not in allowed:raise ConfigError('private owner must be authorized')
    zone=value('service','timezone','Asia/Shanghai')
    try:
        briefing=BriefingConfig(timezone=zone,at=value('briefing','time','08:00'),enabled=boolean('briefing','enabled',False),
          budget=number('briefing','daily_budget_usd',0.20,0,1000000),max_calls=number('briefing','max_calls',8,1,100,True),
          max_output=number('briefing','max_output_tokens',2048,1,16384,True),max_events=number('briefing','max_events',5,1,20,True),
          lookback_hours=number('briefing','lookback_hours',24,1,168,True),retention_days=number('briefing','retention_days',30,1,365,True),
          input_rate=number('briefing','input_rate',0.30,0.000001,10000),output_rate=number('briefing','output_rate',2.50,0.000001,10000))
    except (ValueError,KeyError):raise ConfigError('invalid briefing/timezone configuration') from None
    level=value('service','log_level','INFO').upper()
    if level not in ('DEBUG','INFO','WARNING','ERROR'):raise ConfigError('invalid service.log_level')
    host=value('broker','host','127.0.0.1')
    if any(x.isspace() for x in host) or '/' in host or '@' in host:raise ConfigError('invalid broker.host')
    broker=BrokerConfig(provider,account,host,number('broker','port',4001,1,65535,True),number('broker','client_id',17,0,2147483647,True),
          number('broker','timeout_seconds',12,1,120),number('broker','sync_interval_seconds',900,1,86400))
    roles=tuple((role,value('model',role+'_model',value('model','name','gemini-3.5-flash-lite'))) for role in ('supervisor','portfolio','research','monitor'))
    model=ModelConfig(secret('model','api_key_env','GEMINI_API_KEY'),value('model','name','gemini-3.5-flash-lite'),
          number('model','max_calls',8,1,100,True),number('model','max_tools',12,1,100,True),roles)
    notification=ChannelConfig(channel,secret('notification','token_env','TG_BOT_TOKEN'),int(target),allowed,boolean('notification','interactive',True))
    monitor=MonitorConfig(number('monitor','interval_seconds',300,1,86400),number('monitor','buffer_pct',0.02,0.000001,1),
          number('monitor','max_daily_alerts',5,1,100,True),number('monitor','quote_max_age_seconds',900,1,86400))
    sources=relative(value('sources','file',str(Path(__file__).resolve().parents[1]/'briefing.sources.json')))
    if not sources.is_file():raise ConfigError('sources.file does not exist')
    try:
        registry=json.loads(sources.read_text(encoding='utf-8'))
        if not isinstance(registry,dict) or not isinstance(registry.get('issuers'),dict) or not isinstance(registry.get('rss'),list):raise ValueError()
    except (OSError,UnicodeError,ValueError):raise ConfigError('invalid sources.file registry') from None
    return ServiceConfig(path,relative(value('service','data_dir','runtime')),sources,level,broker,notification,model,briefing,monitor,
          secret('sources','sec_user_agent_env','SEC_USER_AGENT',False))
