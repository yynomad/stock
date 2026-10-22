from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from lib.storage import Store
from lib.briefing.budget import Budget,BudgetExceeded
from lib.briefing.config import BriefingConfig
from lib.stock_briefing.agent import validate_output,ResearchAgent
from lib.stock_briefing.demo import StockSource,BrokenSource
from lib.stock_briefing.service import StockBriefing,event_groups

class BriefModel(BaseChatModel):
    bad:bool=False
    def _llm_type(self):return 'synthetic-briefing'
    def _generate(self,messages,**kwargs):
        data=json.loads(messages[-1].content);item=data['evidence'][0]
        row={'id':item['id'],'quote':item['body'],'impact':'可能传导至订单，需后续核实。','unknown':'尚未核实收入影响。','watch':'关注下一次公司公告。'}
        if self.bad:row['impact']='利润增长 99999%'
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps({'events':[row]}),usage_metadata={'input_tokens':100,'output_tokens':100,'total_tokens':200}))])

class BriefingResearchTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db')
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_invalid_budget_schedule_and_zero_never_calls_model(self):
        for value in ['nan','inf','-1','oops']:
            with self.assertRaises(ValueError):BriefingConfig.load({'BRIEFING_DAILY_BUDGET_USD':value})
        with self.assertRaises(ValueError):BriefingConfig.load({'BRIEFING_TIME':'25:00'})
        service=StockBriefing(self.store,BriefingConfig(budget=0),registry={'issuers':{},'rss':[]})
        result=service.generate('local',{'source':'image','asof':2000000000,'positions':{'AAA':{'shares':2,'avg_cost':99}}},2000000000,sources=[StockSource()])
        self.assertIn('model_disabled',result['text']);self.assertFalse(self.store.execute('SELECT * FROM brief_budget'))
    def test_atomic_budget_across_connections_days_and_unknown_usage(self):
        config=BriefingConfig(budget=0.006,max_calls=8,max_output=2048)
        other=Store(self.store.path);b1=Budget(self.store,config);b2=Budget(other,config)
        try:
            def reserve(b):
                try:return b.reserve('d1',100)
                except BudgetExceeded:return None
            with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(reserve,[b1,b2]))
            self.assertEqual(sum(bool(i) for i in ids),1)
            identity=next(i for i in ids if i);b1.settle(identity)
            self.assertEqual(b1.status('d1')['usage_unknown'],1);self.assertGreater(b1.status('d1')['estimated_usd'],0)
            self.assertTrue(b2.reserve('d2',100))
        finally:other.close()
    def test_quote_unknown_reference_and_financial_numbers_rejected(self):
        item=StockSource().fetch(2000000000)[0]
        row={'id':item.identity,'quote':item.body,'impact':'可能变化','unknown':'未知','watch':'关注公告'}
        for change in [{'id':'invented'},{'quote':'Revenue 50'},{'impact':'利润 999%'},{'watch':'https://evil.example/a'}]:
            with self.assertRaises(ValueError):validate_output(json.dumps({'events':[{**row,**change}]}),[item],5)
        self.assertEqual(len(validate_output(json.dumps({'events':[row]}),[item],5)),1)
    def test_real_langchain_daily_role_keeps_evidence_and_failures_visible(self):
        service=StockBriefing(self.store,BriefingConfig(),model=BriefModel(),registry={'issuers':{'AAA':{'name':'Alpha Corp'},'BBB':{'name':'Beta Corp'}},'rss':[]})
        result=service.generate('local',{'source':'image','asof':2000000000,'positions':{'AAA':{'shares':2,'avg_cost':99}}},2000000000,sources=[StockSource(),BrokenSource()])
        self.assertIn('事实原文摘录',result['text']);self.assertIn('来源异常',result['text'])
        self.assertNotIn('avg_cost',str(self.store.traces(result['task_id'])))
        self.assertEqual(result['meta']['budget']['calls'],1)
        self.assertEqual(result['meta']['budget']['usage_unknown'],0)
    def test_invalid_model_output_falls_back_without_retry(self):
        service=StockBriefing(self.store,BriefingConfig(),model=BriefModel(bad=True),registry={'issuers':{},'rss':[]})
        result=service.generate('local',{'source':'ibkr','asof':2000000000,'positions':{'AAA':{'shares':2}}},2000000000,sources=[StockSource()])
        self.assertIn('deterministic_source_fallback',result['text']);self.assertNotIn('99999',result['text'])
        self.assertEqual(result['meta']['budget']['calls'],1)
    def test_event_grouping_keeps_new_facts_and_external_instructions_have_no_tools(self):
        item=StockSource().fetch(2000000000)[0]
        update=replace(item,body='new fact 42')
        groups=event_groups([item,update]);self.assertEqual(len(groups),1);self.assertEqual(len(groups[0]),2)
        # Only exact evidence can pass as fact, and the daily role exposes no write/send tool.
        self.assertNotEqual(item.fingerprint,update.fingerprint)
    def test_unknown_timeout_consumes_budget_before_retry(self):
        class TimeoutModel(BriefModel):
            def _generate(self,*args,**kwargs):raise TimeoutError('secret')
        budget=Budget(self.store,BriefingConfig(budget=1))
        task=self.store.create_task('local','test')
        output,status=ResearchAgent(self.store,BriefingConfig(budget=1),TimeoutModel()).explain(task,StockSource().fetch(2000000000),budget,2000000000)
        day='2033-05-18'
        rows=self.store.execute('SELECT * FROM brief_budget')
        self.assertEqual(len(rows),2);self.assertTrue(all(r['state']=='usage_unknown' and r['charge']>0 for r in rows))
        self.assertNotIn('secret',str(self.store.traces(task)))

    def test_same_event_updates_displayed_and_only_displayed_items_marked_sent(self):
        class Updates(StockSource):
            def fetch(self,now):
                item=super().fetch(now)[0]
                return [item,replace(item,body='New factual update 42',published=now-5)]
        service=StockBriefing(self.store,BriefingConfig(budget=0),registry={'issuers':{},'rss':[]})
        snapshot={'source':'image','asof':2000000000,'positions':{'AAA':{'shares':2}}}
        result=service.generate('local',snapshot,2000000000,sources=[Updates()])
        self.assertIn('New factual update 42',result['text']);self.assertIn('Beta Corp supplies Alpha Corp.',result['text'])
        self.assertEqual(len(self.store.execute('SELECT * FROM brief_report_items')),2)
    def test_sdk_retry_attempts_and_thinking_usage_contract(self):
        from langchain_google_genai import ChatGoogleGenerativeAI
        from langchain_core.messages import HumanMessage
        model=ChatGoogleGenerativeAI(model='gemini-3.5-flash-lite',api_key='offline-test',max_retries=1,max_output_tokens=2048,timeout=30)
        try:
            request=model._prepare_request([HumanMessage(content='test')])
            self.assertEqual(request['config'].http_options.retry_options.attempts,1)
            self.assertEqual(request['config'].max_output_tokens,2048)
        finally:model.client.close()

    def test_numbers_with_wrong_units_rejected_even_if_digit_occurs_in_source(self):
        item=replace(StockSource().fetch(2000000000)[0],body='Revenue was 99 USD.')
        row={'id':item.identity,'quote':item.body,'impact':'收入上涨 99%','unknown':'未知','watch':'关注公告'}
        with self.assertRaises(ValueError):validate_output(json.dumps({'events':[row]}),[item],5)
    def test_no_news_receipt_and_missing_sources_are_distinct(self):
        class Empty(StockSource):
            def fetch(self,now):return []
        service=StockBriefing(self.store,BriefingConfig(),registry={'issuers':{},'rss':[]})
        snapshot={'source':'image','asof':2000000000,'positions':{'AAA':{'shares':1}}}
        empty=service.generate('local',snapshot,2000000000,sources=[Empty()])
        failed=service.generate('local',snapshot,2000000000,sources=[BrokenSource()])
        self.assertEqual(empty['status'],'completed');self.assertEqual(failed['status'],'partial')
        self.assertNotIn('来源异常',empty['text']);self.assertIn('来源异常',failed['text'])

    def test_exhausted_call_limit_and_settlement_keeps_reasoning_once(self):
        budget=Budget(self.store,BriefingConfig(budget=1,max_calls=1))
        identity=budget.reserve('day',100)
        budget.settle(identity,{'input_tokens':100,'output_tokens':200,'output_token_details':{'reasoning':100}})
        self.assertEqual(budget.status('day')['estimated_usd'],0.00053)
        with self.assertRaises(BudgetExceeded):budget.reserve('day',100)
    def test_invalid_reference_type_is_validation_failure(self):
        item=StockSource().fetch(2000000000)[0]
        row={'id':[],'quote':item.body,'impact':'可能变化','unknown':'未知','watch':'关注公告'}
        with self.assertRaises(ValueError):validate_output(json.dumps({'events':[row]}),[item],5)
    def test_identical_boilerplate_for_different_entities_is_not_deduplicated(self):
        item=StockSource().fetch(2000000000)[0]
        other=replace(item,entities=('CCC',))
        self.assertNotEqual(item.fingerprint,other.fingerprint)
