"""Local evaluation CLI; a live invocation is always explicit and isolated."""
import argparse
from datetime import datetime, timezone
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

from .dataset import digest, evidence, load_dataset
from .execution import dump, enrich, read_journal
from .reporting import POLICY, apply_review, binding, compare_reports, write_artifacts
from .scoring import check_attempt, detector_checks

ROOT = Path(__file__).resolve().parents[3]


def child_environment(mode):
    # Do not inherit broker, Telegram, tracing, dotenv or arbitrary application settings.
    allowed = ('PATH', 'SYSTEMROOT', 'TMPDIR', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
               'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'STOCKMAN_TEST_PG_SOCKET')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PYTHONPATH=str(ROOT/'src') + os.pathsep + str(ROOT),
               PYTHONDONTWRITEBYTECODE='1', STOCKMAN_OFFLINE='1', STOCKMAN_EXPLICIT_CONFIG='1',
               GEMINI_API_KEY=os.environ.get('GEMINI_API_KEY', '') if mode == 'live' else '',
               GOOGLE_API_KEY='', LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false',
               LANGSMITH_API_KEY='', BRIEFING_ENABLED='false')
    return env


def execute(case, options):
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='stockman-eval-') as directory:
        path = Path(directory)
        dump(path/'request.json', {'case': case, 'options': options})
        try:
            process = subprocess.run([sys.executable, '-B', '-m', 'stockman.evaluation.execution', str(path/'request.json')],
                                     cwd=directory, env=child_environment(options['mode']),
                                     capture_output=True, timeout=options['timeout'])
        except subprocess.TimeoutExpired:
            result = {'text': '', 'status': 'timeout', 'error_type': 'EvaluationTimeout'}
        else:
            if process.returncode == 0 and (path/'result.json').is_file():
                result = json.loads((path/'result.json').read_text(encoding='utf-8'))
            else:
                # Provider stderr may contain credentials. Never copy it into reports.
                result = {'text': '', 'status': 'error', 'error_type': 'WorkerFailed'}
        if 'usage' not in result:
            result = enrich(case, options, result, read_journal(path/'journal.jsonl'))
    result['elapsed_ms'] = round((time.monotonic()-started)*1000)
    return result


def git_value(args):
    try:
        result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, timeout=5)
        return result.stdout.decode().strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def metadata(data, options):
    sources = ('src/stockman/agents.py', 'src/stockman/stock_briefing/agent.py')
    prompts = {name: (ROOT/name).read_text(encoding='utf-8') for name in sources}
    evaluator = {str(path.relative_to(ROOT)): path.read_text(encoding='utf-8')
                 for path in sorted((ROOT/'src/stockman/evaluation').glob('*.py'))}
    return {'dataset_version': data['version'], 'dataset_hash': digest(data), 'dataset_case_count': len(data['cases']),
            'mode': options['mode'], 'model': options['model'], 'policy': POLICY,
            'production_prompt_hash': digest(prompts), 'evaluator_hash': digest(evaluator),
            'git_revision': git_value(['rev-parse', 'HEAD']),
            'working_tree_diff_hash': digest(git_value(['diff', 'HEAD']) or ''),
            'fixed_fixture_clock': sorted({c['clock'] for c in data['cases']}),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'config': {k: v for k, v in options.items() if k not in ('model', 'mode')},
            'tool_source': 'fixed synthetic fixtures; no broker/market/search/Telegram requests',
            'usage_scope': 'all observed logical calls, SDK retries disabled; no provider invoice guarantee'}


def run(data, options):
    selected = [case for case in data['cases'] if case['id'] in options['case_ids']]
    report = {'schema': 'stockman-eval-report-v1', 'id': uuid.uuid4().hex,
              'metadata': metadata(data, options),
              'cases': [{**c, 'resolved_evidence': [{**asdict(item), 'id': item.identity} for item in evidence(c)]} for c in selected],
              'attempts': [], 'review_history': []}
    for case in selected:
        for repeat in range(1, options['repeat']+1):
            result = execute(case, options)
            report['attempts'].append({**result, 'case_id': case['id'], 'kind': case['kind'],
                                       'attempt_id': f'{case["id"]}-{repeat}', 'repeat': repeat,
                                       'manual_applicability': case['manual'],
                                       'checks': check_attempt(case, result), 'detector_checks': detector_checks(case)})
            print(f'{case["id"]}-{repeat}: {result["status"]}', flush=True)
    report['binding'] = binding(report)
    return report


