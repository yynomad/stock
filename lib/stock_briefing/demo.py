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
