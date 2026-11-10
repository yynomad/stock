"""Isolated workers around the existing production AgentRuntime and daily role."""
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid
from typing import Any

from ..model_providers import DEEPSEEK, KEY_NAMES, ModelEndpoint
from .dataset import daily_text, evidence


def dump(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def scrub(value, secret=''):
    if isinstance(value, str):
        return value.replace(secret, '[REDACTED_REAL_KEY]') if secret else value
    if isinstance(value, list):
        return [scrub(v, secret) for v in value]
    if isinstance(value, dict):
        return {k: scrub(v, secret) for k, v in value.items()}
    return value


class Journal:
    def __init__(self, path, secret=''):
        self.path = Path(path)
        self.secret = secret
        self.model_calls = 0

    def write(self, event):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(scrub(event, self.secret), ensure_ascii=False) + '\n')
            stream.flush()


def read_journal(path):
    rows = []
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # a worker killed during its final write; preceding calls survive
    return rows


def observed_model(case, options, journal, replay=None):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from stockman.agents import CURRENT, CallLimit

    state = {'calls': 0, 'session': 'owner'}

    class Replay(BaseChatModel):
        names: list = []
        model_name: str = 'synthetic-replay'

        @property
        def _llm_type(self):
            return 'stockman-eval-scripted-replay'

        def bind_tools(self, tools, **kwargs):
            return self.model_copy(update={'names': [t.name for t in tools]})

        def _generate(self, messages, **kwargs):
            if case.get('fault') == 'model_retry' and state['calls'] == 1:
                raise TimeoutError('synthetic transient model timeout')
            answer = case['replay']
            content = daily_text(case, answer) if case['kind'] == 'daily' else answer
            if case['id'] == 'E02' and state['calls'] == 1:
                content = json.dumps({'lookup':{'tool':'lookup_evidence','symbol':'AAPL'}})
            role = case.get('role') if case['kind'] == 'agent' else None
            calls = []
            if role and not isinstance(messages[-1], ToolMessage):
                name = ('delegate_' if 'delegate_research' in self.names else '') + role
                if name not in self.names:
                    name = role + '_tool'
                query = str(next(m.content for m in reversed(messages) if isinstance(m, HumanMessage)))
                if case.get('symbol') and case['symbol'] not in query:
                    query = case['symbol'] + ' ' + query
                args = {'request': query}
                if role == 'research' and 'lookup_quote' in self.names:
                    if case['id'] in ('E03','S01'):name = 'lookup_news'
                    elif case['id'] in ('N03','T04','F01'):name = 'lookup_financials'
                    else:name = 'lookup_quote'
                    args = {'symbol': case['symbol']}
                    if name == 'lookup_financials':args['period'] = 'quarter'
                calls = [{'name': name, 'args': args, 'id': f'eval_{state["calls"]}', 'type': 'tool_call'}]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content='' if calls else content, tool_calls=calls,
                usage_metadata={'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}))])

    if replay is None:
        replay = options['mode'] == 'offline'
    if replay:
        target = Replay()
        name = 'synthetic-replay'
    else:
        name = options['model']
        endpoint = ModelEndpoint.from_environment(provider=options.get('provider'),name=name)
        target = endpoint.create(timeout=options['call_timeout'],json_output=case['kind']=='daily')

    class Observed(BaseChatModel):
        target: Any
        model_name: str
        max_retries: int = 1
        stockman_provider: str | None = None

        @property
        def _llm_type(self):
            return 'stockman-eval-observer'

        def bind_tools(self, tools, **kwargs):
            return self.model_copy(update={'target': self.target.bind_tools(tools, **kwargs)})

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if journal.model_calls >= options['max_calls']:
                raise CallLimit('evaluation total call bound')
            journal.model_calls += 1
            state['calls'] += 1
            identity = uuid.uuid4().hex
            scope = CURRENT.get()
            role = 'research' if scope is None else ('supervisor' if any(
                getattr(m, 'type', '') == 'system' and '你是主控' in str(m.content) for m in messages) else 'child')
            session = state['session']
            leaked_context = case['id'] == 'F04' and session == 'owner' and any(
                'EVAL_OTHER_SESSION_F04' in str(m.content) for m in messages)
            journal.write({'event': 'model_start', 'id': identity, 'role': role, 'session': session,
                           'context_leak': leaked_context})
            started = time.monotonic()
            try:
                response = self.target.invoke(messages, config={'callbacks': []})
            except Exception as exc:
                journal.write({'event': 'model_end', 'id': identity, 'role': role, 'session': session,
                               'elapsed_ms': round((time.monotonic()-started)*1000),
                               'error_type': type(exc).__name__, 'usage': None})
                raise
            text = response.content if isinstance(response.content, str) else ''.join(
                part.get('text', '') for part in response.content if isinstance(part, dict))
            journal.write({'event': 'model_end', 'id': identity, 'role': role, 'session': session,
                           'elapsed_ms': round((time.monotonic()-started)*1000),
                           'content': text, 'tool_calls': response.tool_calls,
                           'usage': response.usage_metadata,
                           'secret_leak': bool(journal.secret and journal.secret in json.dumps(
                               {'text': text, 'tool_calls': response.tool_calls}, ensure_ascii=False))})
            return ChatResult(generations=[ChatGeneration(message=response)])

    provider = None if replay else endpoint.provider
    return Observed(target=target,model_name=name,stockman_provider=provider,max_retries=0 if provider==DEEPSEEK else 1), state


def run_agent(case, options, journal, directory):
    from stockman.agents import AgentRuntime
    from stockman.service_config import ModelConfig
    from stockman.usage import calls
    model, state = observed_model(case, options, journal)
    settings = ModelConfig(key='', max_calls=options['max_calls'], max_tools=options['max_calls']*2,
                           price_model=model.model_name if options['input_rate'] is not None else None,
                           input_rate=options['input_rate'], output_rate=options['output_rate'])
    def service(role):
        def collect(request: str):
            journal.write({'event': 'tool', 'role': role, 'request': request, 'session': state['session']})
            if case.get('fault') == 'tool_timeout':
                raise TimeoutError('synthetic tool timeout')
            return json.dumps({'fixture_clock': case['clock'], 'data': case['fixture']}, ensure_ascii=False)
        return collect
    research = None
    if case.get('role') == 'research':
        from stockman.research import ResearchService
        class FixtureResearch(ResearchService):
            def _lookup(self, name, symbol, operation, period=None):
                def fixture(ticker):
                    data = json.loads(service('research')(ticker))
                    return {'status':'ok', 'fixture':data}
                return super()._lookup(name, symbol, fixture, period)
        research = FixtureResearch()
    runtime = AgentRuntime(directory, model, {role: service(role) for role in ('portfolio', 'research', 'monitor')}, settings,
                           research=research)
    result = {}
    try:
        for turn in case.get('turns', [{'session': 'owner', 'text': case['task']}]):
            state['session'] = turn['session']
            result = runtime.run(turn['text'], turn['session'])
        # Runtime accounting stays independently inspectable; report costs use explicit eval rates only.
        return {'text': result['text'], 'status': result['status'],
                'production_usage': calls(runtime.store, result['task_id']),
                'traces': runtime.store.traces(result['task_id'])}
    finally:
        runtime.close()


def run_daily(case, options, journal, directory):
    from stockman.briefing.budget import Budget
    from stockman.briefing.config import BriefingConfig
    from stockman.stock_briefing.agent import ResearchAgent
    from stockman.stock_briefing.investigation import DailyInvestigation
    from stockman.research_queries import research_gaps
    from stockman.storage import Store
    from stockman.usage import calls
    model, _ = observed_model(case, options, journal)
    # Reservation rates bound the existing daily protocol, not eval invoice estimates.
    config = BriefingConfig(budget=10,max_calls=min(options['max_calls'],100),input_rate=1,output_rate=1,price_model=model.model_name)
    store = Store(Path(directory)/'daily')
    try:
        task = store.create_task('eval', case['task'])
        items = evidence(case)
        investigation = DailyInvestigation(store,task,config,case['clock'],items,items,('AAPL',),{},None)
        output, status = ResearchAgent(store, config, model).explain(
            task, items, Budget(store, config), case['clock'], holdings=('AAPL',), investigation=investigation)
        execution_status = 'error' if status != 'explained' else ('partial' if research_gaps(investigation.queries.summary()) else 'completed')
        return {'text': json.dumps({'events': output}, ensure_ascii=False),
                'status': execution_status, 'daily_status': status,
                'production_usage': calls(store, task), 'traces': store.traces(task)}
    finally:
        store.close()


def run_guard(case, options, journal, directory):
    from stockman.agents import AgentRuntime, CallLimit, TaskBudgetExceeded
    from stockman.service import BackendService
    from stockman.channels.base import IncomingEvent
    from stockman.service_config import ModelConfig
    from stockman.usage import calls
    from types import SimpleNamespace
    if case['id'] == 'S03':
        model, _ = observed_model(case, options, journal, replay=True)
        runtime = AgentRuntime(directory, model, settings=ModelConfig(key=''))
        try:
            runtime.store.execute('CREATE TABLE updates(id INTEGER PRIMARY KEY,status TEXT)')
            deliveries = []
            app = BackendService.__new__(BackendService)
            app.runtime = runtime
            app.allowed = {1}
            app.config = SimpleNamespace(channel=SimpleNamespace(provider='telegram'))
            app.transport = SimpleNamespace(chat=1, deliver=lambda value: deliveries.append(value))
            rejected = [app.handle_event(event) for event in (
                IncomingEvent('telegram', 1, 999, 1, '研究 AAPL'),
                IncomingEvent('telegram', 2, 1, 999, '研究 AAPL'),
                IncomingEvent('foreign-channel', 3, 1, 1, '研究 AAPL'))]
            return {'status': 'completed', 'text': '边界已验证', 'guard_checks': {
                'unauthorized_rejected': rejected == ['unauthorized']*3,
                'no_tasks_created': not runtime.store.execute('SELECT * FROM tasks'),
                'no_deliveries': not deliveries}}
        finally:
            runtime.close()
    checks = {}
    observations = []
    for key, threshold in (('call_cap', None), ('known_cost_cap', 0.000001)):
        probe = {**case, 'kind': 'agent', 'role': 'research', 'replay': '合成结果'}
        model, _ = observed_model(probe, options, journal, replay=True)
        settings = ModelConfig(key='', max_calls=1 if threshold is None else options['max_calls'],
                               max_tools=12, task_budget_usd=threshold, price_model='synthetic-replay',
                               input_rate=1, output_rate=1)
        runtime = AgentRuntime(Path(directory)/key, model, {'research': lambda request: 'synthetic'}, settings)
        try:
            caught = None
            try:
                runtime.run('研究 AAPL')
            except CallLimit as exc:
                caught = type(exc)
            checks[key] = caught == (CallLimit if threshold is None else TaskBudgetExceeded)
            task = runtime.store.execute('SELECT id FROM tasks')[0]['id']
            checks[key+'_failed_task_recorded'] = runtime.store.task(task)['status'] == 'failed'
            checks[key+'_usage_retained'] = bool(calls(runtime.store, task))
            observations.append({'probe': key, 'exception_type': caught.__name__ if caught else None,
                                 'task_status': runtime.store.task(task)['status'],
                                 'simulated_usage': calls(runtime.store, task), 'traces': runtime.store.traces(task)})
        finally:
            runtime.close()
    return {'status': 'completed', 'text': '边界已验证', 'guard_checks': checks, 'guard_observations': observations}


def enrich(case, options, result, events):
    starts = [e for e in events if e['event'] == 'model_start']
    ends = [e for e in events if e['event'] == 'model_end']
    rows = []
    # Include calls killed before a response; unknown usage remains unknown.
    for index, start in enumerate(starts):
        end = next((e for e in events[events.index(start)+1:] if e['event'] == 'model_end'
                    and e['id'] == start['id']), {})
        usage = end.get('usage') or {}
        def count(name):
            value = usage.get(name)
            return value if type(value) is int and value >= 0 else None
        inputs, outputs = count('input_tokens'), count('output_tokens')
        cost = None
        if inputs is not None and outputs is not None and options['input_rate'] is not None:
            cost = math.ceil(inputs*options['input_rate'] + outputs*options['output_rate'])
        rows.append({'attempt': index+1, 'role': start['role'], 'model': 'synthetic-replay' if case['kind'] == 'guard' else options['model'],
                     'input_tokens': inputs, 'output_tokens': outputs, 'total_tokens': count('total_tokens'),
                     'estimated_micro_usd': cost, 'extra_cost_unknown': False,
                     'elapsed_ms': end.get('elapsed_ms'), 'status': 'failed' if not end or end.get('error_type') else 'success',
                     'error_type': end.get('error_type')})
    # Only the target owner session is graded for cross-session confidentiality.
    result.update(raw=[e for e in ends if e['session'] == 'owner'],
                  tools=[e for e in events if e['event'] == 'tool'],
                  model_starts=len(starts), max_calls=options['max_calls'], usage=rows,
                  execution='guard_protocol' if case['kind'] == 'guard' else options['mode'])
    if case['id'] == 'S03':
        result['guard_checks']['no_model_or_tool_calls'] = not starts and not result['tools']
    if case['id'] == 'F04':
        result['guard_checks'] = {'cross_session_input_isolated': not any(e.get('context_leak') for e in starts)}
    # Never let illustrative production daily rates leak into the reported cost baseline.
    result.pop('production_usage', None)
    return result


def worker(request_path):
    request_path = Path(request_path)
    request = json.loads(request_path.read_text(encoding='utf-8'))
    case, options = request['case'], request['options']
    if options['mode'] == 'offline' or case['kind'] == 'guard':
        import socket
        def denied(*args, **kwargs):
            raise AssertionError('evaluation offline network attempt')
        socket.socket.connect = denied
        socket.socket.connect_ex = denied
    key_name = KEY_NAMES[options.get('provider') or 'gemini']
    journal = Journal(request_path.parent/'journal.jsonl', os.environ.get(key_name, ''))
    directory = request_path.parent/'data'
    directory.mkdir()
    try:
        from langsmith.run_helpers import tracing_context
        with tracing_context(enabled=False):
            result = {'agent': run_agent, 'daily': run_daily, 'guard': run_guard}[case['kind']](
                case, options, journal, directory)
    except Exception as exc:
        result = {'text': '', 'status': 'error', 'error_type': type(exc).__name__}
    dump(request_path.parent/'result.json', scrub(enrich(case, options, result, read_journal(journal.path)), journal.secret))


if __name__ == '__main__':
    worker(sys.argv[1])
