import json
import os
import tempfile
import unittest
from unittest.mock import patch
from stockman.agents import AgentRuntime,OfflineModel,CallLimit
from stockman.harness import ContextOverflow
from stockman.service_config import ModelConfig

class AgentTest(unittest.TestCase):
    def test_deepseek_runs_production_tool_chain_through_configured_endpoint(self):
        from test_model_providers import intercept, reply
        requests = []

        def respond(http, request):
            body = json.loads(request.content)
            requests.append((request,body))
            last = body['messages'][-1]
            names = {tool['function']['name'] for tool in body['tools']}
            child = 'portfolio_tool' in names
            if last['role'] == 'tool':
                payload = reply('deepseek',False)
                payload['choices'][0]['message']['content'] = 'verified fixture evidence'
            else:
                payload = reply('deepseek',True)
                function = payload['choices'][0]['message']['tool_calls'][0]['function']
                function['name'] = 'portfolio_tool' if child else 'delegate_portfolio'
                function['arguments'] = '{"request":"portfolio summary"}'
            return http.Response(200,json=payload,request=request)

        def portfolio(request: str):
            return 'fixture portfolio evidence'

        settings = ModelConfig(key='fixture-private-key',name='provider/alias',provider='deepseek',
                               base_url='https://gateway.invalid/llm')
        with tempfile.TemporaryDirectory() as directory, intercept('deepseek',respond):
            runtime = AgentRuntime(directory,services={'portfolio':portfolio},settings=settings)
            try:
                result = runtime.run('检查持仓')
                self.assertEqual(result['status'],'completed')
                self.assertEqual(len(requests),4)
                self.assertTrue(all(request.url.host=='gateway.invalid' and body['model']=='provider/alias'
                                    for request,body in requests))
                self.assertIn('fixture portfolio evidence',repr(requests[2][1]))
                self.assertNotIn(settings.key,repr([body for _,body in requests]))
                from stockman.usage import calls
                usage = calls(runtime.store,result['task_id'])
                self.assertEqual(len(usage),4)
                self.assertEqual({(row['provider'],row['model'],row['cached_input_tokens']) for row in usage},
                                 {('deepseek','provider/alias',2)})
                self.assertTrue(all(row['extra_cost_unknown'] and row['estimated_micro_usd'] is None for row in usage))
            finally:
                runtime.close()

    def test_resume_rejects_changed_gateway_and_keeps_original_task(self):
        from stockman.model_providers import ModelConfigurationError
        from dataclasses import replace
        settings = ModelConfig(key='fixture-private-key',name='market-alias',provider='deepseek',
                               base_url='https://gateway-a.invalid/llm')
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory,OfflineModel(),settings=settings)
            task = runtime.store.create_task('owner','question',runtime.model_route)
            runtime.store.finish(task,'interrupted')
            runtime.close()
            runtime = AgentRuntime(directory,OfflineModel(),settings=replace(settings,base_url='https://gateway-b.invalid/llm'))
            try:
                with self.assertRaisesRegex(ModelConfigurationError,'模型配置已改变'):
                    runtime.resume(task)
                row = runtime.store.task(task)
                self.assertEqual(row['status'],'interrupted')
                self.assertNotIn('gateway-a',repr(row))
                self.assertNotIn(settings.key,repr(row))
            finally:
                runtime.close()

    def test_operator_style_memory_survives_restart_and_is_session_scoped(self):
        from scripts.chat import response_style
        from stockman.storage import RESPONSE_STYLES
        observed = []

        class ObservedModel(OfflineModel):
            def _generate(self, messages, **kwargs):
                observed.append(messages[0].content)
                return super()._generate(messages, **kwargs)

        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(response_style(directory, 'owner', 'detailed'), {'response_style': 'detailed'})
            runtime = AgentRuntime(directory, ObservedModel())
            try:
                runtime.run('记住偏好简短回答', 'owner')
                runtime.run('你好', 'another')
                self.assertEqual(runtime.store.response_style('owner'), 'detailed')
                self.assertIn(RESPONSE_STYLES['detailed'], observed[0])
                self.assertNotIn(RESPONSE_STYLES['detailed'], observed[1])
                with self.assertRaisesRegex(RuntimeError, 'already in use'):
                    response_style(directory, 'owner', 'clear')
            finally:
                runtime.close()
            self.assertEqual(response_style(directory, 'owner', 'show'), {'response_style': 'detailed'})
            self.assertEqual(response_style(directory, 'owner', 'clear'), {'response_style': None})
            self.assertEqual(response_style(directory, 'owner', 'show'), {'response_style': None})

    def test_context_keeps_whole_recent_turns_without_rewriting_checkpoint(self):
        from langchain_core.messages import HumanMessage, ToolMessage
        observed = []

        class ObservedModel(OfflineModel):
            def _generate(self, messages, **kwargs):
                observed.append(list(messages))
                return super()._generate(messages, **kwargs)

        def collect(request: str):
            return 'CURRENT_EVIDENCE'

        settings = ModelConfig(key='', context_bytes=4096)
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory, ObservedModel(), settings=ModelConfig(key=''))
            old = '旧' * 850
            try:
                runtime.run(old, 'owner')
                runtime.run('保留这条旧问题', 'owner')
            finally:
                runtime.close()
            runtime = AgentRuntime(directory, ObservedModel(), {'research': collect}, settings=settings)
            try:
                result = runtime.run('研究 AAPL', 'owner')
                self.assertIn('CURRENT_EVIDENCE', result['text'])
                self.assertTrue(any(isinstance(m, ToolMessage) for m in observed[-1]))
                self.assertNotIn(old, [m.content for m in observed[-1]])
                self.assertIn('保留这条旧问题', [m.content for m in observed[-1]])
                checkpoint = runtime.agent.get_state({'configurable': {'thread_id': 'owner'}})
                self.assertIn(old, [m.content for m in checkpoint.values['messages'] if isinstance(m, HumanMessage)])
                traces = [json.loads(t['payload']) for t in runtime.store.traces(result['task_id'])
                          if t['tool'] == 'context' and t['phase'] == 'prepared']
                self.assertTrue(any(t['omitted_messages'] == 2 for t in traces))
                self.assertTrue(all(t['after_bytes'] <= t['limit_bytes'] for t in traces))
            finally:
                runtime.close()

    def test_context_expires_and_drops_prior_tool_evidence_across_restart(self):
        import time
        from langchain_core.messages import HumanMessage
        observed = []

        class ObservedModel(OfflineModel):
            def _generate(self, messages, **kwargs):
                observed.append(list(messages))
                return super()._generate(messages, **kwargs)

        def collect(request: str):
            return 'OLD_PRIVATE_QUOTE'

        settings = ModelConfig(key='', context_ttl=60)
        now = time.time()
        with tempfile.TemporaryDirectory() as directory:
            with patch('time.time', return_value=now - 61):
                runtime = AgentRuntime(directory, ObservedModel(), settings=settings)
                runtime.run('过期问答', 'owner')
            with patch('time.time', return_value=now - 10):
                runtime.run('保留近期约束', 'owner')
                runtime.close()
                runtime = AgentRuntime(directory, ObservedModel(), {'research': collect}, settings=settings)
                runtime.run('研究 AAPL', 'owner')
                runtime.close()
            with patch('time.time', return_value=now):
                runtime = AgentRuntime(directory, ObservedModel(), settings=settings)
                try:
                    result = runtime.run('继续说明', 'owner')
                    model_input = repr(observed[-1])
                    self.assertNotIn('OLD_PRIVATE_QUOTE', model_input)
                    self.assertNotIn('过期问答', model_input)
                    self.assertIn('保留近期约束', model_input)
                    checkpoint = runtime.agent.get_state({'configurable': {'thread_id': 'owner'}})
                    self.assertIn('OLD_PRIVATE_QUOTE', repr(checkpoint.values['messages']))
                    self.assertIn('过期问答', [m.content for m in checkpoint.values['messages'] if isinstance(m, HumanMessage)])
                    stats = next(json.loads(row['payload']) for row in runtime.store.traces(result['task_id'])
                                 if row['tool'] == 'context')
                    self.assertEqual((stats['expired_turns'], stats['evidence_turns']), (1, 1))
                finally:
                    runtime.close()

    def test_oversized_question_is_blocked_before_model_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory, OfflineModel(), settings=ModelConfig(key='', context_bytes=4096))
            try:
                with patch.object(OfflineModel, '_generate', side_effect=AssertionError('model invoked')):
                    with self.assertRaises(ContextOverflow):
                        runtime.run('问题' * 3000)
                task = runtime.store.execute('SELECT id,status FROM tasks')[0]
                self.assertEqual(task['status'], 'failed')
                self.assertFalse(runtime.store.execute('SELECT id FROM usage_calls'))
                trace = next(t for t in runtime.store.traces(task['id']) if t['phase'] == 'blocked')
                self.assertNotIn('问题', trace['payload'])
                self.assertGreater(json.loads(trace['payload'])['active_bytes'], 4096)
            finally:
                runtime.close()

    def test_real_langchain_tool_loop_and_routing(self):
        with tempfile.TemporaryDirectory() as d:
            received=[]
            def collect(request: str):
                received.append(request);return '证据 AAPL'
            runtime=AgentRuntime(d,OfflineModel(),{'research':collect})
            result=runtime.run('研究 AAPL')
            self.assertEqual(received,['研究 AAPL'])
            self.assertIn('证据 AAPL',result['text'])
            traces=runtime.store.traces(result['task_id'])
            self.assertTrue(any(t['tool']=='delegate_research' and t['phase']=='success' for t in traces))
            self.assertFalse(any(t['tool']=='delegate_portfolio' for t in traces))
            decision = next(t for t in traces if t['tool'] == 'decision' and t['agent'] == 'supervisor')
            self.assertEqual(json.loads(decision['payload'])['steps'], ['delegate_research'])
            delegate = next(t for t in traces if t['tool'] == 'delegate_research' and t['phase'] == 'start')
            child = next(t for t in traces if t['tool'] == 'research_tool' and t['phase'] == 'start')
            self.assertEqual(json.loads(child['payload'])['parent_tool'], json.loads(delegate['payload'])['span'])
            self.assertEqual(result['model_calls'],4)
            runtime.close()

    def test_checkpoints_and_completed_task_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel());result=runtime.run('hello',session='one');runtime.close()
            runtime=AgentRuntime(d,OfflineModel())
            self.assertEqual(runtime.store.task(result['task_id'])['status'],'completed')
            self.assertEqual(len(runtime.agent.get_state({'configurable':{'thread_id':'one'}}).values['messages']),2)
            self.assertEqual(runtime.agent.get_state({'configurable':{'thread_id':'two'}}).values,{})
            runtime.close()

    def test_limit_and_failed_task(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'STOCKMAN_MAX_MODEL_CALLS':'1'}):
            runtime=AgentRuntime(d,OfflineModel())
            with self.assertRaises(CallLimit):runtime.run('研究 AAPL')
            self.assertEqual(runtime.store.execute('SELECT status FROM tasks')[0]['status'],'failed')
            runtime.close()

    def test_tool_failure_is_recorded_without_secret(self):
        def broken(request: str):raise RuntimeError('secret-value-that-must-not-be-logged')
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel(),{'research':broken})
            result=runtime.run('研究 AAPL')
            self.assertIn('工具调用失败',result['text'])
            self.assertEqual(result['status'],'partial')
            self.assertEqual(runtime.store.task(result['task_id'])['status'],'partial')
            self.assertNotIn('secret-value',str(runtime.store.traces(result['task_id'])))
            self.assertTrue(any(t['phase']=='failed' for t in runtime.store.traces(result['task_id'])))
            runtime.close()

    def test_local_execution_disables_inherited_cloud_tracing(self):
        from langsmith.run_helpers import get_tracing_context
        observed=[]
        def collect(request: str):
            observed.append(get_tracing_context()['enabled']);return 'local evidence'
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel(),{'research':collect})
            runtime.run('研究 AAPL');runtime.close()
        self.assertEqual(observed,[False])

    def test_legacy_image_task_cannot_resume_as_broker_task(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel())
            task=runtime.store.create_task('owner','检查截图持仓')
            runtime.store.execute('CREATE TABLE task_context(task_id TEXT PRIMARY KEY,snapshot TEXT)')
            snapshot=runtime.store.snapshot('image','owner',{'NVDA':{'shares':2}})
            runtime.store.execute('INSERT INTO task_context VALUES(%s,%s)',(task,snapshot))
            runtime.store.finish(task,'interrupted');runtime.close()
            runtime=AgentRuntime(d,OfflineModel())
            try:
                with patch.object(runtime.agent,'get_state') as state,patch.object(runtime.agent,'invoke') as invoke:
                    with self.assertRaisesRegex(ValueError,'截图任务功能已移除'):runtime.resume(task)
                    state.assert_not_called();invoke.assert_not_called()
                self.assertEqual(runtime.store.task(task)['status'],'interrupted')
                self.assertEqual(set(runtime.store.get_snapshot(snapshot)['positions']),{'NVDA'})
            finally:runtime.close()

    def test_text_resume_works_with_absent_or_empty_legacy_context(self):
        from types import SimpleNamespace
        from stockman.agents import CURRENT
        for legacy_table in (False,True):
            with self.subTest(legacy_table=legacy_table),tempfile.TemporaryDirectory() as d:
                runtime=AgentRuntime(d,OfflineModel());observed=[]
                task=runtime.store.create_task('owner','研究 AAPL')
                if legacy_table:
                    runtime.store.execute('CREATE TABLE task_context(task_id TEXT PRIMARY KEY,snapshot TEXT)')
                    runtime.store.execute('INSERT INTO task_context VALUES(%s,NULL)',(task,))
                runtime.store.finish(task,'interrupted')
                def invoke(payload,config):
                    scope=CURRENT.get();observed.append((scope.session,scope.request))
                    return {'messages':[SimpleNamespace(content='恢复文字任务')]}
                try:
                    with patch.object(runtime.agent,'get_state',return_value=SimpleNamespace(next=('tools',))),patch.object(runtime.agent,'invoke',side_effect=invoke):
                        self.assertEqual(runtime.resume(task)['status'],'completed')
                    self.assertEqual(observed,[('owner','研究 AAPL')])
                finally:runtime.close()

    def test_parallel_sessions_keep_context_and_all_writes(self):
        from concurrent.futures import ThreadPoolExecutor
        from stockman.agents import CURRENT
        def collect(request: str):return 'session='+CURRENT.get().session
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel(),{'research':collect})
            try:
                def run(session):return runtime.run('研究 AAPL',session)
                with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(run,['one','two','one','two']))
                self.assertEqual(len(runtime.store.execute('SELECT id FROM tasks')),4)
                self.assertTrue(all(r['status']=='completed' for r in results))
                for result,session in zip(results,['one','two','one','two']):self.assertIn('session='+session,result['text'])
            finally:runtime.close()

    def test_unfinished_task_is_marked_interrupted(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel());task=runtime.store.create_task('x','synthetic');runtime.close()
            runtime=AgentRuntime(d,OfflineModel())
            self.assertEqual(runtime.store.task(task)['status'],'interrupted')
            with self.assertRaises(ValueError):runtime.resume(task)
            runtime.close()

    def test_resume_rejects_newer_same_session_task_with_equal_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory, OfflineModel())
            try:
                with patch('stockman.storage.time.time', return_value=1):
                    older = runtime.store.create_task('owner', '先前问题')
                    runtime.store.finish(older, 'interrupted')
                    runtime.store.create_task('owner', '后续问题')
                with patch.object(runtime.agent, 'get_state') as state:
                    with self.assertRaisesRegex(ValueError, '已有后续任务'):
                        runtime.resume(older)
                    state.assert_not_called()
            finally:
                runtime.close()
