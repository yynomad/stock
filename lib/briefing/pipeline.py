"""Deterministic collection, quality reporting and a minimal domain-free preview."""
import time
from .history import History

def collect(sources,now=None,lookback=86400):
    now=time.time() if now is None else now
    items=[];health=[];policies={}
    for source in sources:
        policy=source.policy
        if policy.source in policies:raise ValueError('duplicate source identifier')
        policies[policy.source]=policy
        try:
            policy.validate();rows=source.fetch(now)
            accepted=[];rejected=0
            for item in rows:
                try:
                    item.validate(now)
                    if item.source!=policy.source:raise ValueError('source identity mismatch')
                    if now-lookback<=item.published<=now:accepted.append(item)
                except (ValueError,TypeError):rejected+=1
            items.extend(accepted)
            health.append({'source':policy.source,'status':'partial' if rejected else 'ok','accepted':len(accepted),'invalid':rejected})
        except Exception as exc:health.append({'source':policy.source,'status':'failed','error_type':type(exc).__name__})
    return items,policies,health

def preview(store,sources,owner='local',day='demo',now=None):
    items,policies,health=collect(sources,now)
    history=History(store);items=history.fresh(owner,items)
    text='\n'.join([f'{i.title}\n{i.url}' for i in items]) or '本次成功取得的来源和时间窗内未筛选出新增。'
    result={'text':text,'health':health,'item_count':len(items)}
    identity=history.save(owner,day,items,policies,text,{'health':health})
    result['report_id']=identity
    return result
