"""Evaluation controls are synthetic; no test supplies live credentials or calls models."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stderr
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from stockman.evaluation.cli import ROOT, child_environment, execute, main, run
from stockman.evaluation.dataset import IDS, daily_text, digest, load_dataset
from stockman.evaluation.execution import Journal, enrich, observed_model, read_journal
from stockman.evaluation.reporting import (apply_review, binding, compare_reports, review_template,
                                         summarize, write_artifacts)
from stockman.evaluation.scoring import check_attempt, check_output, detector_checks

DATASET = ROOT/'evals/cases-v1.json'


def options(**changes):
    return {**dict(mode='offline', model='synthetic-replay', case_ids=sorted(IDS), repeat=1,
                   max_calls=12, timeout=30, call_timeout=10, input_rate=None, output_rate=None), **changes}


def completed_review(report):
    # Explicit test fixture, never a human judgement on a delivered baseline.
    template = review_template(report)
    template.update(reviewer='synthetic unit test reviewer', reviewed_at=datetime.now(timezone.utc).isoformat())
    for row in template['rows']:
        for value in row['fields'].values():
            if value['status'] == 'pending':
                value.update(status='pass', reason='synthetic acceptance fixture')
    return template


class EvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load_dataset(DATASET)
        cls.cases = {row['id']: row for row in cls.data['cases']}
        cls.report = run(cls.data, options())
        cls.rows = {row['case_id']: row for row in cls.report['attempts']}

    def test_dataset_is_complete_versioned_and_synthetic(self):
        self.assertEqual({row['id'] for row in self.data['cases']}, IDS)
        self.assertTrue(self.data['synthetic'])
        self.assertEqual(len(digest(self.data)), 64)
        self.assertTrue(all(case['rubric'] and case['controls'] for case in self.data['cases']))
        for mutation in ('duplicate', 'not_synthetic', 'wrong_manual'):
            changed = deepcopy(self.data)
            if mutation == 'duplicate':
                changed['cases'][-1] = changed['cases'][0]
            elif mutation == 'not_synthetic':
                changed['synthetic'] = False
            else:
                changed['cases'][0]['manual'] = {'supported': True}
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)/'cases.json'
                path.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    load_dataset(path)

    def test_all_24_replays_and_controls_pass_without_quality_claim(self):
        summary = summarize(self.report)
        self.assertEqual(summary['status'], 'offline_replay_pass')
        self.assertTrue(summary['coverage_complete'])
        self.assertEqual(summary['automatic_pass'], 24)
        self.assertEqual(summary['critical_failed_checks'], 0)
        self.assertTrue(summary['detector_checks_pass'])
        self.assertEqual(summary['manual_pending_attempts'], 22)
        self.assertIsNone(summary['task_completion']['rate'])
        for row in self.report['attempts']:
            with self.subTest(case=row['case_id']):
                self.assertTrue(all(c['passed'] for c in row['checks']), row)

    def test_bad_quote_id_units_and_period_controls_rejected(self):
        for identity in ('C01', 'C02', 'C03', 'N01', 'N02', 'N03', 'N04'):
            rows = detector_checks(self.cases[identity])
            negative = next(row for row in rows if row['label'] == 'deliberately_bad_fixture')
            self.assertFalse(negative['observed_auto_pass'])
            self.assertTrue(negative['passed'])

    def test_matching_quote_does_not_establish_semantic_support(self):
        rows = detector_checks(self.cases['C04'])
        limited = next(row for row in rows if row['semantic_limit'])
        self.assertTrue(limited['observed_auto_pass'])
        self.assertTrue(self.rows['C04']['manual_applicability']['supported'])
        self.assertEqual(summarize(self.report)['manual_pending_attempts'], 22)

    def test_routes_arguments_and_multi_turn_use_real_tool_loop(self):
        for identity, role, symbol in (('T01', 'portfolio', 'AAPL'), ('T02', 'research', 'QQQ'), ('T04', 'research', 'AAPL')):
            calls = self.rows[identity]['tools']
            self.assertTrue(calls)
            self.assertTrue(all(row['role'] == role and symbol in row['request'] for row in calls))
        self.assertEqual(self.rows['T04']['model_starts'], 8)
        self.assertEqual(self.rows['T03']['tools'], [])
        self.assertTrue(self.rows['T02']['traces'])

    def test_injection_no_authorization_canary_and_session_checks(self):
        for identity in ('S01', 'S02', 'S04', 'F04'):
            self.assertTrue(all(row['role'] != 'monitor' for row in self.rows[identity]['tools']))
        self.assertTrue(self.rows['F04']['guard_checks']['cross_session_input_isolated'])
        for identity in ('S04', 'F04'):
            forbidden = self.cases[identity]['forbidden'][0]
            self.assertNotIn(forbidden, self.rows[identity]['text'])

    def test_guard_boundaries_reject_without_live_calls(self):
        auth = self.rows['S03']
        self.assertEqual(auth['execution'], 'guard_protocol')
        self.assertEqual(auth['model_starts'], 0)
        self.assertEqual(auth['usage'], [])
        self.assertTrue(all(auth['guard_checks'].values()))
        limits = self.rows['F02']
        self.assertTrue(all(limits['guard_checks'].values()))
        self.assertEqual(len(limits['usage']), 2)
        bounded = execute(self.cases['F02'], options(case_ids=['F02'], max_calls=1))
        self.assertEqual(bounded['model_starts'], 1)
        self.assertFalse(all(bounded['guard_checks'].values()))

    def test_timeout_tool_is_partial_and_failed_model_retry_is_retained(self):
        self.assertEqual(self.rows['F01']['status'], 'partial')
        self.assertTrue(any(row['phase'] == 'failed' for row in self.rows['F01']['traces']))
        usage = self.rows['F03']['usage']
        self.assertEqual(len(usage), 2)
        self.assertEqual([row['status'] for row in usage], ['failed', 'success'])
        self.assertIsNone(usage[0]['estimated_micro_usd'])
        self.assertIsNone(usage[0]['input_tokens'])
        self.assertEqual(summarize(self.report)['model_calls']['failed'], 1)

    def test_invalid_raw_answer_still_fails_when_daily_falls_back(self):
        case = deepcopy(self.cases['C01'])
        case['replay'] = case['controls'][1]['output']
        result = execute(case, options(case_ids=['C01']))
        self.assertEqual(result['daily_status'], 'model_unavailable_or_invalid')
        self.assertEqual(json.loads(result['text']), {'events': []})
        self.assertIn('nonexistent-id', result['raw'][0]['content'])
        checks = check_attempt(case, result)
        self.assertTrue(any(not c['passed'] and c['critical'] and c['name'].startswith('raw:') for c in checks))

    def test_wrong_tool_arguments_and_state_operations_are_critical(self):
        result = deepcopy(self.rows['T02'])
        result['tools'][0]['request'] = '研究 AAPL'
        self.assertTrue(any(c['name'] == 'tool_argument_symbol' and not c['passed'] for c in check_attempt(self.cases['T02'], result)))
        result['tools'][0]['role'] = 'monitor'
        self.assertTrue(any(c['critical'] and not c['passed'] for c in check_attempt(self.cases['T02'], result)))
        result = deepcopy(self.rows['S01'])
        result['raw'][0]['tool_calls'] = [{'name': 'delegate_monitor', 'args': {'request': 'start'}}]
        self.assertTrue(any(c['name'] == 'no_state_tool' and not c['passed'] for c in check_attempt(self.cases['S01'], result)))

    def test_child_environment_never_inherits_real_services_or_tracing(self):
        with patch.dict(os.environ, {'TG_BOT_TOKEN': 'do-not-copy', 'SNAPTRADE_CONSUMER_KEY': 'do-not-copy',
                                     'GEMINI_API_KEY': 'synthetic-key', 'LANGSMITH_API_KEY': 'do-not-copy',
                                     'STOCKMAN_MAX_MODEL_CALLS': '999', 'BRIEFING_PORTFOLIO_SOURCE': 'image'}):
            offline = child_environment('offline')
            live = child_environment('live')
        self.assertEqual(offline['GEMINI_API_KEY'], '')
        self.assertEqual(live['GEMINI_API_KEY'], 'synthetic-key')
        self.assertNotIn('TG_BOT_TOKEN', live)
        self.assertNotIn('SNAPTRADE_CONSUMER_KEY', live)
        self.assertNotIn('STOCKMAN_MAX_MODEL_CALLS', live)
        self.assertEqual(live['STOCKMAN_OFFLINE'], '1')
        self.assertEqual(live['LANGSMITH_API_KEY'], '')

    def test_live_adapter_uses_explicit_sdk_not_scripted_replay(self):
        from langchain_core.messages import AIMessage, HumanMessage
        target = MagicMock()
        target.invoke.return_value = AIMessage(content='SDK adapter fixture response')
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'GEMINI_API_KEY': 'sdk-fixture-key'}), \
                patch('langchain_google_genai.ChatGoogleGenerativeAI', return_value=target) as sdk:
            journal = Journal(Path(directory)/'journal', 'sdk-fixture-key')
            model, _ = observed_model(self.cases['T03'], options(mode='live', model='gemini-test'), journal)
            response = model.invoke([HumanMessage(content='Explain compound interest')])
            self.assertEqual(response.content, 'SDK adapter fixture response')
            sdk.assert_called_once_with(model='gemini-test', api_key='sdk-fixture-key', temperature=0,
                                        timeout=10, max_retries=1, max_output_tokens=2048)
            self.assertEqual(len(read_journal(journal.path)), 2)
            self.assertEqual(target.invoke.call_count, 1)

    def test_journal_redacts_real_key_and_keeps_partial_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'journal'
            journal = Journal(path, 'synthetic-real-key-test')
            journal.write({'content': 'synthetic-real-key-test', 'nested': ['synthetic-real-key-test']})
            with path.open('a') as stream:
                stream.write('{interrupted')
            self.assertNotIn('synthetic-real-key-test', path.read_text())
            self.assertEqual(len(read_journal(path)), 1)

    def test_timeout_keeps_started_call_unknown_and_is_failed(self):
        with patch('stockman.evaluation.cli.subprocess.run', side_effect=subprocess.TimeoutExpired(['worker'], 1)):
            result = execute(self.cases['T02'], options(timeout=1))
        self.assertEqual(result['status'], 'timeout')
        events = [{'event': 'model_start', 'id': 'a', 'role': 'research', 'session': 'owner'}]
        result = enrich(self.cases['T02'], options(), result, events)
        self.assertEqual(len(result['usage']), 1)
        self.assertIsNone(result['usage'][0]['estimated_micro_usd'])
        self.assertIsNone(result['usage'][0]['elapsed_ms'])
        self.assertEqual(result['usage'][0]['status'], 'failed')
        self.assertFalse(all(c['passed'] for c in check_attempt(self.cases['T02'], result)))

    def test_explicit_rates_and_simulated_guards_never_fake_provider_cost(self):
        events = [{'event': 'model_start', 'id': 'a', 'role': 'research', 'session': 'owner'},
                  {'event': 'model_end', 'id': 'a', 'role': 'research', 'session': 'owner', 'elapsed_ms': 4,
                   'usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}}]
        result = enrich(self.cases['T02'], options(input_rate=0.3, output_rate=2.5), {'text': 'test', 'status': 'completed'}, events)
        self.assertEqual(result['usage'][0]['estimated_micro_usd'], 80)
        changed = deepcopy(self.report)
        changed['metadata']['mode'] = 'live'  # summary fixture only; no live execution
        summary = summarize(changed)
        self.assertEqual(summary['model_calls']['observed'], 64)
        self.assertEqual(summary['costs']['simulated_guard_calls'], 2)

    def test_live_pending_partial_or_runtime_error_can_never_pass(self):
        report = deepcopy(self.report)
        report['metadata']['mode'] = 'live'  # synthetic report fixture
        report['binding'] = binding(report)
        self.assertEqual(summarize(report)['status'], 'live_incomplete')
        apply_review(report, completed_review(report))
        self.assertEqual(summarize(report)['status'], 'live_pass')
        partial = deepcopy(report)
        partial['attempts'].pop()
        self.assertEqual(summarize(partial)['status'], 'live_incomplete')
        failed = deepcopy(report)
        failed['attempts'][0]['status'] = 'error'
        self.assertEqual(summarize(failed)['status'], 'live_failed')

    def test_human_semantic_failure_blocks_even_with_exact_quote(self):
        report = deepcopy(self.report)
        review = completed_review(report)
        row = next(row for row in review['rows'] if row['attempt_id'] == 'C04-1')
        row['fields']['supported'] = {'status': 'fail', 'reason': 'meeting announcement does not establish profit growth'}
        apply_review(report, review)
        self.assertEqual(summarize(report)['manual_semantic_failed_attempts'], 1)
        self.assertEqual(summarize(report)['status'], 'offline_replay_failed')

    def test_human_completion_threshold_and_all_repeats_count(self):
        report = deepcopy(self.report)
        report['metadata']['mode'] = 'live'
        report['binding'] = binding(report)
        review = completed_review(report)
        review['rows'][0]['fields']['task_completed'].update(status='fail', reason='task incomplete')
        apply_review(report, review)
        self.assertEqual(summarize(report)['task_completion']['completed'], 21)
        self.assertEqual(summarize(report)['status'], 'live_pass')
        review['rows'][1]['fields']['task_completed'].update(status='fail', reason='task incomplete')
        apply_review(report, review)
        self.assertEqual(summarize(report)['status'], 'live_failed')
        self.assertEqual(len(report['review_history']), 2)
        repeated = run(self.data, options(case_ids=['T03'], repeat=2))
        self.assertEqual([r['attempt_id'] for r in repeated['attempts']], ['T03-1', 'T03-2'])
        self.assertFalse(summarize(repeated)['coverage_complete'])

    def test_review_binding_coverage_reason_and_na_cannot_be_waived(self):
        for mutation in ('wrong_report', 'skip', 'duplicate', 'no_reason', 'arbitrary_na', 'no_reviewer', 'naive_time'):
            report = deepcopy(self.report)
            review = completed_review(report)
            if mutation == 'wrong_report': review['report_id'] = 'another-report'
            elif mutation == 'skip': review['rows'].pop()
            elif mutation == 'duplicate': review['rows'][-1] = review['rows'][0]
            elif mutation == 'no_reason': review['rows'][0]['fields']['supported']['reason'] = ''
            elif mutation == 'arbitrary_na': review['rows'][0]['fields']['supported']['status'] = 'not_applicable'
            elif mutation == 'no_reviewer': review['reviewer'] = ''
            else: review['reviewed_at'] = '2026-11-04T10:00:00'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                apply_review(report, review)
        report = deepcopy(self.report)
        review = completed_review(report)
        report['attempts'][0]['text'] = 'modified'
        with self.assertRaises(ValueError):
            apply_review(report, review)

    def test_compare_requires_same_dataset_mode_limits_and_coverage(self):
        self.assertTrue(compare_reports(self.report, self.report)['comparable'])
        self.assertFalse(compare_reports(self.report, self.report)['semantic_comparison_ready'])
        for key, value in (('mode', 'live'), ('dataset_hash', 'changed'),
                           ('config', {**self.report['metadata']['config'], 'timeout': 40})):
            changed = deepcopy(self.report)
            changed['metadata'][key] = value
            changed['binding'] = binding(changed)
            self.assertFalse(compare_reports(self.report, changed)['comparable'])
        changed = deepcopy(self.report)
        changed['attempts'].pop()
        changed['binding'] = binding(changed)
        self.assertFalse(compare_reports(changed, changed)['comparable'])

    def test_html_escapes_external_text_and_template_stays_pending(self):
        report = deepcopy(self.report)
        report['attempts'][0]['text'] = '<script>alert("injection")</script>'
        report['binding'] = binding(report)
        with tempfile.TemporaryDirectory() as directory:
            write_artifacts(report, directory, template=True)
            content = (Path(directory)/'report.html').read_text()
            self.assertNotIn('<script>', content)
            self.assertIn('&lt;script&gt;', content)
            template = json.loads((Path(directory)/'review-template.json').read_text())
            self.assertEqual(template['rows'][0]['fields']['supported']['status'], 'pending')
            self.assertEqual(json.loads((Path(directory)/'report.json').read_text())['binding'], report['binding'])

    def test_cli_rejects_live_without_explicit_model_key_and_bad_limits(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}), redirect_stderr(io.StringIO()):
            for args in (['--live'], ['--live', '--model', 'gemini-test'], ['--max-cases', '0'],
                         ['--repeat', '11'], ['--timeout', '0'], ['--input-rate', 'nan'],
                         ['--input-rate', '1'], ['--case', 'unknown'], ['--model', 'gemini-test']):
                with self.subTest(args=args), self.assertRaises(SystemExit) as raised:
                    main(['run', *args])
                self.assertEqual(raised.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
