"""Explicit INI configuration. Import/check never loads dotenv, connects or writes."""
import configparser
from dataclasses import dataclass,field
import math
import json
import os
from pathlib import Path
import re
from .briefing.config import BriefingConfig
from .model_providers import DEFAULT_MODELS, DEFAULT_URLS, GEMINI, KEY_NAMES, ROLES, ModelEndpoint, model_name
from .harness import (DEFAULT_CONTEXT_BYTES, DEFAULT_CONTEXT_TTL, MAX_CONTEXT_BYTES,
                      MAX_CONTEXT_TTL, MIN_CONTEXT_BYTES)

class ConfigError(ValueError):pass

@dataclass(frozen=True)
class BrokerConfig:
    provider:str
    account:str=field(repr=False)
    client_id:str=field(repr=False)
    consumer_key:str=field(repr=False)
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
    task_budget_usd:float|None=None
    price_model:str|None=None
    input_rate:float|None=None
    output_rate:float|None=None
    max_research_queries:int=3
    research_timeout:int=90
    context_bytes:int=DEFAULT_CONTEXT_BYTES
    context_ttl:int=DEFAULT_CONTEXT_TTL
    provider:str=GEMINI
    base_url:str|None=field(default=None,repr=False)
    grounding_key:str=field(default='',repr=False)

    @property
    def endpoint(self):
        base_url = self.base_url or DEFAULT_URLS.get(self.provider,'')
        return ModelEndpoint(self.provider,self.name,self.key,base_url,self.grounding_key)

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
    database_url:str=field(repr=False,default='')

SCHEMA={
 'service':{'data_dir','timezone','log_level'},
 'database':{'url_env'},
 'broker':{'provider','account_env','client_id_env','consumer_key_env','sync_interval_seconds'},
 'model':{'name','api_key_env','max_calls','max_tools','supervisor_model','portfolio_model','research_model','monitor_model','task_budget_usd','price_model','input_rate','output_rate','max_research_queries','research_timeout_seconds','context_bytes','context_ttl_seconds'},
 'notification':{'provider','token_env','chat_id_env','allowed_users_env','interactive'},
 'briefing':{'enabled','alerts_enabled','time','daily_budget_usd','max_calls','max_output_tokens','max_events','lookback_hours','retention_days','input_rate','output_rate','max_research_queries','max_report_model_calls','research_timeout_seconds','price_model'},
 'monitor':{'interval_seconds','buffer_pct','max_daily_alerts','quote_max_age_seconds'},
 'sources':{'file','sec_user_agent_env'}}

