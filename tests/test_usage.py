import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path
from stockman.agents import AgentRuntime, OfflineModel, TaskBudgetExceeded
from stockman.storage import Store
from stockman.usage import calls, record


class UsageTest(unittest.TestCase):
    def test_known_and_unknown_usage_are_not_conflated(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/"stockman.sqlite")
            try:
                task=store.create_task("local","test")
                record(store,task,"research","daily_model","gemini-test",time.monotonic(),
                       {"input_tokens":100,"output_tokens":200,"total_tokens":300},
                       input_rate=0.3,output_rate=2.5)
                record(store,task,"research","grounded_search","gemini-test",time.monotonic(),extra_unknown=True,error=True)
                rows=calls(store,task)
                self.assertEqual(rows[0]["estimated_micro_usd"],530)
                self.assertEqual(rows[0]["input_tokens"],100)
                self.assertIsNone(rows[1]["input_tokens"])
                self.assertIsNone(rows[1]["estimated_micro_usd"])
                self.assertEqual(rows[1]["extra_cost_unknown"],1)
                self.assertEqual(rows[1]["status"],"failed")
            finally: store.close()

    def test_agent_call_records_are_scoped_to_task_and_safe(self):
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,OfflineModel())
            try:
                a=runtime.run("hello secret-value",session="one")
                b=runtime.run("hello again",session="two")
                rows=calls(runtime.store,a["task_id"])
                self.assertEqual(len(rows),a["model_calls"])
                self.assertTrue(all(row["input_tokens"] is None for row in rows))
                self.assertNotIn("secret-value",str(rows))
                self.assertEqual(len(calls(runtime.store,b["task_id"])),b["model_calls"])
                out=subprocess.check_output([sys.executable,str(Path(__file__).resolve().parents[1]/"scripts/tasks.py"),"--data-dir",d,"--task",a["task_id"]],text=True)
                self.assertIn("usage_calls",json.loads(out))
                self.assertIn("input_tokens",json.loads(out)["usage_calls"][0])
            finally: runtime.close()

    def test_real_langchain_middleware_collects_model_usage(self):
        class UsageModel(OfflineModel):
            def _generate(self,*args,**kwargs):
                result=super()._generate(*args,**kwargs)
                result.generations[0].message.usage_metadata={"input_tokens":12,"output_tokens":8,"total_tokens":20}
                return result
        with tempfile.TemporaryDirectory() as d:
            runtime=AgentRuntime(d,UsageModel())
            try:
                result=runtime.run("hello")
                row=calls(runtime.store,result["task_id"])[0]
                self.assertEqual((row["input_tokens"],row["output_tokens"],row["total_tokens"]),(12,8,20))
                self.assertIsNone(row["estimated_micro_usd"])
            finally: runtime.close()

    def test_grounded_search_records_even_when_citation_validation_fails(self):
        from stockman.agents import CURRENT, Invocation
        from stockman.research import GoogleNews
        class Metadata:
            def model_dump(self, **kwargs):
                return {"prompt_token_count": 10, "candidates_token_count": 4, "total_token_count": 14}
        class Client:
            def __init__(self, **kwargs): self.models=self
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def generate_content(self, **kwargs): return SimpleNamespace(usage_metadata=Metadata())
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/"stockman.sqlite")
            token=CURRENT.set(Invocation("task","local",store=store))
            try:
                with patch("google.genai.Client",Client), patch("stockman.research.parse_grounding",side_effect=ValueError("invalid citation")):
                    with self.assertRaises(ValueError): GoogleNews("synthetic","gemini-test").fetch("AAPL")
                row=calls(store,"task")[0]
                self.assertEqual(row["kind"],"grounded_search")
                self.assertEqual(row["input_tokens"],10)
                self.assertEqual(row["extra_cost_unknown"],1)
            finally: CURRENT.reset(token);store.close()

    def test_known_budget_stops_next_priced_call_and_persists_explanation(self):
        class PricedModel(OfflineModel):
            model_name: str="gemini-synthetic"
            def _generate(self,*args,**kwargs):
                response=super()._generate(*args,**kwargs)
                response.generations[0].message.usage_metadata={"input_tokens":12,"output_tokens":8,"total_tokens":20}
                return response
        env={"STOCKMAN_TASK_BUDGET_USD":"0.000010","STOCKMAN_TASK_INPUT_RATE":"1","STOCKMAN_TASK_OUTPUT_RATE":"1","STOCKMAN_TASK_PRICE_MODEL":"gemini-synthetic"}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,env):
            runtime=AgentRuntime(d,PricedModel())
            try:
                with self.assertRaises(TaskBudgetExceeded):runtime.run("研究 AAPL")
                task=runtime.store.execute("SELECT * FROM tasks")[0]
                self.assertEqual(task["status"],"failed")
                self.assertEqual(len(calls(runtime.store,task["id"])),1)
                self.assertTrue(any(x["phase"]=="budget_exhausted" for x in runtime.store.traces(task["id"])))
                out=subprocess.check_output([sys.executable,str(Path(__file__).resolve().parents[1]/"scripts/tasks.py"),"--data-dir",d,"--task",task["id"]],text=True)
                summary=json.loads(out)["usage_summary"]
                self.assertTrue(summary["limit_reached"])
                self.assertEqual(summary["known_estimated_micro_usd"],20)
            finally:runtime.close()

    def test_unknown_model_cost_continues_and_is_reported(self):
        env={"STOCKMAN_TASK_BUDGET_USD":"0.000010","STOCKMAN_TASK_INPUT_RATE":"1","STOCKMAN_TASK_OUTPUT_RATE":"1","STOCKMAN_TASK_PRICE_MODEL":"different-model"}
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,env):
            runtime=AgentRuntime(d,OfflineModel(),{"research":lambda request:"evidence"})
            try:
                result=runtime.run("研究 AAPL")
                self.assertEqual(result["status"],"completed")
                self.assertGreater(result["usage_summary"]["cost_unknown_calls"],0)
                self.assertIn("费用部分或全部未知",result["text"])
            finally:runtime.close()

    def test_parallel_child_calls_share_the_same_threshold(self):
        class PricedModel(OfflineModel):
            model_name: str="gemini-synthetic"
            def _generate(self,*args,**kwargs):
                time.sleep(0.01)
                response=super()._generate(*args,**kwargs)
                response.generations[0].message.usage_metadata={"input_tokens":12,"output_tokens":8,"total_tokens":20}
                return response
        env={"STOCKMAN_TASK_BUDGET_USD":"0.000050","STOCKMAN_TASK_INPUT_RATE":"1","STOCKMAN_TASK_OUTPUT_RATE":"1","STOCKMAN_TASK_PRICE_MODEL":"gemini-synthetic"}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,env):
            runtime=AgentRuntime(d,PricedModel())
            try:
                with self.assertRaises(TaskBudgetExceeded): runtime.run("完整分析 AAPL")
                task=runtime.store.execute("SELECT id FROM tasks")[0]["id"]
                rows=calls(runtime.store,task)
                self.assertEqual(len(rows),3)
                self.assertEqual(sum(r["estimated_micro_usd"] for r in rows),60)
            finally:runtime.close()

    def test_configured_budget_persists_in_task_ledger(self):
        env={"STOCKMAN_TASK_BUDGET_USD":"0.000029"}
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,env):
            runtime=AgentRuntime(d,OfflineModel())
            try:
                result=runtime.run("hello")
                row=runtime.store.execute("SELECT limit_micro_usd FROM task_budget_limits WHERE task_id=?",(result["task_id"],))[0]
                self.assertEqual(row["limit_micro_usd"],29)
            finally:runtime.close()

    def test_google_thought_tokens_are_included_without_inventing_missing_usage(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/"stockman.sqlite")
            try:
                record(store,"a","research","grounded_search","gemini-test",time.monotonic(),
                    {"prompt_token_count":5,"candidates_token_count":3,"thoughts_token_count":2,"total_token_count":10},extra_unknown=True)
                row=calls(store,"a")[0]
                self.assertEqual(row["output_tokens"],5)
                self.assertIsNone(row["estimated_micro_usd"])
            finally: store.close()