def bounded_integer(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError('integer required') from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f'expected {low}..{high}')
        return number
    return parse


def rate(value):
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('number required') from None
    if not math.isfinite(number) or not 0 <= number <= 10000:
        raise argparse.ArgumentTypeError('expected finite USD per million rate in 0..10000')
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description='StockMan synthetic evaluation: rules + human review')
    commands = parser.add_subparsers(dest='command', required=True)
    run_parser = commands.add_parser('run')
    modes = run_parser.add_mutually_exclusive_group()
    modes.add_argument('--offline', action='store_true', help='default: replay protocol, never a model quality score')
    modes.add_argument('--live', action='store_true', help='explicit Gemini calls; synthetic business tools only')
    run_parser.add_argument('--dataset', type=Path, default=ROOT/'evals/cases-v1.json')
    run_parser.add_argument('--output', type=Path, default=Path('runtime/evals'))
    run_parser.add_argument('--model')
    run_parser.add_argument('--case', action='append', default=[])
    run_parser.add_argument('--max-cases', type=bounded_integer(1, 24), default=24)
    run_parser.add_argument('--repeat', type=bounded_integer(1, 10), default=1)
    run_parser.add_argument('--max-calls', type=bounded_integer(1, 100), default=12)
    run_parser.add_argument('--call-timeout', type=bounded_integer(1, 120), default=30)
    run_parser.add_argument('--timeout', type=bounded_integer(1, 600), default=120, help='whole isolated attempt timeout')
    run_parser.add_argument('--input-rate', type=rate, help='explicit USD per million tokens, exact requested model only')
    run_parser.add_argument('--output-rate', type=rate)
    review_parser = commands.add_parser('review')
    review_parser.add_argument('--report', type=Path, required=True)
    review_parser.add_argument('--input', type=Path, required=True)
    comparison = commands.add_parser('compare')
    comparison.add_argument('--baseline', type=Path, required=True)
    comparison.add_argument('--candidate', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'review':
            report = apply_review(json.loads(args.report.read_text(encoding='utf-8')),
                                  json.loads(args.input.read_text(encoding='utf-8')))
            write_artifacts(report, args.report.parent)
            print(json.dumps(report['summary'], ensure_ascii=False))
            return 0
        if args.command == 'compare':
            comparison = compare_reports(json.loads(args.baseline.read_text(encoding='utf-8')),
                                         json.loads(args.candidate.read_text(encoding='utf-8')))
            print(json.dumps(comparison, ensure_ascii=False, indent=2))
            return 0 if comparison['comparable'] else 2
        if (args.input_rate is None) != (args.output_rate is None):
            parser.error('both --input-rate and --output-rate are required together')
        if args.live:
            if not args.model or not args.model.startswith('gemini-') or len(args.model) > 150:
                parser.error('--live requires an explicitly named supported gemini-* model')
            if not os.environ.get('GEMINI_API_KEY'):
                parser.error('set GEMINI_API_KEY in the process environment; eval never loads .env')
        elif args.model:
            parser.error('--model is only valid with --live')
        data = load_dataset(args.dataset)
        ids = [c['id'] for c in data['cases']]
        if len(set(args.case)) != len(args.case) or any(identity not in ids for identity in args.case):
            parser.error('unknown or repeated --case ID')
        selected = [identity for identity in ids if not args.case or identity in args.case][:args.max_cases]
        options = dict(mode='live' if args.live else 'offline', model=args.model or 'synthetic-replay',
                       case_ids=selected, repeat=args.repeat, max_calls=args.max_calls,
                       timeout=args.timeout, call_timeout=args.call_timeout,
                       input_rate=args.input_rate, output_rate=args.output_rate)
        report = run(data, options)
        directory = args.output/(report['metadata']['created_at'].replace(':', '').replace('+', '_')+'-'+report['id'][:8])
        write_artifacts(report, directory, template=True)
        print(json.dumps({'report': str(directory/'report.html'), 'summary': report['summary']}, ensure_ascii=False, indent=2))
        state = report['summary']['status']
        return 2 if state.endswith('failed') else (3 if state == 'live_incomplete' else 0)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        # No raw provider exception or input data in terminal error messages.
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
