import json
import os
import tempfile
import unittest
from unittest.mock import patch
from lib.agents import AgentRuntime,OfflineModel,CallLimit

class AgentTest(unittest.TestCase):
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

    def test_resume_keeps_image_source_and_session_scope(self):
        from types import SimpleNamespace
        from lib.agents import CURRENT
        observed=[]
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel())
            task=runtime.store.create_task('owner','检查截图持仓')
            runtime.store.execute('INSERT INTO task_context VALUES(?,?)',(task,'image-snapshot-id'))
            runtime.store.finish(task,'interrupted')
            def invoke(payload,config):
                scope=CURRENT.get();observed.append((scope.session,scope.snapshot))
                return {'messages':[SimpleNamespace(content='恢复截图来源')]}
            with patch.object(runtime.agent,'get_state',return_value=SimpleNamespace(next=('tools',))),patch.object(runtime.agent,'invoke',side_effect=invoke):
                self.assertEqual(runtime.resume(task)['status'],'completed')
            self.assertEqual(observed,[('owner','image-snapshot-id')])
            runtime.close()

    def test_parallel_sessions_keep_context_and_all_writes(self):
        from concurrent.futures import ThreadPoolExecutor
        from lib.agents import CURRENT
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
