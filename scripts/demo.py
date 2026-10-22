#!/usr/bin/env python3
"""Explicit offline protocol demos; no real account, model or messages."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true',required=True)
    parser.add_argument('--scenario',choices=['routing','persistence'],required=True)
    args=parser.parse_args()
    os.environ['STOCKMAN_OFFLINE']='1'
    os.environ['LANGSMITH_TRACING']='false'
    from lib.agents import AgentRuntime,OfflineModel
    with tempfile.TemporaryDirectory() as directory:
        runtime=AgentRuntime(directory,OfflineModel())
        if args.scenario=='routing':
            for query in ['检查持仓','研究 AAPL','监控状态','你好']:
                result=runtime.run(query,session=query)
                print(json.dumps(result,ensure_ascii=False))
                print(json.dumps(runtime.store.traces(result['task_id']),ensure_ascii=False))
        else:
            result=runtime.run('你好',session='demo')
            runtime.close();runtime=AgentRuntime(directory,OfflineModel())
            assert runtime.store.task(result['task_id'])['status']=='completed'
            state=runtime.agent.get_state({'configurable':{'thread_id':'demo'}})
            assert len(state.values['messages'])==2
            print('PASS: SQLite business state and LangChain conversation survived restart')
        runtime.close()
if __name__=='__main__':main()
