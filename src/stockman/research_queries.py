"""A task-local lookup allowance and cache for the two research entry points."""
import json
import threading
import time
from datetime import datetime,timezone
from requests.exceptions import Timeout


class ResearchQueries:
    def __init__(self, store, task, limit, timeout):
        self.store = store
        self.task = task
        self.limit = limit
        self.deadline = time.monotonic() + timeout
        self.lock = threading.Lock()
        self.cache = {}
        self.outcomes = {}
        self.attempts = 0
        # A resumed task may have spent its allowance before the process exited.
        self.previous = set()
        for trace in store.traces(task):
            if trace['tool'] == 'research_lookup' and trace['phase'] == 'start':
                self.attempts += 1
                self.previous.add(tuple(json.loads(trace['payload'])['key']))

    def expired(self):
        return time.monotonic() >= self.deadline

    def _save(self, key, result):
        self.cache[key] = result
        status = result['status']
        self.outcomes[key] = status
        # Log tool identity and outcome only. Never log provider response bodies.
        phase='failed' if status in ('source_failed','source_timeout') else status
        self.store.trace(self.task, 'research', 'research_lookup', phase, {'key': list(key), 'status': status})
        return result

    def reject(self, tool, reason):
        with self.lock:
            return self._save((tool,), {'status': reason})

    def lookup(self, key, operation, *, once=False):
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            if key in self.previous or (once and any(old[0] == key[0] for old in self.previous)):
                return self._save(key, {'status': 'already_attempted'})
            if self.expired():
                return self._save(key, {'status': 'deadline_exceeded'})
            if self.attempts >= self.limit:
                return self._save(key, {'status': 'lookup_limit'})
            self.attempts += 1
            self.previous.add(key)
            self.store.trace(self.task, 'research', 'research_lookup', 'start', {'key': list(key)})
            try:
                result = json.loads(json.dumps(operation(),ensure_ascii=False,allow_nan=False,default=str))
            except Exception as exc:
                from .agents import CallLimit
                if isinstance(exc, CallLimit):
                    self._save(key, {'status': 'model_budget_exhausted'})
                    raise
                status='source_timeout' if isinstance(exc,(TimeoutError,Timeout)) else 'source_failed'
                result = {'status': status, 'error_type': type(exc).__name__}
            return self._save(key, result)

    def summary(self):
        summary = {
            'queries': self.attempts,
            'limit': self.limit,
            'outcomes': [{'tool': key[0], 'status': status} for key, status in self.outcomes.items()],
            'evidence': [result for key,result in self.cache.items() if result['status'] == 'ok' and key[0] != 'lookup_filing'],
        }
        filings = [{key: value for key,value in result.items() if key != 'passages'}
                   for result in self.filing_lookups() if 'filing' in result]
        if filings:
            summary['filings'] = filings
        return summary

    def filing_lookups(self):
        with self.lock:
            return [result for key,result in self.cache.items() if key[0]=='lookup_filing']


STATUS_TEXT = {
    'missing': '缺少可用证据',
    'source_failed': '来源调用失败',
    'source_timeout': '来源请求超时',
    'deadline_exceeded': '调查时间已到',
    'lookup_limit': '查询次数达到上限',
    'model_budget_exhausted': '模型调用或费用额度已用尽',
    'scope_denied': '标的不在本次调查范围',
    'already_attempted': '该查询已尝试，本次未重复调用',
    'filing_missing': '未找到所选申报或主文档',
    'filing_request_invalid': '请指定一份 10-K 或 10-Q，以及有效的报告期末',
    'filing_metadata_invalid': '申报信息缺失或格式不符',
    'filing_not_reviewed': '该发行人尚未启用正文读取',
    'filing_rate_limited': 'SEC 请求被限速',
    'filing_access_denied': 'SEC 暂时拒绝访问',
    'filing_too_large': '文档超出下载大小上限',
    'filing_parse_failed': '主文档解析失败',
    'filing_section_missing': '主文档未找到可核验的目标章节段落',
}


def research_gaps(summary):
    labels={'lookup_quote':'报价','lookup_daily_bars':'日线','lookup_financials':'SEC 财务','lookup_news':'新闻','lookup_evidence':'已审核材料','lookup_filing':'SEC 正文','research':'研究'}
    return list(dict.fromkeys(f'{labels.get(row["tool"],"研究")}：{STATUS_TEXT[row["status"]]}'
                              for row in summary['outcomes'] if row['status'] in STATUS_TEXT))


def research_references(evidence):
    refs=[]
    for result in evidence:
        if result.get('url'):
            asof=result.get('asof') or result.get('quote',{}).get('asof')
            if isinstance(asof,(int,float)):
                asof=datetime.fromtimestamp(asof,timezone.utc).isoformat(timespec='seconds')
            refs.append(f'来源：{result["url"]}（数据时间 {asof or "未知"}）')
        for fact in result.get('financials',{}).values():
            if 'value' in fact and fact.get('source'):
                refs.append(f'来源：{fact["source"]}（报告期 {fact.get("start") or "时点"} 至 {fact.get("end")}，披露 {fact.get("filed")}）')
    return list(dict.fromkeys(refs))
