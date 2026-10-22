"""Holdings-scoped daily briefing with conservative novelty, evidence and visible degradation."""
from datetime import datetime
import time
from zoneinfo import ZoneInfo
from ..briefing.config import BriefingConfig
from ..briefing.history import History
from ..briefing.pipeline import collect
from ..briefing.budget import Budget
from .agent import ResearchAgent
from .relations import Relations,scope,relevant
from .sources import PublicHTTP,configured_sources,load_registry

def stamp(value,zone):return datetime.fromtimestamp(value,ZoneInfo(zone)).strftime('%Y-%m-%d %H:%M %Z')

def event_groups(items):
    groups={}
    for item in items:
        # Exact body duplicates are removed by History; explicit event keys group updates
        # without discarding distinct facts. No title-similarity suppression.
        key=(tuple(sorted(item.entities)),item.event_key or item.fingerprint)
        groups.setdefault(key,[]).append(item)
    return list(groups.values())

class StockBriefing:
    def __init__(self,store,config=None,model=None,registry=None,http=None):
        self.store=store;self.config=config or BriefingConfig.load();self.registry=registry if registry is not None else load_registry()
        self.history=History(store);self.relations=Relations(store);self.budget=Budget(store,self.config)
        self.research=ResearchAgent(store,self.config,model);self.http=http or PublicHTTP()
    def generate(self,owner,snapshot,now=None,day=None,sources=None):
        now=time.time() if now is None else now;config=self.config
        day=day or datetime.fromtimestamp(now,ZoneInfo(config.timezone)).date().isoformat()
        task=self.store.create_task(owner,'daily stock briefing')
        try:
            holdings=scope(snapshot,now)
            prior=self.relations.direct(holdings['symbols'],now)
            symbols=holdings['symbols']+[r['related'] for r in prior if not r['stale']]
            auto_sources=sources is None
            if auto_sources:sources,missing=configured_sources(self.registry,symbols,self.http)
            else:missing=[]
            items,policies,health=collect(sources,now,config.lookback_hours*3600)
            self.relations.discover(holdings['symbols'],items,policies,self.registry.get('issuers',{}),now)
            for proposal in self.registry.get('relationship_proposals',[]):self.relations.verify(proposal,items,policies,self.registry.get('issuers',{}),now)
            relations=self.relations.direct(holdings['symbols'],now)
            if auto_sources:
                new_symbols={r['related'] for r in relations if not r['stale']}-set(symbols)
                if new_symbols:
                    extra,extra_missing=configured_sources(self.registry,new_symbols,self.http)
                    extra=[s for s in extra if s.policy.source not in policies]
                    room=max(0,20-len(policies))
                    if len(extra)>room:extra_missing.append('new relation sources exceed per-run cap; partial coverage')
                    more,more_policies,more_health=collect(extra[:room],now,config.lookback_hours*3600)
                    items.extend(more);policies.update(more_policies);health.extend(more_health);missing.extend(extra_missing)
            scoped=relevant(items,holdings['symbols'],relations)
            reasons={i.identity:reason for i,reason in scoped}
            fresh=self.history.fresh(owner,[i for i,_ in scoped])
            groups=event_groups(sorted(fresh,key=lambda i:i.published,reverse=True))
            # Limit inputs independently of source text; never let a feed force unlimited model work.
            candidates=[group[0] for group in groups][:min(20,config.max_events*2)]
            grouped={group[0].identity:group for group in groups}
            allowed=[i for i in candidates if policies[i.source].analyze]
            explained,status=self.research.explain(task,allowed,self.budget,now,relations,holdings['symbols']) if allowed and config.budget else ([], 'model_disabled' if config.budget==0 else ('no_new_evidence' if not candidates else 'no_analyzable_new_evidence'))
            chosen={row['id']:row for row in explained}
            selected=([i for i in candidates if i.identity in chosen] if explained else candidates[:config.max_events])
            displayed=list(selected)
            status=status if explained or not allowed else status+'; deterministic_source_fallback'
            lines=[f'Stock 持仓简报 · {day}',f'资料截止：{stamp(now,config.timezone)}；主要覆盖最近 {config.lookback_hours} 小时。',
                f'持仓：{holdings["source"]}，时点 {stamp(holdings["asof"],config.timezone)}'+('（缓存）' if holdings['cached'] else '')+('（超过一天，需更新）' if holdings['stale'] else '')]
            previous=self.store.execute("SELECT at FROM brief_reports WHERE owner=? AND status='sent' ORDER BY at DESC LIMIT 1",(owner,))
            if previous and now-previous[0]['at']>config.lookback_hours*3600:lines.append('覆盖缺口：上次成功投递早于当前窗口，未补抓全部历史。')
            if not selected:lines.append('成功取得的来源和窗口内没有筛选出新增；不代表市场没有新闻。')
            for index,item in enumerate(selected,1):
                row=chosen.get(item.identity)
                lines.extend([f'\n{index}. {item.title}',f'关联：{reasons[item.identity]}；发布：{stamp(item.published,config.timezone)}'])
                lines.append('事件发生时间：'+(stamp(item.occurred,config.timezone) if item.occurred is not None else '未知（发布时间不等于发生时间）'))
                for old in self.history.context(owner,item.event_key):lines.append('此前报道背景：'+old['title']+' '+old['url'])
                if row:lines.extend(['事实原文摘录：'+row['quote'],'可能影响（推测）：'+row['impact'],'未知／反面证据：'+row['unknown'],'后续观察：'+row['watch']])
                else:
                    lines.append('未生成已验证的模型解释；持仓影响需另核实。')
                    if policies[item.source].analyze and item.body:lines.append('来源原文摘录（未作影响判断）：'+item.body[:300])
                lines.append(f'来源：{item.source} {item.url} [证据 {item.identity}]')
                for extra in grouped[item.identity][1:4]:
                    excerpt=extra.body if policies[extra.source].analyze else extra.title
                    lines.append(f'同事件的另一进展原文：{(excerpt or extra.title)[:300]}\n来源：{extra.source} {extra.url} [证据 {extra.identity}]')
                    displayed.append(extra)
            failed=[h['source'] for h in health if h['status']!='ok']
            if not health:missing.append('no configured sources for this scope')
            if len(groups)>len(selected):lines.append(f'还有 {len(groups)-len(selected)} 个新增事件组未展开；受篇幅／分析限制，不代表完整覆盖。')
            if failed:lines.append('来源异常／覆盖不完整：'+', '.join(failed))
            if missing:lines.append('覆盖缺失：'+'；'.join(missing))
            uncovered=[s for s in holdings['symbols'] if not any(r['held']==s and not r['stale'] for r in relations)]
            if uncovered:lines.append('暂无可核验直接上下游／竞争关系：'+', '.join(uncovered))
            stale=[r['related'] for r in relations if r['stale']]
            if stale:lines.append('关系证据已旧，未扩展采集：'+', '.join(stale))
            bill=self.budget.status(day)
            lines.extend([f'分析状态：{status}',f'当日模型费用估算 ${bill["estimated_usd"]:.6f} / 配置 ${config.budget:.4f}；用量未知 {bill["usage_unknown"]} 次。',bill['note']])
            text='\n'.join(lines)
            meta={'health':health,'holdings':holdings,'asof':now,'status':status,'budget':bill,'relations':relations,'event_groups':len(groups),'selected':len(selected)}
            identity=self.history.save(owner,day,displayed,policies,text,meta)
            result={'task_id':task,'report_id':identity,'text':text,'status':'partial' if failed or missing or holdings['stale'] or status not in ('explained','no_new_evidence') else 'completed','meta':meta}
            self.store.finish(task,result['status'],{'report_id':identity,'budget':bill,'health':health})
            self.history.prune(config.retention_days,now)
            return result
        except Exception as exc:
            self.store.finish(task,'failed',error=type(exc).__name__);raise
