"""Synthetic stock evidence; not real investment facts."""
from pathlib import Path
from ..storage import Store
from ..briefing.models import Evidence,SourcePolicy
from ..briefing.pipeline import collect
from .relations import Relations

class StockSource:
    policy=SourcePolicy('synthetic-stock','https://example.com/policy',True,True,True,False,True)
    def fetch(self,now):
        return [Evidence(self.policy.source,'合成：订单更新','https://example.com/fact',now-10,now,'Beta Corp supplies Alpha Corp.',('AAA','BBB'),'synthetic-event')]
class BrokenSource:
    policy=SourcePolicy('synthetic-broken','https://example.com/policy',True,True,True,False,True)
    def fetch(self,now):raise TimeoutError()

def sources_demo(directory):
    store=Store(Path(directory)/'stockman.sqlite')
    try:
        now=2000000000
        items,policies,health=collect([StockSource(),BrokenSource()],now)
        relations=Relations(store)
        proposal={'held':'AAA','related':'BBB','kind':'supplier','quote':items[0].body,'evidence_id':items[0].identity}
        issuers={'AAA':{'name':'Alpha Corp'},'BBB':{'name':'Beta Corp'}}
        assert relations.verify(proposal,items,policies,issuers,now)
        assert not relations.verify({**proposal,'quote':'unsupported claim'},items,policies,issuers,now)
        return {'synthetic':True,'scenario':'sources','health':health,'direct_relations':relations.direct(['AAA'],now),'unsupported_relation':'rejected'}
    finally:store.close()

def research_demo(directory,budget_zero=False):
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration,ChatResult
    import json
    from ..briefing.config import BriefingConfig
    from .service import StockBriefing
    class SyntheticResearch(BaseChatModel):
        @property
        def _llm_type(self):return 'synthetic-stock-briefing'
        def _generate(self,messages,**kwargs):
            item=json.loads(messages[-1].content)['evidence'][0]
            output={'events':[{'id':item['id'],'quote':item['body'],'impact':'供应关系可能影响订单，需核实。','unknown':'未取得财务影响证据。','watch':'关注后续公告。'}]}
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(output),usage_metadata={'input_tokens':100,'output_tokens':100,'total_tokens':200}))])
    store=Store(Path(directory)/'stockman.sqlite')
    try:
        service=StockBriefing(store,BriefingConfig(budget=0 if budget_zero else 0.20),model=SyntheticResearch(),registry={'issuers':{'AAA':{'name':'Alpha Corp'},'BBB':{'name':'Beta Corp'}},'rss':[]})
        snapshot={'source':'image','asof':2000000000,'positions':{'AAA':{'shares':2}}}
        first=service.generate('local',snapshot,2000000000,sources=[StockSource(),BrokenSource()])
        assert ('model_disabled' in first['text']) if budget_zero else ('事实原文摘录' in first['text'])
        assert first['meta']['budget']['calls']==(0 if budget_zero else 1)
        service.history.deliver(first['report_id'],first,lambda _: 'sent')
        second=service.generate('local',snapshot,2000000001,sources=[StockSource()])
        assert second['meta']['selected']==0
        return {'synthetic':True,'scenario':'budget' if budget_zero else 'research','text':first['text'],'budget':first['meta']['budget'],'sent_news_dedup':'passed'}
    finally:store.close()

def schedule_demo(directory):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from ..briefing.config import BriefingConfig
    from ..briefing.schedule import Scheduler
    from ..briefing.pipeline import preview
    store=Store(Path(directory)/'stockman.sqlite');sent=[]
    def at(value):return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    try:
        scheduler=Scheduler(store,BriefingConfig(enabled=True),'123',{'123'},lambda owner,now,day:preview(store,[StockSource()],owner,day,now),lambda r:sent.append(r) or 'sent')
        assert scheduler.tick(at('2026-10-22T07:59:00'))['status']=='not_due'
        assert scheduler.tick(at('2026-10-22T08:00:00'))['status']=='sent'
        scheduler.recover();assert scheduler.tick(at('2026-10-22T09:00:00'))['status']=='sent'
        delayed=scheduler.tick(at('2026-10-25T12:00:00'))
        assert delayed['status']=='sent' and delayed['delayed'] and len(sent)==2
        assert len(store.execute('SELECT * FROM brief_daily'))==2
        return {'synthetic':True,'scenario':'schedule','deliveries':len(sent),'today_only_catchup':delayed,'restart_dedup':'passed'}
    finally:store.close()

