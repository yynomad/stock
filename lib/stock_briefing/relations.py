"""One-hop relationships verified against an exact, explicit sentence, never model memory."""
import json
import re

class Relations:
    def __init__(self,store):
        self.store=store
        store.execute('CREATE TABLE IF NOT EXISTS brief_relations(held TEXT,related TEXT,kind TEXT,evidence TEXT,verified REAL,published REAL,PRIMARY KEY(held,related,kind))')
    def verify(self,proposal,items,policies,issuers,now):
        held=proposal.get('held');related=proposal.get('related');kind=proposal.get('kind')
        if held==related or held not in issuers or related not in issuers or kind not in ('supplier','customer','competitor'):return False
        item=next((i for i in items if i.identity==proposal.get('evidence_id')),None)
        quote=proposal.get('quote','')
        if item is not None:item.validate(now)
        if item is None or not policies[item.source].store_analysis or not policies[item.source].analyze or not isinstance(quote,str) or len(quote)<12 or quote not in item.body:return False
        # A strict supported grammar intentionally sacrifices recall. Mere co-occurrence is insufficient.
        # Both legal names must be supplied in trusted configuration, avoiding ambiguous ticker aliases.
        a=re.escape(issuers[held]['name']);b=re.escape(issuers[related]['name'])
        patterns={'supplier':fr'{b} (?:is a supplier to|supplies) {a}',
                  'customer':fr'{b} (?:is a customer of|buys from) {a}',
                  'competitor':fr'{b} (?:is a competitor of|competes with) {a}'}
        if not re.fullmatch(patterns[kind]+r'[.]?',quote,re.IGNORECASE):return False
        record={'quote':quote,'url':item.url,'source':item.source,'evidence_id':item.identity}
        self.store.execute('INSERT OR REPLACE INTO brief_relations VALUES(?,?,?,?,?,?)',(held,related,kind,json.dumps(record),now,item.published))
        return True
    def discover(self,holdings,items,policies,issuers,now):
        accepted=0
        if len(issuers)>100:raise ValueError('at most 100 configured issuers')
        for held in holdings:
            if held not in issuers:continue
            for related in issuers:
                if related==held:continue
                a=re.escape(issuers[held]['name']);b=re.escape(issuers[related]['name'])
                for kind,verbs in [('supplier','is a supplier to|supplies'),('customer','is a customer of|buys from'),('competitor','is a competitor of|competes with')]:
                    pattern=fr'(?<![\w]){b} (?:{verbs}) {a}[.]?(?![\w])'
                    for item in items:
                        # Require a complete standalone sentence, not a quote cut out of a negation.
                        for sentence in re.split(r'[\n!?]+|(?<=\.)\s+',item.body):
                            if re.fullmatch(pattern,sentence.strip(),re.IGNORECASE):
                                accepted+=bool(self.verify({'held':held,'related':related,'kind':kind,'quote':sentence.strip(),'evidence_id':item.identity},items,policies,issuers,now))
        return accepted
    def direct(self,holdings,now,max_age=180*86400):
        rows=self.store.execute('SELECT * FROM brief_relations ORDER BY held,related,kind')
        return [{**r,'evidence':json.loads(r['evidence']),'stale':now-r['published']>max_age} for r in rows if r['held'] in holdings]

def scope(snapshot,now):
    from ..portfolio import normalize_positions
    positions=normalize_positions(snapshot['positions'])
    asof=snapshot['asof']
    if isinstance(asof,bool) or not isinstance(asof,(int,float)) or not 0<asof<=now+30:raise ValueError('invalid holdings time')
    return {'symbols':sorted(positions),'source':snapshot['source'],'asof':asof,
            'cached':bool(snapshot.get('cached')),'stale':now-asof>86400}

def relevant(items,holdings,relations):
    direct={r['related']:r['held'] for r in relations if not r['stale'] and r['held'] in holdings}
    result=[]
    for item in items:
        entities=set(item.entities)
        if entities & set(holdings):reason='holding'
        elif entities & set(direct):reason='verified_direct_relation'
        elif 'macro' in entities:reason='macro_context; impact depends on exposure, not a company-specific fact'
        else:continue
        result.append((item,reason))
    return result