for model_provider in DEFAULT_URLS:
    SCHEMA['model.' + model_provider] = SCHEMA['model'] | {'base_url','grounding_api_key_env'}


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
    provider=value('broker','provider','snaptrade')
    channel=value('notification','provider','telegram')
    if provider!='snaptrade':raise ConfigError('unsupported broker.provider')
    if channel!='telegram':raise ConfigError('unsupported notification.provider')
    from uuid import UUID
    account=secret('broker','account_env','SNAPTRADE_ACCOUNT_ID')
    try:
        if str(UUID(account))!=account.lower():raise ValueError()
    except ValueError:raise ConfigError('invalid broker.account_env') from None
    client_id=secret('broker','client_id_env','SNAPTRADE_CLIENT_ID')
    consumer_key=secret('broker','consumer_key_env','SNAPTRADE_CONSUMER_KEY')
    target=secret('notification','chat_id_env','TG_CHAT_ID')
    if not target.isdigit() or int(target)<=0:raise ConfigError('notification.chat_id_env requires a private owner')
    owners=secret('notification','allowed_users_env','TG_ALLOWED_USERS',False)
    if owners and any(not x.strip().isdigit() or int(x.strip())<=0 for x in owners.split(',')):raise ConfigError('invalid notification.allowed_users_env')
    allowed=frozenset(int(x.strip()) for x in owners.split(',') if x.strip()) or frozenset({int(target)})
    if int(target) not in allowed:raise ConfigError('private owner must be authorized')
    zone=value('service','timezone','Asia/Shanghai')
    price_model=parser.get('briefing','price_model',fallback=None)
    if price_model is not None and not all(parser.has_option('briefing',k) for k in ('input_rate','output_rate')):
        raise ConfigError('briefing.price_model requires explicit input and output rates')
    try:
        briefing=BriefingConfig(timezone=zone,at=value('briefing','time','08:00'),enabled=boolean('briefing','enabled',False),
          budget=number('briefing','daily_budget_usd',0.20,0,1000000),max_calls=number('briefing','max_calls',8,1,100,True),
          max_output=number('briefing','max_output_tokens',2048,1,16384,True),max_events=number('briefing','max_events',5,1,20,True),
          lookback_hours=number('briefing','lookback_hours',24,1,168,True),retention_days=number('briefing','retention_days',30,1,365,True),
          input_rate=number('briefing','input_rate',0.30,0.000001,10000),output_rate=number('briefing','output_rate',2.50,0.000001,10000),
          max_research_queries=number('briefing','max_research_queries',3,1,12,True),
          max_report_model_calls=number('briefing','max_report_model_calls',4,1,20,True),
          research_timeout=number('briefing','research_timeout_seconds',90,1,600,True),
          alerts_enabled=boolean('briefing','alerts_enabled',False),price_model=model_name(price_model) if price_model is not None else None)
    except (ValueError,KeyError):raise ConfigError('invalid briefing/timezone configuration') from None
    level=value('service','log_level','INFO').upper()
    if level not in ('DEBUG','INFO','WARNING','ERROR'):raise ConfigError('invalid service.log_level')
    broker=BrokerConfig(provider,account,client_id,consumer_key,number('broker','sync_interval_seconds',900,1,86400))
    model_sections = [name for name in ('model', *(f'model.{p}' for p in DEFAULT_URLS)) if parser.has_section(name)]
    if len(model_sections) > 1:
        raise ConfigError('select only one model configuration section')
    model_section = model_sections[0] if model_sections else 'model'
    model_provider = model_section.removeprefix('model.') if model_section != 'model' else GEMINI
    explicit = model_section != 'model'
    key = secret(model_section,'api_key_env',KEY_NAMES[model_provider])
    grounding_key = ''
    if parser.has_option(model_section,'grounding_api_key_env'):
        grounding_key = secret(model_section,'grounding_api_key_env','GEMINI_API_KEY')
    name = value(model_section,'name',None if explicit else DEFAULT_MODELS[model_provider])
    base_url = value(model_section,'base_url',None if explicit else DEFAULT_URLS[model_provider])
    try:
        endpoint = ModelEndpoint(model_provider,name,key,base_url,grounding_key)
        roles = tuple((role,endpoint.validate_name(value(model_section,role+'_model',name))) for role in ROLES)
    except ValueError as exc:
        raise ConfigError(f'{model_section}: {exc}') from None
    configured=parser[model_section] if parser.has_section(model_section) else {}
    has_in='input_rate' in configured
    has_out='output_rate' in configured
    if has_in != has_out:
        raise ConfigError(f'{model_section}.input_rate and {model_section}.output_rate must be configured together')
    model=ModelConfig(key,name,
          number(model_section,'max_calls',8,1,100,True),number(model_section,'max_tools',12,1,100,True),roles,
          number(model_section,'task_budget_usd',1,0.000001,1000000) if 'task_budget_usd' in configured else None,
          value(model_section,'price_model',name) if has_in else None,
          number(model_section,'input_rate',1,0,10000) if has_in else None,
          number(model_section,'output_rate',1,0,10000) if has_out else None,
          max_research_queries=number(model_section,'max_research_queries',3,1,12,True),
          research_timeout=number(model_section,'research_timeout_seconds',90,1,600,True),
          context_bytes=number(model_section,'context_bytes',DEFAULT_CONTEXT_BYTES,MIN_CONTEXT_BYTES,MAX_CONTEXT_BYTES,True),
          context_ttl=number(model_section,'context_ttl_seconds',DEFAULT_CONTEXT_TTL,1,MAX_CONTEXT_TTL,True),
          provider=model_provider,base_url=endpoint.base_url,grounding_key=grounding_key)
    notification=ChannelConfig(channel,secret('notification','token_env','TG_BOT_TOKEN'),int(target),allowed,boolean('notification','interactive',True))
    monitor=MonitorConfig(number('monitor','interval_seconds',300,1,86400),number('monitor','buffer_pct',0.02,0.000001,1),
          number('monitor','max_daily_alerts',5,1,100,True),number('monitor','quote_max_age_seconds',900,1,86400))
    sources=relative(value('sources','file',str(Path(__file__).resolve().parents[2]/'briefing.sources.json')))
    if not sources.is_file():raise ConfigError('sources.file does not exist')
    try:
        registry=json.loads(sources.read_text(encoding='utf-8'))
        if not isinstance(registry,dict) or not isinstance(registry.get('issuers'),dict) or not isinstance(registry.get('rss'),list):raise ValueError()
    except (OSError,UnicodeError,ValueError):raise ConfigError('invalid sources.file registry') from None
    database_url=secret('database','url_env','STOCKMAN_DATABASE_URL')
    from .database import validate_url
    try:validate_url(database_url)
    except ValueError:raise ConfigError('database.url_env requires a direct TLS PostgreSQL URL') from None
    return ServiceConfig(path,relative(value('service','data_dir','runtime')),sources,level,broker,notification,model,briefing,monitor,
          secret('sources','sec_user_agent_env','SEC_USER_AGENT',False),database_url)
