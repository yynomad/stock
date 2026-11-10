"""Validated daily configuration, read explicitly before any source/model call."""
from dataclasses import dataclass
import math
import os
import re
from zoneinfo import ZoneInfo

@dataclass(frozen=True)
class BriefingConfig:
    budget:float=0.20
    max_calls:int=8
    max_output:int=2048
    max_events:int=5
    lookback_hours:int=24
    retention_days:int=30
    timezone:str='Asia/Shanghai'
    at:str='08:00'
    enabled:bool=False
    input_rate:float=0.30
    output_rate:float=2.50
    max_research_queries:int=3
    max_report_model_calls:int=4
    research_timeout:int=90
    alerts_enabled:bool=False
    def __post_init__(self):
        for name in ('budget','input_rate','output_rate'):
            value=getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('invalid briefing '+name)
        if self.input_rate<=0 or self.output_rate<=0:raise ValueError('token rates must be positive')
        for name,limit in [('max_calls',100),('max_output',16384),('max_events',20),('lookback_hours',168),('retention_days',365),
                           ('max_research_queries',12),('max_report_model_calls',20),('research_timeout',600)]:
            value=getattr(self,name)
            if type(value) is not int or not 1<=value<=limit:raise ValueError('invalid briefing '+name)
        ZoneInfo(self.timezone)
        if type(self.alerts_enabled) is not bool:raise ValueError('invalid briefing alerts setting')
        if not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',self.at) or type(self.enabled) is not bool:raise ValueError('invalid briefing schedule')
    @classmethod
    def load(cls,env=None):
        env=os.environ if env is None else env
        source=env.get('BRIEFING_PORTFOLIO_SOURCE','snaptrade')
        if source=='image':raise ValueError('BRIEFING_PORTFOLIO_SOURCE: 截图来源已移除')
        if source!='snaptrade':raise ValueError('BRIEFING_PORTFOLIO_SOURCE: 不支持旧持仓来源；请配置 SnapTrade')
        enabled=env.get('BRIEFING_ENABLED','false').lower()
        if enabled not in ('true','false'):raise ValueError('BRIEFING_ENABLED must be true or false')
        return cls(budget=float(env.get('BRIEFING_DAILY_BUDGET_USD','0.20')),
            max_calls=int(env.get('BRIEFING_MAX_MODEL_CALLS','8')),max_output=int(env.get('BRIEFING_MAX_OUTPUT_TOKENS','2048')),
            max_events=int(env.get('BRIEFING_MAX_EVENTS','5')),lookback_hours=int(env.get('BRIEFING_LOOKBACK_HOURS','24')),
            retention_days=int(env.get('BRIEFING_RETENTION_DAYS','30')),timezone=env.get('BRIEFING_TIMEZONE','Asia/Shanghai'),
            at=env.get('BRIEFING_TIME','08:00'),enabled=enabled=='true',
            input_rate=float(env.get('BRIEFING_INPUT_USD_PER_MILLION','0.30')),output_rate=float(env.get('BRIEFING_OUTPUT_USD_PER_MILLION','2.50')),
            max_research_queries=int(env.get('BRIEFING_MAX_RESEARCH_QUERIES','3')),
            max_report_model_calls=int(env.get('BRIEFING_MAX_REPORT_MODEL_CALLS','4')),
            research_timeout=int(env.get('BRIEFING_RESEARCH_TIMEOUT_SECONDS','90')))
