"""SQLite business records; credentials never belong in this database."""
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(x in k.lower() for x in ("password","token","api_key","secret","account")) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):
        return [redact(v) for v in value]
    if isinstance(value,str):
        return re.sub(r"\bU\d{5,}\b", "[ACCOUNT]", value)
    return value


class Store:
    def __init__(self, path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        self.lock=threading.RLock()
        self.conn=sqlite3.connect(str(path),check_same_thread=False,timeout=30)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,session TEXT,input TEXT,status TEXT,result TEXT,error TEXT,created REAL,updated REAL);
        CREATE TABLE IF NOT EXISTS traces(id INTEGER PRIMARY KEY,task_id TEXT,agent TEXT,tool TEXT,phase TEXT,payload TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,source TEXT,scope TEXT,asof REAL,payload TEXT);
        CREATE TABLE IF NOT EXISTS rules(symbol TEXT PRIMARY KEY,spec TEXT,state TEXT DEFAULT 'normal',last_time REAL DEFAULT 0,enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,symbol TEXT,day TEXT,status TEXT,payload TEXT,at REAL);
        """)
        self.conn.commit()

    def execute(self, sql, args=()):
        with self.lock, self.conn:
            cursor=self.conn.execute(sql,args)
            return [dict(row) for row in cursor.fetchall()] if cursor.description else []

    def create_task(self, session, text):
        task=uuid.uuid4().hex
        now=time.time()
        self.execute("INSERT INTO tasks VALUES(?,?,?,'running',NULL,NULL,?,?)",(task,session,json.dumps(redact(text),ensure_ascii=False),now,now))
        return task

    def finish(self, task, status, result=None, error=None):
        self.execute("UPDATE tasks SET status=?,result=?,error=?,updated=? WHERE id=?",(status,json.dumps(redact(result),ensure_ascii=False),json.dumps(redact(error),ensure_ascii=False),time.time(),task))

    def trace(self, task, agent, tool, phase, payload):
        self.execute("INSERT INTO traces(task_id,agent,tool,phase,payload,at) VALUES(?,?,?,?,?,?)",(task,agent,tool,phase,json.dumps(redact(payload),ensure_ascii=False,default=str),time.time()))

    def task(self, task):
        rows=self.execute("SELECT * FROM tasks WHERE id=?",(task,))
        return rows[0] if rows else None

    def traces(self, task):
        return self.execute("SELECT * FROM traces WHERE task_id=? ORDER BY id",(task,))

    def snapshot(self, source, scope, positions, asof=None, identity=None):
        identity=identity or uuid.uuid4().hex
        self.execute("INSERT OR REPLACE INTO snapshots VALUES(?,?,?,?,?)",(identity,source,scope,asof or time.time(),json.dumps(positions,ensure_ascii=False)))
        return identity

    def latest(self, source, scope=None):
        sql="SELECT * FROM snapshots WHERE source=?"
        args=[source]
        if scope is not None:
            sql+=" AND scope=?"; args.append(scope)
        rows=self.execute(sql+" ORDER BY asof DESC LIMIT 1",args)
        if not rows:return None
        row=rows[0];row['positions']=json.loads(row.pop('payload'));return row

    def get_snapshot(self, identity):
        rows=self.execute("SELECT * FROM snapshots WHERE id=?",(identity,))
        if not rows:return None
        row=rows[0];row['positions']=json.loads(row.pop('payload'));return row

    def close(self):
        self.conn.close()
