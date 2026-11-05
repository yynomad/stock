import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from stockman.agents import AgentRuntime, CURRENT, Invocation, OfflineModel, TaskBudgetExceeded
from stockman.briefing.config import BriefingConfig
from stockman.briefing.models import Evidence, SourcePolicy
from stockman.offline import create_offline_app
from stockman.research import GoogleNews, ResearchService
from stockman.research_queries import ResearchQueries
from stockman.service_config import ModelConfig
from stockman.stock_briefing.service import StockBriefing
from stockman.storage import Store
from stockman.usage import record


FACT = {'value': 123, 'unit': 'USD', 'period': 'quarter', 'start': '2026-04-01', 'end': '2026-06-30',
        'filed': '2026-08-01', 'source': 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/'}
FINANCIAL = {'lookup': {'tool': 'lookup_financials', 'symbol': 'AAA', 'period': 'quarter'}}
NOW = 1793832155


class DailyModel(BaseChatModel):
    steps: list
    seen: list = []

    @property
    def _llm_type(self):
        return 'scripted-research-test'

    def _generate(self, messages, **kwargs):
        payload = json.loads(messages[-1].content)
        step = self.steps[len(self.seen)]
        self.seen.append(payload)
        if step in ('final','retrieved'):
            if step == 'retrieved':
                item = next(row for row in payload['evidence'] if row['id']==payload['lookup_result']['evidence_ids'][0])
            else:item = payload['evidence'][0]
            step = {'events': [{'id': item['id'], 'quote': item['body'], 'impact': '仍需核实实际影响',
                               'unknown': '未读取正文', 'watch': '关注后续披露'}]}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(step),
            usage_metadata={'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}))])


class FilingSource:
    policy = SourcePolicy('test-sec', 'https://example.com/policy', True, True, True, False, True)

    def fetch(self, now):
        return [Evidence('test-sec', 'AAA filing', 'https://example.com/filing', now-10, now,
                         'Issuer AAA submitted a filing. Filing contents not retrieved.', ('AAA',))]


def company_facts():
    point = {'val': 123, 'start': FACT['start'], 'end': FACT['end'], 'filed': FACT['filed'],
             'form': '10-Q', 'accn': '0000000001-26-000001'}
    return json.dumps({'facts': {'us-gaap': {'Revenues': {'units': {'USD': [point]}}}}}).encode()


class ResearchToolTest(unittest.TestCase):
    def test_question_only_calls_selected_source_and_keeps_citation(self):
        market = Mock(quote_age=900)
        market.info.return_value = {'regularMarketPrice': 100, 'regularMarketTime': time.time()}
        sec, news = Mock(), Mock()
        sec.facts.return_value = {'revenue': FACT}
        with TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory, OfflineModel(), research=ResearchService(market, sec, news))
            try:
                price = runtime.run('AAPL 最新价格', 'owner')
                self.assertEqual(price['research']['queries'], 1)
                market.info.assert_called_once_with('AAPL')
                sec.facts.assert_not_called()
                market.bars.assert_not_called()
                news.fetch.assert_not_called()
                self.assertIn('https://finance.yahoo.com/quote/AAPL/', price['text'])
                finance = runtime.run('那它最近一季营收呢', 'owner')
                sec.facts.assert_called_once_with('AAPL', period='quarter')
                market.info.assert_called_once_with('AAPL')
                self.assertIn(FACT['end'], finance['text'])
                self.assertIn(FACT['source'], finance['text'])
                other = runtime.run('那它最近一季营收呢', 'other')
                self.assertEqual(other['research']['queries'], 0)
                self.assertEqual(other['status'], 'partial')
                plain = runtime.run('你好', 'owner')
                self.assertNotIn('research', plain)
                sec.facts.assert_called_once()
            finally:
                runtime.close()

    def test_telegram_entry_uses_research_tools(self):
        transport = Mock(chat=123)
        transport.deliver.return_value = 'sent'
        with TemporaryDirectory() as directory:
            app = create_offline_app(directory, transport)
            app.allowed = {123}
            try:
                result = app.handle_update({'update_id': 12, 'message': {'from': {'id': 123}, 'chat': {'id': 123},
                                           'text': 'AAPL 最新价格'}})
                self.assertEqual(result, 'sent')
                response = transport.deliver.call_args.args[0]
                self.assertEqual(response['research']['outcomes'], [{'tool': 'lookup_quote', 'status': 'ok'}])
            finally:
                app.runtime.close()

    def test_scope_denial_and_parallel_reuse_charge_only_real_attempt(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory)/'db')
            market = Mock(quote_age=900)
            market.info.return_value = {'regularMarketPrice': 100, 'regularMarketTime': time.time()}
            queries = ResearchQueries(store, 'task', 1, 90)
            scope = Invocation('task','owner',research=queries,research_symbol='AAPL')
            service = ResearchService(market=market)
            def lookup(symbol):
                token = CURRENT.set(scope)
                try:return json.loads(service.lookup_quote(symbol))
                finally:CURRENT.reset(token)
            try:
                self.assertEqual(lookup('MSFT')['status'], 'scope_denied')
                with ThreadPoolExecutor(max_workers=4) as pool:
                    results = list(pool.map(lookup, ['AAPL']*4))
                self.assertTrue(all(result['status']=='ok' for result in results))
                market.info.assert_called_once_with('AAPL')
                self.assertEqual(queries.attempts,1)
                extra = queries.lookup(('lookup_daily_bars','AAPL'),lambda:self.fail('cap bypassed'))
                self.assertEqual(extra['status'],'lookup_limit')
            finally:
                store.close()

    def test_failed_attempt_is_not_repeated_after_resume_and_deadline_blocks_query(self):
        with TemporaryDirectory() as directory:
            store = Store(Path(directory)/'db')
            try:
                operation=Mock(side_effect=TimeoutError('private provider text'))
                queries=ResearchQueries(store,'task',3,90)
                key=('lookup_quote','AAPL')
                self.assertEqual(queries.lookup(key,operation)['status'],'source_timeout')
                self.assertEqual(queries.lookup(key,operation)['status'],'source_timeout')
                resumed=ResearchQueries(store,'task',3,90)
                self.assertEqual(resumed.attempts,1)
                self.assertEqual(resumed.lookup(key,operation)['status'],'already_attempted')
                resumed.deadline=0
                self.assertEqual(resumed.lookup(('lookup_quote','MSFT'),operation)['status'],'deadline_exceeded')
                operation.assert_called_once()
                self.assertNotIn('private provider text', str(store.traces('task')))
            finally:
                store.close()

    def test_model_call_limit_keeps_verified_quote(self):
        market = Mock(quote_age=900)
        market.info.return_value = {'regularMarketPrice': 100, 'regularMarketTime': time.time()}
        with TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory, OfflineModel(), settings=ModelConfig(key='',max_calls=2),
                                   research=ResearchService(market=market))
            try:
                result=runtime.run('AAPL 最新价格')
                self.assertEqual((result['status'], result['model_calls']), ('partial',2))
                self.assertIn('已有证据',result['text'])
                self.assertEqual(result['research']['evidence'][0]['quote']['price'],100)
            finally:
                runtime.close()

    def test_grounding_is_attachment_only_and_respects_known_fee_threshold(self):
        news=Mock()
        news.fetch.return_value={'text':'GROUNDING_BODY_CANARY','suggestions_html':'<p>fixture</p>','sources':[]}
        with TemporaryDirectory() as directory:
            runtime=AgentRuntime(directory,OfflineModel(),research=ResearchService(news=news))
            try:
                result=runtime.run('AAPL 新闻')
                self.assertEqual(result['news'][0]['text'],'GROUNDING_BODY_CANARY')
                self.assertNotIn('GROUNDING_BODY_CANARY',result['text'])
                self.assertNotIn('GROUNDING_BODY_CANARY',str(runtime.store.task(result['task_id'])))
                news.fetch.assert_called_once_with('AAPL')
                store=runtime.store
                record(store,'limit','supervisor','model','gemini-test',time.monotonic(),
                       {'input_tokens':2,'output_tokens':2},input_rate=1,output_rate=1)
                scope=Invocation('limit','owner',store=store,budget_micro=1,price_model='gemini-test',input_rate=1,output_rate=1)
                token=CURRENT.set(scope)
                try:
                    with patch('google.genai.Client') as provider, self.assertRaises(TaskBudgetExceeded):
                        GoogleNews('fixture','gemini-test').fetch('AAPL')
                    provider.assert_not_called()
                finally:CURRENT.reset(token)
            finally:
                runtime.close()

    def daily(self, steps, *, config=None, http=None):
        temp=TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store=Store(Path(temp.name)/'db')
        self.addCleanup(store.close)
        model=DailyModel(steps=steps)
        http=http if http is not None else Mock(**{'read.return_value': company_facts()})
        service=StockBriefing(store,config or BriefingConfig(),model,
                              registry={'issuers': {'AAA': {'name':'Alpha','cik':'1'}}, 'rss':[]}, http=http)
        return service,model,http

    def generate(self, service, sources=None):
        return service.generate('owner',{'source':'snaptrade','asof':NOW,'positions':{'AAA':{'shares':1}}},
                                now=NOW,sources=sources or [FilingSource()])

    def test_daily_chooses_financial_followup_and_separates_context_from_filing(self):
        service,model,http=self.daily([FINANCIAL,'final'])
        result=self.generate(service)
        self.assertEqual(result['meta']['budget']['calls'],2)
        self.assertEqual(result['meta']['research']['queries'],1)
        self.assertEqual(model.seen[1]['lookup_result']['financials']['revenue'],{**FACT,'tag':'us-gaap:Revenues'})
        self.assertIn('未读取正文',result['text'])
        self.assertIn('财务背景：AAA revenue 123 USD',result['text'])
        self.assertIn(FACT['source'],result['text'])
        self.assertIn('https://example.com/filing',result['text'])
        http.read.assert_called_once_with('https://data.sec.gov/api/xbrl/companyfacts/CIK0000000001.json',
                                         {'data.sec.gov'},max_bytes=16*1024*1024)

    def test_daily_source_failure_and_wrong_symbol_are_visible_and_safe(self):
        wrong={'lookup':{'tool':'lookup_financials','symbol':'BBB'}}
        http=Mock(**{'read.side_effect':TimeoutError('private body')})
        service,model,_=self.daily([wrong,FINANCIAL,'final'],http=http)
        result=self.generate(service)
        self.assertEqual(result['meta']['research']['queries'],1)
        self.assertEqual(model.seen[1]['lookup_result']['status'],'scope_denied')
        self.assertEqual(model.seen[2]['lookup_result']['status'],'source_timeout')
        self.assertIn('来源请求超时',result['text'])
        self.assertIn('https://example.com/filing',result['text'])
        self.assertNotIn('private body',result['text'])
        http.read.assert_called_once()

    def test_daily_retrieves_reviewed_evidence_outside_initial_shortlist(self):
        class Source(FilingSource):
            def fetch(self, now):
                return [Evidence('test-sec', f'{symbol} filing {n}', f'https://example.com/filing/{n}', now-n, now,
                                 f'Issuer {symbol} submitted synthetic filing {n}.', (symbol,))
                        for n,symbol in enumerate(('BBB','BBB','AAA'),1)]
        steps=[{'lookup':{'tool':'lookup_evidence','symbol':'AAA'}},'retrieved']
        service,model,http=self.daily(steps,config=BriefingConfig(max_events=1))
        snapshot={'source':'snaptrade','asof':NOW,'positions':{'AAA':{'shares':1},'BBB':{'shares':1}}}
        result=service.generate('owner',snapshot,now=NOW,sources=[Source()])
        self.assertTrue(all(row['entities']==['BBB'] for row in model.seen[0]['evidence']))
        self.assertEqual(result['meta']['selected'],1)
        self.assertIn('https://example.com/filing/3',result['text'])
        self.assertNotIn('https://example.com/filing/1',result['text'])
        http.read.assert_not_called()

    def test_daily_budget_stops_next_model_step_and_keeps_financial_background(self):
        service,model,http=self.daily([FINANCIAL],config=BriefingConfig(max_calls=1))
        result=self.generate(service)
        self.assertEqual(len(model.seen),1)
        self.assertEqual(result['meta']['budget']['calls'],1)
        self.assertIn('budget_exhausted',result['meta']['status'])
        self.assertIn('财务背景：AAA revenue',result['text'])
        self.assertIn('https://example.com/filing',result['text'])
        http.read.assert_called_once()

    def test_daily_report_cap_leaves_no_unfinishable_lookup(self):
        service,model,http=self.daily([FINANCIAL,FINANCIAL],config=BriefingConfig(max_report_model_calls=2))
        result=self.generate(service)
        self.assertEqual(result['meta']['budget']['calls'],2)
        self.assertIn('model_call_limit',result['meta']['status'])
        self.assertIn('财务背景：AAA revenue',result['text'])
        http.read.assert_called_once()


if __name__=='__main__':
    unittest.main()
