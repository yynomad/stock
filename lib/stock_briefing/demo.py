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
