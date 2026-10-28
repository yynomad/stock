"""Calendar-day schedule, today-only catch-up, persisted claim before any external send."""
from datetime import datetime
import json
from zoneinfo import ZoneInfo
from .history import History

class Scheduler:
    def __init__(self,store,config,owner,allowed,build,send):
        if not str(owner).isdigit() or int(owner)<=0 or str(owner) not in {str(x) for x in allowed}:raise PermissionError('daily briefing requires the configured private owner')
        self.store=store;self.config=config;self.owner=str(owner);self.build=build;self.send=send;self.history=History(store)
        store.execute('CREATE TABLE IF NOT EXISTS brief_daily(owner TEXT,day TEXT,scheduled REAL,actual REAL,report TEXT,status TEXT,attempts INTEGER,next_try REAL,error TEXT,PRIMARY KEY(owner,day))')
    def recover(self):
        # Call once after acquiring the runtime process lock, never from a polling loop.
        self.history.recover()
        self.store.execute("UPDATE brief_daily SET status='unknown',error='interrupted; verify delivery manually' WHERE status IN ('generating','sending')")
    def tick(self,now,force=False):
        if not self.config.enabled and not force:return {'status':'disabled'}
        local=datetime.fromtimestamp(now,ZoneInfo(self.config.timezone));day=local.date().isoformat()
        hour,minute=map(int,self.config.at.split(':'))
        scheduled=local.replace(hour=hour,minute=minute,second=0,microsecond=0).timestamp()
        if now<scheduled and not force:return {'status':'not_due'}
        # Cross-connection serialization also prevents two threads claiming the same day.
        with self.store.lock:
            self.store.conn.execute('BEGIN IMMEDIATE')
            try:
                row=self.store.conn.execute('SELECT * FROM brief_daily WHERE owner=? AND day=?',(self.owner,day)).fetchone()
                if row:
                    if row['status']!='failed' or row['report'] or row['attempts']>=3 or now<row['next_try']:
                        self.store.conn.commit();return {'status':row['status'],'day':day,'report_id':row['report']}
                    self.store.conn.execute("UPDATE brief_daily SET status='generating',attempts=attempts+1,actual=? WHERE owner=? AND day=?",(now,self.owner,day))
                else:self.store.conn.execute('INSERT INTO brief_daily VALUES(?,?,?,?,?,?,?,?,?)',(self.owner,day,scheduled,now,None,'generating',1,0,None))
                self.store.conn.commit()
            except BaseException:self.store.conn.rollback();raise
        try:
            result=self.build(self.owner,now,day)
            identity=result['report_id']
            # Program-generated delivery metadata; external news/model cannot choose owner/date.
            delayed=now-scheduled>=60
            header=('延迟补发（仅当天）' if delayed else ('手动生成' if now<scheduled else '按时日报'))
            header+=f'；计划 {datetime.fromtimestamp(scheduled,ZoneInfo(self.config.timezone)).isoformat()}；实际开始 {local.isoformat()}。\n'
            result={**result,'text':header+result['text']}
            with self.store.lock,self.store.conn:
                row=self.store.conn.execute('SELECT text,meta FROM brief_reports WHERE id=? AND owner=? AND day=?',(identity,self.owner,day)).fetchone()
                if not row:raise ValueError('report owner/date mismatch')
                meta=json.loads(row['meta']);meta.update({'scheduled':scheduled,'actual_start':now,'delayed':delayed})
                # Respect source policy: do not replace a display-only marker with source text.
                persisted=row['text'] if row['text'].startswith('[analysis display-only:') else result['text']
                self.store.conn.execute('UPDATE brief_reports SET text=?,meta=? WHERE id=?',(persisted,json.dumps(meta),identity))
                self.store.conn.execute("UPDATE brief_daily SET report=?,status='sending' WHERE owner=? AND day=?",(identity,self.owner,day))
            status=self.history.deliver(identity,result,self.send)
            self.store.execute('UPDATE brief_daily SET status=? WHERE owner=? AND day=?',(status,self.owner,day))
            return {'status':status,'day':day,'report_id':identity,'delayed':delayed}
        except Exception as exc:
            row=self.store.execute('SELECT report,status FROM brief_daily WHERE owner=? AND day=?',(self.owner,day))[0]
            # If any send might have begun, ambiguous state is never retried automatically.
            status='unknown' if row['report'] else 'failed'
            self.store.execute('UPDATE brief_daily SET status=?,next_try=?,error=? WHERE owner=? AND day=?',(status,now+900,type(exc).__name__,self.owner,day))
            return {'status':status,'day':day,'error_type':type(exc).__name__}
