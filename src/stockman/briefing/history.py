"""Shared history and honest delivery outcomes, built on the existing SQLite store."""
import json
import time
import uuid

class History:
    def __init__(self,store):
        self.store=store
        with store.lock,store.conn:
            store.conn.executescript("""
            CREATE TABLE IF NOT EXISTS brief_evidence(id TEXT PRIMARY KEY,fingerprint TEXT,event_key TEXT,metadata TEXT,at REAL);
            CREATE TABLE IF NOT EXISTS brief_reports(id TEXT PRIMARY KEY,owner TEXT,day TEXT,status TEXT,text TEXT,meta TEXT,at REAL);
            CREATE TABLE IF NOT EXISTS brief_report_items(report TEXT,evidence TEXT,PRIMARY KEY(report,evidence));
            """)
    def fresh(self,owner,items):
        result=[];seen=set()
        for item in items:
            if item.fingerprint in seen:continue
            seen.add(item.fingerprint)
            rows=self.store.execute("SELECT e.id FROM brief_evidence e JOIN brief_report_items i ON i.evidence=e.id JOIN brief_reports r ON r.id=i.report WHERE r.owner=? AND r.status='sent' AND e.fingerprint=? LIMIT 1",(owner,item.fingerprint))
            if not rows:result.append(item)
        return result
    def context(self,owner,event_key):
        if not event_key:return []
        rows=self.store.execute("SELECT e.metadata FROM brief_evidence e JOIN brief_report_items i ON i.evidence=e.id JOIN brief_reports r ON r.id=i.report WHERE r.owner=? AND r.status='sent' AND e.event_key=? ORDER BY r.at DESC LIMIT 1",(owner,event_key))
        return [json.loads(row['metadata']) for row in rows]
    def save(self,owner,day,items,policies,text,meta):
        identity=uuid.uuid4().hex
        can_store=all(policies[i.source].store_analysis for i in items)
        with self.store.lock,self.store.conn:
            self.store.conn.execute('INSERT INTO brief_reports VALUES(?,?,?,?,?,?,?)',(identity,owner,day,'preview',text if can_store else '[analysis display-only: source policy]',json.dumps(meta,ensure_ascii=False),meta.get('asof',time.time())))
            for item in items:
                policy=policies[item.source];policy.validate()
                self.store.conn.execute('INSERT OR REPLACE INTO brief_evidence VALUES(?,?,?,?,?)',(item.identity,item.fingerprint,item.event_key,json.dumps(item.metadata(policy),ensure_ascii=False),time.time()))
                self.store.conn.execute('INSERT INTO brief_report_items VALUES(?,?)',(identity,item.identity))
        return identity
    def deliver(self,identity,result,send):
        # Persist before the side effect; interrupted sending must never be replayed blindly.
        with self.store.lock,self.store.conn:
            row=self.store.conn.execute('SELECT status FROM brief_reports WHERE id=?',(identity,)).fetchone()
            if not row:raise ValueError('unknown report')
            if row['status']!='preview':return row['status']
            self.store.conn.execute("UPDATE brief_reports SET status='sending' WHERE id=?",(identity,))
        try:status=send(result)
        except Exception:status='unknown'
        if status not in ('sent','failed','unknown'):status='unknown'
        self.store.execute('UPDATE brief_reports SET status=? WHERE id=?',(status,identity))
        return status
    def recover(self):self.store.execute("UPDATE brief_reports SET status='unknown' WHERE status='sending'")
    def prune(self,days,now):
        cutoff=now-days*86400
        with self.store.lock,self.store.conn:
            self.store.conn.execute('DELETE FROM brief_report_items WHERE report IN (SELECT id FROM brief_reports WHERE at<?)',(cutoff,))
            self.store.conn.execute('DELETE FROM brief_reports WHERE at<?',(cutoff,))
            self.store.conn.execute('DELETE FROM brief_evidence WHERE at<? AND id NOT IN (SELECT evidence FROM brief_report_items)',(cutoff,))
