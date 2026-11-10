"""PostgreSQL business records; credentials never belong in this database."""
import json
import re
from .database import Database
import threading
import time
import uuid

RESPONSE_STYLES = {
    'concise': '在保留必要证据和限制的前提下，使用简明回答。',
    'standard': '先回答主要问题，再列出支撑证据和限制。',
    'detailed': '分段展开证据、依据和限制，明确每一项推测。',
}

def decode_snapshot(row):
    """Read legacy positions or the atomic positions+scope envelope; no DB writes."""
    row=dict(row);data=json.loads(row.pop('payload'))
    if isinstance(data,dict) and isinstance(data.get('format'),str):
        from .brokers.coverage import FORMAT,validate_coverage
        if data['format']!=FORMAT or set(data)!={'format','positions','coverage'} or not isinstance(data['positions'],dict):
            raise ValueError('invalid snapshot envelope')
        row['positions']=data['positions']
        row['coverage']=validate_coverage(data['coverage'],len(row['positions']))
    else:row['positions']=data
    return row


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if any(x in k.lower() for x in ("password","token","api_key","secret","account")) else redact(v)) for k,v in value.items()}
    if isinstance(value,list):
        return [redact(v) for v in value]
    if isinstance(value,str):
        return re.sub(r"\bU\d{5,}\b", "[ACCOUNT]", value)
    return value


class Store:
    def __init__(self, location, database=None):
        self.database = database or Database(location)
        self.lock = threading.RLock()
        self.conn = self.database.connect()
        try:
            with self.conn.transaction():
                self.database.initialize_schema(self.conn)
                self._setup()
        except BaseException:
            self.conn.close()
            raise

    def _setup(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,session TEXT,input TEXT,status TEXT,result TEXT,error TEXT,created DOUBLE PRECISION,updated DOUBLE PRECISION,seq BIGINT GENERATED ALWAYS AS IDENTITY);
        CREATE TABLE IF NOT EXISTS traces(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,task_id TEXT,agent TEXT,tool TEXT,phase TEXT,payload TEXT,at DOUBLE PRECISION);
        CREATE TABLE IF NOT EXISTS usage_calls(id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,task_id TEXT,role TEXT,kind TEXT,provider TEXT,model TEXT,elapsed_ms INTEGER,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,estimated_micro_usd BIGINT,extra_cost_unknown INTEGER NOT NULL,status TEXT,at DOUBLE PRECISION);
        ALTER TABLE usage_calls ADD COLUMN IF NOT EXISTS cached_input_tokens INTEGER;
        ALTER TABLE tasks ADD COLUMN IF NOT EXISTS model_route TEXT;
        CREATE TABLE IF NOT EXISTS task_budget_limits(task_id TEXT PRIMARY KEY,limit_micro_usd BIGINT NOT NULL);
        CREATE TABLE IF NOT EXISTS session_preferences(session TEXT PRIMARY KEY,response_style TEXT NOT NULL);

        CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,source TEXT,scope TEXT,asof DOUBLE PRECISION,payload TEXT,seq BIGINT GENERATED ALWAYS AS IDENTITY);
        CREATE TABLE IF NOT EXISTS rules(symbol TEXT PRIMARY KEY,spec TEXT,state TEXT DEFAULT 'normal',last_time DOUBLE PRECISION DEFAULT 0,enabled INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,symbol TEXT,day TEXT,status TEXT,payload TEXT,at DOUBLE PRECISION);
        """)
    def execute(self, sql, args=()):
        with self.lock:
            cursor = self.conn.execute(sql, args or None)
            return cursor.fetchall() if cursor.description else []

    def lock_transaction(self, key):
        self.conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                          (self.database.schema + ':' + key,))

    def create_task(self, session, text, model_route=None):
        task=uuid.uuid4().hex
        now=time.time()
        self.execute("INSERT INTO tasks(id,session,input,status,result,error,created,updated,model_route) VALUES(%s,%s,%s,'running',NULL,NULL,%s,%s,%s)",(task,session,json.dumps(redact(text),ensure_ascii=False),now,now,model_route))
        return task

    def finish(self, task, status, result=None, error=None):
        self.execute("UPDATE tasks SET status=%s,result=%s,error=%s,updated=%s WHERE id=%s",(status,json.dumps(redact(result),ensure_ascii=False),json.dumps(redact(error),ensure_ascii=False),time.time(),task))

    def trace(self, task, agent, tool, phase, payload):
        self.execute("INSERT INTO traces(task_id,agent,tool,phase,payload,at) VALUES(%s,%s,%s,%s,%s,%s)",(task,agent,tool,phase,json.dumps(redact(payload),ensure_ascii=False,default=str),time.time()))

    def task(self, task):
        rows=self.execute("SELECT * FROM tasks WHERE id=%s",(task,))
        return rows[0] if rows else None

    def traces(self, task):
        return self.execute("SELECT * FROM traces WHERE task_id=%s ORDER BY id",(task,))

    def response_style(self, session):
        rows = self.execute('SELECT response_style FROM session_preferences WHERE session=%s', (session,))
        return rows[0]['response_style'] if rows else None

    def set_response_style(self, session, style):
        if style is None:
            self.execute('DELETE FROM session_preferences WHERE session=%s', (session,))
            return
        if style not in RESPONSE_STYLES:
            raise ValueError('invalid response style')
        self.execute('INSERT INTO session_preferences VALUES (%s,%s) ON CONFLICT(session) '
                     'DO UPDATE SET response_style=excluded.response_style', (session, style))

    def snapshot(self, source, scope, positions, asof=None, identity=None,coverage=None):
        identity=identity or uuid.uuid4().hex
        payload=positions
        if coverage is not None:
            from .brokers.coverage import FORMAT,validate_coverage
            payload={'format':FORMAT,'positions':positions,'coverage':validate_coverage(coverage,len(positions))}
        self.execute("INSERT INTO snapshots(id,source,scope,asof,payload) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET source=excluded.source,scope=excluded.scope,asof=excluded.asof,payload=excluded.payload",(identity,source,scope,asof or time.time(),json.dumps(payload,ensure_ascii=False)))
        return identity

    def latest(self, source, scope=None):
        sql="SELECT * FROM snapshots WHERE source=%s"
        args=[source]
        if scope is not None:
            sql+=" AND scope=%s"; args.append(scope)
        rows=self.execute(sql+" ORDER BY asof DESC,seq DESC LIMIT 1",args)
        if not rows:return None
        return decode_snapshot(rows[0])

    def get_snapshot(self, identity):
        rows=self.execute("SELECT * FROM snapshots WHERE id=%s",(identity,))
        if not rows:return None
        return decode_snapshot(rows[0])

    def close(self):
        self.conn.close()
