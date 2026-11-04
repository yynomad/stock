"""Versioned, synthetic evaluation data; never exported from a user's database."""
import hashlib
import json
from pathlib import Path

FIELDS = ('supported', 'uncertainty_clear', 'task_completed')
IDS = {f'{group}{number:02}' for group in 'ECNTSF' for number in range(1, 5)}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def evidence(case):
    from stockman.briefing.models import Evidence
    return [Evidence(source='synthetic', title=row['title'], body=row['body'],
                     url=f'https://example.invalid/evals/{case["id"]}/{index}',
                     published=row['published'], fetched=case['clock'],
                     entities=tuple(row['entities']))
            for index, row in enumerate(case['evidence'])]


def daily_text(case, candidate):
    """Resolve fixture aliases to the production evidence identities."""
    items = evidence(case)
    data = json.loads(json.dumps(candidate, ensure_ascii=False))
    for row in data.get('events', []):
        identity = row.get('id')
        if isinstance(identity, str) and identity.startswith('@'):
            row['id'] = items[int(identity[1:])].identity
    return json.dumps(data, ensure_ascii=False)


def load_dataset(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if data.get('version') != 'stockman-evals-v1' or data.get('synthetic') is not True:
        raise ValueError('unsupported dataset version or non-synthetic dataset')
    cases = data.get('cases', [])
    if len(cases) != 24 or {c['id'] for c in cases} != IDS:
        raise ValueError('v1 requires all 24 unique cases')
    for case in cases:
        if case['kind'] not in ('agent', 'daily', 'guard') or case['group'] != case['id'][0]:
            raise ValueError('invalid case kind/group')
        if set(case['manual']) != set(FIELDS) or not all(type(x) is bool for x in case['manual'].values()):
            raise ValueError('manual applicability must be declared in dataset')
        if not case['task'] or not case['rubric'] or not case['controls']:
            raise ValueError('missing task, rubric or detector controls')
        for item in evidence(case):
            item.validate(case['clock'])
        if case['kind'] == 'daily':
            daily_text(case, case['replay'])
    return data
