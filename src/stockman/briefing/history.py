"""Shared history and honest delivery outcomes, stored in PostgreSQL."""
import json
import time
import uuid

class History:
    def __init__(self,store):
        self.store=store
        with store.lock,store.conn.transaction():
            store.conn.execute("""
            CREATE TABLE IF NOT EXISTS brief_evidence(id TEXT PRIMARY KEY,fingerprint TEXT,event_key TEXT,metadata TEXT,at DOUBLE PRECISION);
            CREATE TABLE IF NOT EXISTS brief_reports(id TEXT PRIMARY KEY,owner TEXT,day TEXT,status TEXT,text TEXT,meta TEXT,at DOUBLE PRECISION);
            CREATE TABLE IF NOT EXISTS brief_report_items(report TEXT,evidence TEXT,PRIMARY KEY(report,evidence));
            """)
    def fresh(self,owner,items):
        result=[];seen=set()
        for item in items:
            if item.fingerprint in seen:continue
            seen.add(item.fingerprint)
            rows=self.store.execute("SELECT e.id FROM brief_evidence e JOIN brief_report_items i ON i.evidence=e.id JOIN brief_reports r ON r.id=i.report WHERE r.owner=%s AND r.status='sent' AND e.fingerprint=%s LIMIT 1",(owner,item.fingerprint))
            if not rows:result.append(item)
        return result
    def context(self,owner,event_key):
        if not event_key:return []
        rows=self.store.execute("SELECT e.metadata FROM brief_evidence e JOIN brief_report_items i ON i.evidence=e.id JOIN brief_reports r ON r.id=i.report WHERE r.owner=%s AND r.status='sent' AND e.event_key=%s ORDER BY r.at DESC LIMIT 1",(owner,event_key))
        return [json.loads(row['metadata']) for row in rows]
    def save(self,owner,day,items,policies,text,meta,*,store_analysis=True):
        identity=uuid.uuid4().hex
        can_store=store_analysis and all(policies[i.source].store_analysis for i in items)
        # Display-only analysis can also appear in metadata, including excerpts and research notes.
        stored_meta = meta if can_store else {key:value for key,value in meta.items() if key not in ('research','filing_citations')}
        with self.store.lock,self.store.conn.transaction():
            self.store.conn.execute('INSERT INTO brief_reports VALUES(%s,%s,%s,%s,%s,%s,%s)',(identity,owner,day,'preview',text if can_store else '[analysis display-only: source policy]',json.dumps(stored_meta,ensure_ascii=False),meta.get('asof',time.time())))
            for item in items:
                policy=policies[item.source];policy.validate()
                self.store.conn.execute('INSERT INTO brief_evidence VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET fingerprint=excluded.fingerprint,event_key=excluded.event_key,metadata=excluded.metadata,at=excluded.at',(item.identity,item.fingerprint,item.event_key,json.dumps(item.metadata(policy),ensure_ascii=False),time.time()))
                self.store.conn.execute('INSERT INTO brief_report_items VALUES(%s,%s)',(identity,item.identity))
        return identity
    def deliver(self,identity,result,send):
        # Persist before the side effect; interrupted sending must never be replayed blindly.
        with self.store.lock,self.store.conn.transaction():
            row=self.store.conn.execute('SELECT status FROM brief_reports WHERE id=%s FOR UPDATE',(identity,)).fetchone()
            if not row:raise ValueError('unknown report')
            if row['status']!='preview':return row['status']
            self.store.conn.execute("UPDATE brief_reports SET status='sending' WHERE id=%s",(identity,))
        try:status=send(result)
        except Exception:status='unknown'
        if status not in ('sent','failed','unknown'):status='unknown'
        self.store.execute('UPDATE brief_reports SET status=%s WHERE id=%s',(status,identity))
        return status
    def recover(self):self.store.execute("UPDATE brief_reports SET status='unknown' WHERE status='sending'")
    def prune(self,days,now):
        cutoff=now-days*86400
        with self.store.lock,self.store.conn.transaction():
            self.store.conn.execute('DELETE FROM brief_report_items WHERE report IN (SELECT id FROM brief_reports WHERE at<%s)',(cutoff,))
            self.store.conn.execute('DELETE FROM brief_reports WHERE at<%s',(cutoff,))
            self.store.conn.execute('DELETE FROM brief_evidence WHERE at<%s AND id NOT IN (SELECT evidence FROM brief_report_items)',(cutoff,))
