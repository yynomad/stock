import json
import unittest
from types import SimpleNamespace as NS
from lib.market import choose_quote,regular_session
from lib.research import financial_facts,parse_grounding,news_html,ResearchService,symbol_from_request

class ResearchTest(unittest.TestCase):
    def test_newer_regular_quote_beats_stale_premarket(self):
        quote=choose_quote({'preMarketPrice':90,'preMarketTime':100,'regularMarketPrice':105,'regularMarketTime':990},now=1000)
        self.assertEqual((quote['kind'],quote['price']),('regular',105))
        self.assertTrue(quote['valid'])
    def test_bad_quote_times_and_prices_do_not_trigger(self):
        for info in [{'regularMarketPrice':10},{'regularMarketPrice':float('nan'),'regularMarketTime':1000},{'regularMarketPrice':10,'regularMarketTime':2000}]:
            self.assertFalse(choose_quote(info,now=1000)['valid'])
        self.assertFalse(choose_quote({'regularMarketPrice':10,'regularMarketTime':1},now=10000)['valid'])
    def test_holiday_and_regular_session(self):
        from datetime import datetime
        self.assertFalse(regular_session(datetime.fromisoformat('2026-12-25T16:00:00+00:00').timestamp()))
        self.assertTrue(regular_session(datetime.fromisoformat('2026-10-22T16:00:00+00:00').timestamp()))
    def test_financial_periods_and_latest_revision(self):
        def point(start,end,filed,value):return {'start':start,'end':end,'filed':filed,'val':value,'form':'10-Q','accn':'0000320193-26-000001'}
        points=[point('2026-01-01','2026-06-30','2026-07-20',200),point('2026-04-01','2026-06-30','2026-07-20',100),point('2026-04-01','2026-06-30','2026-08-01',110)]
        result=financial_facts({'facts':{'us-gaap':{'Revenues':{'units':{'USD':points}}}}},320193)
        self.assertEqual(result['revenue']['value'],110)
        self.assertEqual(result['revenue']['period'],'quarter')
        self.assertTrue(result['assets']['missing'])
        self.assertIn('sec.gov',result['revenue']['source'])
    def test_grounding_requires_actual_sources_and_suggestions(self):
        metadata=NS(grounding_chunks=[NS(web=NS(uri='https://example.com/article',title='Source'))],grounding_supports=[NS(grounding_chunk_indices=[0])],search_entry_point=NS(rendered_content='<div>Search suggestions</div>'),web_search_queries=['AAPL news'])
        response=NS(candidates=[NS(grounding_metadata=metadata)],text='Original news',usage_metadata=None)
        bundle=parse_grounding(response);self.assertIn('Original news',news_html(bundle))
        self.assertIn('<div>Search suggestions</div>',news_html(bundle))
        metadata.grounding_supports[0].grounding_chunk_indices=[3]
        with self.assertRaises(ValueError):parse_grounding(response)
        metadata.grounding_supports=[]
        with self.assertRaises(ValueError):parse_grounding(response)
    def test_etf_does_not_use_company_fundamentals(self):
        class Market:
            def info(self,symbol):return {'quoteType':'ETF','longName':'Synthetic ETF'}
            def bars(self,symbol):return []
        class SEC:
            def facts(self,symbol):raise AssertionError('ETF must not request company finances')
        class News:
            def fetch(self,symbol):raise TimeoutError()
        result=json.loads(ResearchService(Market(),SEC(),News()).collect('研究 QQQ'))
        self.assertEqual(result['kind'],'ETF');self.assertIn('not_applicable',result['financials'])
        self.assertTrue(any('新闻' in msg for msg in result['missing']))
    def test_research_rejects_ambiguous_symbols(self):
        self.assertEqual(symbol_from_request('研究 AAPL'),'AAPL')
        with self.assertRaises(ValueError):symbol_from_request('研究 AAPL NVDA')

    def test_grounded_news_is_not_put_in_checkpoints_or_business_records(self):
        import tempfile
        from pathlib import Path
        from lib.agents import AgentRuntime,OfflineModel
        class Market:
            def info(self,symbol):return {'quoteType':'ETF'}
            def bars(self,symbol):return []
        class News:
            def fetch(self,symbol):return {'text':'UNIQUE-GROUNDED-TEXT','sources':[],'suggestions_html':'test'}
        with tempfile.TemporaryDirectory() as d:
            service=ResearchService(market=Market(),news=News())
            runtime=AgentRuntime(d,OfflineModel(),{'research':service.collect})
            result=runtime.run('研究 QQQ')
            self.assertEqual(result['news'][0]['text'],'UNIQUE-GROUNDED-TEXT')
            self.assertNotIn('UNIQUE-GROUNDED-TEXT',str(runtime.store.task(result['task_id'])))
            runtime.close()
            for path in Path(d).glob('*.sqlite'):
                self.assertNotIn(b'UNIQUE-GROUNDED-TEXT',path.read_bytes())
