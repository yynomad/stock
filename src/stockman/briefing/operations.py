"""Sanitized service diagnostics and one-shot daily Telegram alerts."""
from datetime import datetime, timezone
import json
import math
import re
import time
from uuid import uuid4

ANALYSIS_STATES = {
    'explained', 'no_new_evidence', 'no_analyzable_new_evidence', 'insufficient_sources',
    'model_disabled', 'model_unconfigured', 'model_failed', 'model_output_invalid',
    'model_call_limit', 'budget_exhausted', 'deadline_exceeded',
}
GENERATION_ERRORS = {
    'generation_interrupted', 'generation_timeout', 'generation_denied', 'generation_invalid',
    'generation_failed', 'budget_exhausted',
}
ALERT_TEXT = {
    'daily_failed': '日报运行失败。请查看 StockMan 服务日志。',
    'delivery_unknown': '日报送达情况未知。请先核对 Telegram 中是否已有当日原日报，并查看 StockMan 服务日志。',
}


def _numbers(data, keys):
    return {key:value for key in keys if type(value := data.get(key)) in (int, float)
            and math.isfinite(value) and value >= 0}


class DailyOperations:
    def __init__(self, store, logger, budget, send_alert=None):
        self.store = store
        self.logger = logger
        self.budget = budget
        self.send_alert = send_alert
        self.last_daily = None
        store.execute("""CREATE TABLE IF NOT EXISTS brief_source_health(
            source TEXT PRIMARY KEY, reference TEXT NOT NULL, last_success DOUBLE PRECISION)""")
        store.execute("""CREATE TABLE IF NOT EXISTS brief_alerts(
            day TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL, at DOUBLE PRECISION NOT NULL,
            PRIMARY KEY(day,kind))""")
        # A previous alert may have reached Telegram before its outcome was recorded.
        with store.lock, store.conn.transaction():
            rows = store.conn.execute("SELECT day,kind FROM brief_alerts WHERE status='sending'").fetchall()
            store.conn.execute("UPDATE brief_alerts SET status='unknown' WHERE status='sending'")
        for row in rows:
            self._log('daily_alert', day=row['day'], kind=row['kind'], status='unknown', reason='interrupted')

    def _log(self, event, **fields):
        fields['time'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        warning = fields.get('status') in ('failed','unknown','retry_pending','partial') or fields.get('task_status') == 'partial'
        log = self.logger.warning if warning else self.logger.info
        log('%s %s', event, json.dumps(fields, ensure_ascii=False, allow_nan=False))

    def observe_sources(self, day, task, health):
        for source in health:
            # The local mapping retains the configured source key. Only its random alias
            # reaches the log because the key itself can include a held ticker.
            success = source['attempted_at'] if source['status'] == 'ok' or source.get('data_asof') else None
            with self.store.lock, self.store.conn.transaction():
                conn = self.store.conn
                conn.execute("""INSERT INTO brief_source_health VALUES(%s,%s,%s)
                    ON CONFLICT(source) DO UPDATE SET last_success=excluded.last_success
                    WHERE excluded.last_success IS NOT NULL
                      AND (brief_source_health.last_success IS NULL OR excluded.last_success > brief_source_health.last_success)""",
                             (source['source'], uuid4().hex[:12], success))
                row = conn.execute('SELECT reference,last_success FROM brief_source_health WHERE source=%s',
                                   (source['source'],)).fetchone()
            self._log('daily_source', day=day, task_id=task, source_ref=row['reference'],
                      source_type=source['source_type'], status=source['status'], reason=source.get('error_type'),
                      attempted_at=source['attempted_at'], last_success=row['last_success'],
                      data_asof=source['data_asof'], freshness=source['freshness'])

    def _report_summary(self, report_id):
        if not report_id:
            return {}
        rows = self.store.execute('SELECT meta FROM brief_reports WHERE id=%s', (report_id,))
        if not rows:
            return {}
        try:
            meta = json.loads(rows[0]['meta'])
        except (ValueError, TypeError):
            return {}
        if not isinstance(meta, dict):
            return {}
        # Report metadata can contain holdings and source text. Log only the safe summary below.
        raw_state = meta.get('status')
        state = raw_state.split(';', 1)[0] if isinstance(raw_state, str) else None
        result = {'analysis':state if state in ANALYSIS_STATES else 'unknown'}
        if meta.get('task_status') in ('completed', 'partial'):
            result['task_status'] = meta['task_status']
        task = meta.get('task_id')
        if isinstance(task, str) and re.fullmatch(r'[a-f0-9]{32}', task):
            result['task_id'] = task
        holdings = meta.get('holdings')
        if isinstance(holdings, dict):
            result['holdings'] = _numbers(holdings, ('asof',))
            result['holdings'].update({key:holdings[key] for key in ('stale','cached') if type(holdings.get(key)) is bool})
            coverage = holdings.get('coverage')
            if isinstance(coverage, dict):
                result['holdings'].update(_numbers(coverage, ('included_positions','total_positions','excluded_positions')))
        return result

    def observe_scheduler_error(self):
        key = ('scheduler_error',)
        if self.last_daily != key:
            self._log('daily_run', stage='scheduler', status='failed', reason='scheduler_error')
            self.last_daily = key

    def observe_daily(self, result, now):
        day = result.get('day')
        if not day:
            self.last_daily = None
            return
        status = result['status']
        reason = result.get('error_type')
        next_try = result.get('next_try')
        report = result.get('report_id')
        # Polling the same outcome should not repeat the log. Alert deduplication is persisted in PostgreSQL.
        key = (day, status, reason, next_try, result['attempts'], report)
        if key == self.last_daily:
            return
        alert = None
        if status == 'unknown':
            stage, reason, alert = 'delivery', 'delivery_unknown', 'delivery_unknown'
        elif status == 'failed' and report:
            stage, reason, alert = 'delivery', 'delivery_rejected', 'daily_failed'
        elif status == 'failed':
            stage = 'generation'
            reason = reason if reason in GENERATION_ERRORS else 'generation_failed'
            # Alert only when the scheduler has stopped retrying this generation failure.
            if next_try is None:
                alert = 'daily_failed'
        else:
            stage = 'generation' if status == 'building' else 'delivery'
        budget = _numbers(self.budget.status(day), ('estimated_usd','known_estimated_usd','limit_usd',
                                                    'calls','call_limit','usage_unknown'))
        self._log('daily_run', day=day, stage=stage, status='retry_pending' if next_try else status,
                  reason=reason, attempts=result['attempts'], next_try=next_try,
                  started_at=result['at'], budget=budget, **self._report_summary(report))
        if alert and self.send_alert is not None:
            self._alert(day, alert, now)
        self.last_daily = key

    def _alert(self, day, kind, now):
        # Commit the day-and-kind claim first so even a crash during send cannot replay the alert.
        with self.store.lock, self.store.conn.transaction():
            claimed = self.store.conn.execute("INSERT INTO brief_alerts VALUES(%s,%s,'sending',%s) ON CONFLICT(day,kind) DO NOTHING",
                                              (day, kind, now))
            if claimed.rowcount != 1:
                return
        try:
            status = self.send_alert({'text':f'{day} {ALERT_TEXT[kind]}'})
        except Exception:
            status = 'unknown'
        if status not in ('sent', 'failed', 'unknown'):
            status = 'unknown'
        self.store.execute('UPDATE brief_alerts SET status=%s,at=%s WHERE day=%s AND kind=%s',
                           (status, time.time(), day, kind))
        self._log('daily_alert', day=day, kind=kind, status=status,
                  reason={'sent':None, 'failed':'alert_rejected', 'unknown':'alert_delivery_unknown'}[status])
