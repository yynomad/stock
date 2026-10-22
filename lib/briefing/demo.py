"""Synthetic, network-free protocol examples."""
from pathlib import Path
from .models import SourcePolicy,Evidence
from .history import History
from .pipeline import preview
from ..storage import Store

class SyntheticSource:
    policy=SourcePolicy('synthetic','https://example.com/policy',True,True,True,False,True)
    def fetch(self,now):
        return [Evidence('synthetic','合成事件：订单计划有更新','https://example.com/event',now-10,now,'Synthetic facts, not real financial evidence.',('DEMO',),'demo-order')]

def run(scenario,directory):
    if scenario!='pipeline':raise ValueError('generic demo only supports pipeline; Stock demos have their own module')
    store=Store(Path(directory)/'stockman.sqlite')
    try:
        result=preview(store,[SyntheticSource()],now=2000000000)
        sent=[]
        def send(report):sent.append(report);return 'sent'
        history=History(store)
        assert history.deliver(result['report_id'],result,send)=='sent'
        assert history.deliver(result['report_id'],result,send)=='sent' and len(sent)==1
        assert preview(store,[SyntheticSource()],now=2000000001)['item_count']==0
        return {'synthetic':True,'scenario':scenario,'deliveries':len(sent),'history_and_dedup':'passed','report_id':result['report_id']}
    finally:store.close()
