"""Local reports and explicit human judgements; no model judge or cloud upload."""
from datetime import datetime
import html
import json
import math
from pathlib import Path
import statistics

from .dataset import FIELDS, digest
from .execution import dump

POLICY = {'version': 'rules-human-v1', 'offline_required': 1.0,
          'live_task_completion_required': 0.95, 'critical_failures_allowed': 0,
          'semantic_method': 'human', 'cost_latency': 'observation_only'}


def binding(report):
    return digest({k: report[k] for k in ('schema', 'id', 'metadata', 'cases', 'attempts')})


def check_report(report):
    if report.get('schema') != 'stockman-eval-report-v1' or binding(report) != report.get('binding'):
        raise ValueError('report execution content changed or schema invalid')
    config = report['metadata']['config']
    if type(config.get('repeat')) is not int or not 1 <= config['repeat'] <= 10:
        raise ValueError('invalid report repeat configuration')
    if len({r['attempt_id'] for r in report['attempts']}) != len(report['attempts']):
        raise ValueError('duplicate report attempts')
    return report


def review_template(report):
    return {'report_id': report['id'], 'binding': report['binding'], 'reviewer': '', 'reviewed_at': '',
            'note': 'Compare task, evidence, every raw response, tool requests and final output. Never infer support from quote matching alone.',
            'rows': [{'attempt_id': row['attempt_id'], 'fields': {
                field: {'status': 'pending' if row['manual_applicability'][field] else 'not_applicable', 'reason': ''}
                for field in FIELDS}}
                for row in report['attempts'] if any(row['manual_applicability'].values())]}