def end_to_end(directory):
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    from dataclasses import replace
    import json
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration,ChatResult
    from ..briefing.config import BriefingConfig
    from ..briefing.schedule import Scheduler
    from ..portfolio import PortfolioService
    from .service import StockBriefing
    class Model(BaseChatModel):
        @property
        def _llm_type(self):return 'synthetic-daily-end-to-end'
        def _generate(self,messages,**kwargs):
            data=json.loads(messages[-1].content)
            rows=[{'id':i['id'],'quote':i['body'][:200],'impact':'存在间接影响的可能，需后续验证。','unknown':'未核实持仓的收入影响。','watch':'关注公司下一份披露。'} for i in data['evidence'][:data['max_events']]]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps({'events':rows}),usage_metadata={'input_tokens':200,'output_tokens':200,'total_tokens':400}))])
    class FixtureHTTP:
        def __init__(self):self.now=0;self.calls=[]
        def read(self,url,hosts):
            self.calls.append(url)
            if 'submissions' in url:
                cik='0000000123' if '0000000123' in url else '0000000456'
                data={'filings':{'recent':{'form':['8-K'],'accessionNumber':[cik+'-26-000001'],'primaryDocument':['update.htm'],'acceptanceDateTime':[datetime.fromtimestamp(self.now-10,timezone.utc).isoformat()]}}}
                return json.dumps(data).encode()
            if url.endswith('/broken'):raise TimeoutError()
            published=datetime.fromtimestamp(self.now-10,timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
            return f'<rss><channel><item><title>合成：直接供应关系</title><link>https://example.com/relationship</link><pubDate>{published}</pubDate><description>Beta Corp supplies Alpha Corp.</description></item></channel></rss>'.encode()
    def at(value):return datetime.fromisoformat(value).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
    http=FixtureHTTP();sent=[];config=BriefingConfig(enabled=True)
    registry={'issuers':{'AAA':{'name':'Alpha Corp','cik':'123'},'BBB':{'name':'Beta Corp','cik':'456'}},'rss':[
        {'url':'https://example.com/relationships','symbols':['AAA'],'relationships':True,'policy':{'source':'synthetic-relations','review_url':'https://example.com/policy','reviewed':True,'analyze':True,'store_metadata':True,'store_analysis':True}},
        {'url':'https://example.com/broken','macro':True,'policy':{'source':'synthetic-unavailable','review_url':'https://example.com/policy','reviewed':True,'analyze':True,'store_metadata':True,'store_analysis':True}}]}
    store=Store(Path(directory)/'stockman.sqlite')
    try:
        portfolio=PortfolioService(store,ibkr=lambda account:{'AAA':{'shares':0.5}},image=lambda path:{'AAA':{'shares':2}})
        # Import the screenshot once; source scopes are independent.
        image=portfolio.import_image('SYNTHETIC','123')
        store.execute('UPDATE snapshots SET asof=? WHERE id=?',(at('2026-10-22T07:00:00'),image))
        service=StockBriefing(store,config,Model(),registry,http)
        def build(owner,now,day):
            http.now=now
            return service.generate(owner,portfolio.load(owner,image),now,day)
        scheduler=Scheduler(store,config,'123',{'123'},build,lambda result:sent.append(result) or 'sent')
        assert scheduler.tick(at('2026-10-22T07:59:00'))['status']=='not_due'
        first=scheduler.tick(at('2026-10-22T08:00:00'));assert first['status']=='sent',first
        assert any('0000000456' in url for url in http.calls) # newly verified one-hop issuer, same run
        assert '来源异常' in sent[0]['text'] and '事实原文摘录' in sent[0]['text']
        assert len(service.relations.direct(['AAA'],http.now))==1
        # Same facts are not suppressed just because a preview ran, but sent evidence is.
        http.now=at('2026-10-22T09:00:00')
        preview=build('123',http.now,'2026-10-22');assert preview['meta']['selected']==0
        store.close();store=Store(Path(directory)/'stockman.sqlite')
        service=StockBriefing(store,config,Model(),registry,http)
        portfolio=PortfolioService(store,ibkr=lambda account:{'AAA':{'shares':0.5}})
        scheduler=Scheduler(store,config,'123',{'123'},build,lambda result:sent.append(result) or 'sent')
        scheduler.recover();assert scheduler.tick(at('2026-10-22T10:00:00'))['status']=='sent'
        delayed=scheduler.tick(at('2026-10-25T12:00:00'));assert delayed['status']=='sent' and delayed['delayed']
        assert len(store.execute('SELECT * FROM brief_daily'))==2 and len(sent)==2
        assert '缓存' not in sent[0]['text'] # explicit original screenshot, not a merged broker source
        # Separate fake day simulates partial network delivery; recovery must stop replay.
        scheduler.send=lambda result:'unknown'
        assert scheduler.tick(at('2026-10-26T08:00:00'))['status']=='unknown'
        scheduler.recover();scheduler.send=lambda result:(_ for _ in ()).throw(AssertionError('replay'))
        assert scheduler.tick(at('2026-10-26T09:00:00'))['status']=='unknown'
        return {'synthetic':True,'scenario':'end-to-end','deliveries':len(sent),'sources':['image (synthetic)','SEC adapter (synthetic)','RSS adapter (synthetic)'],'relationship_hops':1,'today_only_catchup':'passed','restart_and_dedup':'passed','unconfirmed_delivery':'unknown','budget':service.budget.status('2026-10-22')}
    finally:store.close()
