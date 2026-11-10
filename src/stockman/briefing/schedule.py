"""Calendar-day scheduling with a durable boundary before any external send."""
from datetime import datetime
import json
import math
from zoneinfo import ZoneInfo

from .history import History

MAX_ATTEMPTS = 3
RETRY_SECONDS = 900
DELIVERY_RESULTS = ('sent', 'failed', 'unknown')


class Scheduler:
    def __init__(self, store, config, owner, allowed, build, send):
        if not str(owner).isdigit() or int(owner) <= 0 or str(owner) not in {str(value) for value in allowed}:
            raise PermissionError('daily briefing requires the configured private owner')
        self.store = store
        self.config = config
        self.owner = str(owner)
        self.build = build
        self.send = send
        self.history = History(store)
        store.execute('CREATE TABLE IF NOT EXISTS brief_daily(owner TEXT,day TEXT,scheduled REAL,actual REAL,report TEXT,status TEXT,attempts INTEGER,next_try REAL,error TEXT,PRIMARY KEY(owner,day))')

    def recover(self):
        """Run after taking the directory lock. Old claims do not prove no send occurred."""
        with self.store.lock, self.store.conn:
            conn = self.store.conn
            conn.execute("""UPDATE brief_reports SET status='unknown' WHERE status='sending'""")
            # A linked terminal report can resolve a legacy claim left in flight.
            conn.execute("""UPDATE brief_daily SET status=COALESCE(
                (SELECT status FROM brief_reports WHERE id=brief_daily.report AND owner=brief_daily.owner
                 AND day=brief_daily.day AND status IN ('sent','failed','unknown')), 'unknown'),
                error=NULL, next_try=0 WHERE status IN ('generating','sending')""")
            # An unlinked building claim proves the send boundary was never reached.
            conn.execute("""UPDATE brief_daily SET status='failed', error='generation_interrupted',
                next_try=CASE WHEN attempts < ? THEN actual + ? ELSE 0 END
                WHERE status='building' AND report IS NULL""", (MAX_ATTEMPTS, RETRY_SECONDS))
            conn.execute("""UPDATE brief_daily SET status='unknown', error=NULL, next_try=0
                WHERE status='building'""")

    def _budget_exhausted(self, conn, day):
        if not self.config.budget or not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='brief_budget' AND type='table'"
        ).fetchone():
            return False
        calls, cost = conn.execute(
            'SELECT COUNT(*),COALESCE(SUM(charge),0) FROM brief_budget WHERE day=?', (day,)
        ).fetchone()
        return calls >= self.config.max_calls or cost >= math.floor(self.config.budget * 1_000_000)

    def _claim(self, day, scheduled, now):
        with self.store.lock:
            conn = self.store.conn
            # Lock the claim across SQLite connections before checking for an existing run.
            conn.execute('BEGIN IMMEDIATE')
            try:
                row = conn.execute('SELECT * FROM brief_daily WHERE owner=? AND day=?', (self.owner, day)).fetchone()
                if row:
                    # An interrupted model call may still have a reservation in the daily ledger.
                    if row['error'] == 'generation_interrupted' and row['next_try'] and self._budget_exhausted(conn, day):
                        conn.execute("UPDATE brief_daily SET next_try=0,error='budget_exhausted' WHERE owner=? AND day=?",
                                     (self.owner, day))
                        row = conn.execute('SELECT * FROM brief_daily WHERE owner=? AND day=?', (self.owner, day)).fetchone()
                    if not self._can_retry(row) or now < row['next_try']:
                        conn.commit()
                        return row
                    conn.execute("""UPDATE brief_daily SET status='building',attempts=attempts+1,actual=?,
                        next_try=0,error=NULL WHERE owner=? AND day=?""", (now, self.owner, day))
                else:
                    conn.execute('INSERT INTO brief_daily VALUES(?,?,?,?,?,?,?,?,?)',
                                 (self.owner, day, scheduled, now, None, 'building', 1, 0, None))
                conn.commit()
                return None
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def _can_retry(row):
        return (row['status'] == 'failed' and not row['report']
                and row['attempts'] < MAX_ATTEMPTS and bool(row['next_try']))

    def _result(self, row):
        result = {'status':row['status'], 'day':row['day'], 'report_id':row['report'],
                  'attempts':row['attempts'], 'next_try':row['next_try'] if self._can_retry(row) else None,
                  'at':row['actual']}
        if row['error']:
            result['error_type'] = row['error']
        return result

    def _row(self, day):
        return self.store.execute('SELECT * FROM brief_daily WHERE owner=? AND day=?', (self.owner, day))[0]

    def _generation_failed(self, day, now, exc):
        row = self._row(day)
        retry = not isinstance(exc, (PermissionError, ValueError)) and row['attempts'] < MAX_ATTEMPTS
        if isinstance(exc, TimeoutError):
            code = 'generation_timeout'
        elif isinstance(exc, PermissionError):
            code = 'generation_denied'
        elif isinstance(exc, ValueError):
            code = 'generation_invalid'
        else:
            code = 'generation_failed'
        self.store.execute("""UPDATE brief_daily SET status='failed',next_try=?,error=?
            WHERE owner=? AND day=? AND status='building'""",
                           (now + RETRY_SECONDS if retry else 0, code, self.owner, day))
        return self._result(self._row(day))

    def _begin_delivery(self, day, identity, text, metadata):
        with self.store.lock, self.store.conn:
            conn = self.store.conn
            row = conn.execute("""SELECT text,meta FROM brief_reports
                WHERE id=? AND owner=? AND day=? AND status='preview'""", (identity, self.owner, day)).fetchone()
            if not row:
                raise ValueError('report owner, date or state mismatch')
            meta = json.loads(row['meta'])
            meta.update(metadata)
            # Scheduling metadata may be stored even when source policy bars storing the report text.
            persisted = row['text'] if row['text'].startswith('[analysis display-only:') else text
            conn.execute("UPDATE brief_reports SET text=?,meta=?,status='sending' WHERE id=?",
                         (persisted, json.dumps(meta), identity))
            claimed = conn.execute("""UPDATE brief_daily SET report=?,status='sending'
                WHERE owner=? AND day=? AND status='building'""", (identity, self.owner, day))
            if claimed.rowcount != 1:
                raise ValueError('report was not claimed')

    def tick(self, now, force=False):
        if not self.config.enabled and not force:
            return {'status':'disabled'}
        local = datetime.fromtimestamp(now, ZoneInfo(self.config.timezone))
        day = local.date().isoformat()
        hour, minute = map(int, self.config.at.split(':'))
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
        if now < scheduled and not force:
            return {'status':'not_due'}
        existing = self._claim(day, scheduled, now)
        if existing is not None:
            return self._result(existing)
        try:
            result = self.build(self.owner, now, day)
            identity = result['report_id']
            delayed = now - scheduled >= 60
            header = '延迟补发（仅当天）' if delayed else ('手动生成' if now < scheduled else '按时日报')
            header += f'；计划 {datetime.fromtimestamp(scheduled, local.tzinfo).isoformat()}；实际开始 {local.isoformat()}。\n'
            result = {**result, 'text':header + result['text']}
            # Commit both sending markers before calling the external transport.
            self._begin_delivery(day, identity, result['text'],
                                 {'scheduled':scheduled, 'actual_start':now, 'delayed':delayed})
        except Exception as exc:
            return self._generation_failed(day, now, exc)

        try:
            status = self.send(result)
        except Exception:
            status = 'unknown'
        if status not in DELIVERY_RESULTS:
            status = 'unknown'
        # Keep the report and scheduler outcome in sync if the process stops during the write.
        with self.store.lock, self.store.conn:
            self.store.conn.execute('UPDATE brief_reports SET status=? WHERE id=?', (status, identity))
            self.store.conn.execute('UPDATE brief_daily SET status=? WHERE owner=? AND day=?', (status, self.owner, day))
        return {**self._result(self._row(day)), 'delayed':delayed}