def apply_review(report, review):
    check_report(report)
    if review.get('report_id') != report['id'] or review.get('binding') != report['binding']:
        raise ValueError('review belongs to another execution report')
    if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip():
        raise ValueError('reviewer required')
    try:
        stamp = datetime.fromisoformat(review['reviewed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise ValueError('reviewed_at must be an ISO timestamp with timezone') from None
    expected = {r['attempt_id']: r for r in report['attempts'] if any(r['manual_applicability'].values())}
    rows = review.get('rows', [])
    if len(rows) != len(expected) or {r['attempt_id'] for r in rows} != set(expected):
        raise ValueError('review must cover every applicable attempt exactly once')
    for row in rows:
        if set(row.get('fields', {})) != set(FIELDS):
            raise ValueError('all three review fields required')
        for field, value in row['fields'].items():
            status = value.get('status')
            applicable = expected[row['attempt_id']]['manual_applicability'][field]
            if status not in (('pass', 'fail', 'pending') if applicable else ('not_applicable',)):
                raise ValueError('invalid status; applicability is fixed by the dataset')
            if not isinstance(value.get('reason'), str) or (status in ('pass', 'fail') and not value['reason'].strip()):
                raise ValueError('pass/fail requires a review reason')
    # Do not overwrite earlier judgements silently: keep all review submissions.
    report.setdefault('review_history', []).append(json.loads(json.dumps(review)))
    report['summary'] = summarize(report)
    return report


def summarize(report):
    attempts = report['attempts']
    latest = report.get('review_history', [])
    reviews = {r['attempt_id']: r['fields'] for r in latest[-1]['rows']} if latest else {}
    eligible = [r for r in attempts if r['kind'] != 'guard']
    pending = 0
    completed = 0
    semantic_failures = 0
    for row in eligible:
        fields = reviews.get(row['attempt_id'], {})
        statuses = {f: fields.get(f, {}).get('status', 'pending') for f in FIELDS if row['manual_applicability'][f]}
        pending += any(status == 'pending' for status in statuses.values())
        semantic_failures += any(statuses.get(f) == 'fail' for f in ('supported', 'uncertainty_clear'))
        completed += statuses.get('task_completed') == 'pass' and all(c['passed'] for c in row['checks'])
    auto_pass = sum(all(c['passed'] for c in r['checks']) for r in attempts)
    critical = sum(not c['passed'] and c['critical'] for r in attempts for c in r['checks'])
    guards = [r for r in attempts if r['kind'] == 'guard']
    guard_pass = all(all(c['passed'] for c in r['checks']) for r in guards)
    detectors = [c for r in attempts for c in r['detector_checks']]
    detector_pass = all(c['passed'] for c in detectors)
    expected = report['metadata']['dataset_case_count'] * report['metadata']['config']['repeat']
    coverage_complete = len(attempts) == expected and all(r['status'] != 'not_run' for r in attempts)
    rate = completed/len(eligible) if eligible and not pending else None
    offline = report['metadata']['mode'] == 'offline'
    errors = sum(r['status'] in ('error', 'timeout', 'not_run') for r in attempts)
    replay_pass = auto_pass == len(attempts) and detector_pass and bool(attempts)
    failed = bool(critical or semantic_failures or not guard_pass or not detector_pass or errors)
    if offline:
        state = 'offline_replay_failed' if failed or not replay_pass else 'offline_replay_pass'
    elif failed or (not pending and rate is not None and rate < POLICY['live_task_completion_required']):
        state = 'live_failed'
    elif pending or not coverage_complete:
        state = 'live_incomplete'
    else:
        state = 'live_pass'
    # Guard probes are always simulated, even in a live run. Keep their tokens/costs
    # outside the provider totals rather than attributing fake calls to Gemini.
    usage = [u for r in attempts if offline or r['kind'] != 'guard' for u in r['usage']]
    from stockman.usage import summary
    costs = summary(usage)
    latencies = sorted(r['elapsed_ms'] for r in attempts)
    def tokens(name):
        return {'known': sum(u[name] or 0 for u in usage), 'unknown_calls': sum(u[name] is None for u in usage)}
    groups = {}
    for group in 'ECNTSF':
        selected = [r for r in attempts if r['case_id'][0] == group]
        normal = [r for r in selected if r['kind'] != 'guard']
        groups[group] = {'executed': len(selected), 'automatic_pass': sum(all(c['passed'] for c in r['checks']) for r in selected),
                         'task_completion_eligible': len(normal),
                         'task_completed': sum(reviews.get(r['attempt_id'], {}).get('task_completed', {}).get('status') == 'pass'
                                               and all(c['passed'] for c in r['checks']) for r in normal),
                         'manual_pending': sum(any(reviews.get(r['attempt_id'], {}).get(f, {}).get('status', 'pending') == 'pending'
                                                   for f in FIELDS if r['manual_applicability'][f]) for r in normal)}
    return {'status': state, 'coverage_complete': coverage_complete, 'executed': len(attempts), 'expected': expected,
            'automatic_pass': auto_pass, 'critical_failed_checks': critical, 'detector_checks_pass': detector_pass,
            'guard_attempts': len(guards), 'guards_pass': guard_pass, 'runtime_errors': errors,
            'manual_pending_attempts': pending, 'manual_semantic_failed_attempts': semantic_failures,
            'task_completion': {'completed': completed, 'eligible': len(eligible), 'rate': rate},
            'costs': {**costs, 'kind': 'simulated' if offline else 'logical_provider_estimate',
                      'simulated_guard_calls': sum(len(r['usage']) for r in guards)},
            'model_calls': {'observed': len(usage), 'failed': sum(u['status'] == 'failed' for u in usage),
                            'failure_rate': sum(u['status'] == 'failed' for u in usage)/len(usage) if usage else None},
            'tokens': {key: tokens(key) for key in ('input_tokens', 'output_tokens', 'total_tokens')},
            'latency_ms': {'n': len(latencies), 'median': statistics.median(latencies) if latencies else None,
                           'p95_nearest_rank': latencies[math.ceil(len(latencies)*0.95)-1] if latencies else None,
                           'scope': 'whole isolated attempt including process startup'},
            'groups': groups,
            'note': 'Replay is protocol evidence only. Pending is not a semantic pass. Small synthetic samples do not establish production accuracy.'}


def write_artifacts(report, directory, template=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report['summary'] = summarize(report)
    dump(directory/'report.json', report)
    if template:
        dump(directory/'review-template.json', review_template(report))
    def esc(value):
        return html.escape(str(value), quote=True)
    cards = []
    by_id = {c['id']: c for c in report['cases']}
    for row in report['attempts']:
        case = by_id[row['case_id']]
        payload = {'task': case['task'], 'turns': case.get('turns'), 'rubric': case['rubric'],
                   'synthetic_evidence': case['resolved_evidence'], 'synthetic_tool_fixture': case['fixture'],
                   'result': row}
        cards.append(f'<section><h2>{esc(row["attempt_id"])}</h2><pre>{esc(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></section>')
    document = ('<!doctype html><html lang="zh"><meta charset="utf-8">'
                '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
                '<title>StockMan evaluation</title><style>body{max-width:1100px;margin:32px auto;padding:16px;font:16px system-ui;background:#fafafa;color:#18212b}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px;border:1px solid #ddd}section{margin-top:32px}</style>'
                '<h1>StockMan 本地评测</h1><p>合成工具；离线回放不证明真实模型质量。人工待评不等于通过。成本未知不等于免费。</p>'
                f'<pre>{esc(json.dumps(report["metadata"], ensure_ascii=False, indent=2))}</pre>'
                f'<pre>{esc(json.dumps(report["summary"], ensure_ascii=False, indent=2))}</pre>'
                f'<h2>人工评审记录</h2><pre>{esc(json.dumps(report.get("review_history", []), ensure_ascii=False, indent=2))}</pre>'
                + ''.join(cards) + '</html>')
    (directory/'report.html').write_text(document, encoding='utf-8')


def compare_reports(baseline, candidate):
    check_report(baseline)
    check_report(candidate)
    reasons = []
    for key in ('dataset_hash', 'mode', 'policy', 'config'):
        if baseline['metadata'][key] != candidate['metadata'][key]:
            reasons.append(key + ' differs')
    a, b = summarize(baseline), summarize(candidate)
    if not a['coverage_complete'] or not b['coverage_complete']:
        reasons.append('incomplete dataset coverage')
    changed = [key for key in ('model', 'production_prompt_hash', 'git_revision', 'working_tree_diff_hash', 'evaluator_hash')
               if baseline['metadata'].get(key) != candidate['metadata'].get(key)]
    return {'comparable': not reasons, 'reasons': reasons, 'changed_variables': changed,
            'baseline': a, 'candidate': b,
            'semantic_comparison_ready': not reasons and not a['manual_pending_attempts'] and not b['manual_pending_attempts'],
            'note': 'Offline and live are never ranked together. Changes in model and prompts together confound attribution; all attempts and human judgements remain visible.'}
