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
    parser.add_argument('--scenario',choices=['routing','persistence','portfolio','research'],required=True)
    args=parser.parse_args()
    os.environ['STOCKMAN_OFFLINE']='1'
    os.environ['LANGSMITH_TRACING']='false'
    from lib.agents import AgentRuntime,OfflineModel
    with tempfile.TemporaryDirectory() as directory:
        runtime=AgentRuntime(directory,OfflineModel())
        if args.scenario=='research':
            from lib.research import ResearchService
            from tests_data import demo_bars
            class Market:
                def info(self,symbol):return {'quoteType':'ETF','longName':'Synthetic QQQ'}
                bars=staticmethod(demo_bars)
            class News:
                def fetch(self,symbol):raise ValueError('offline demo: no network news')
            service=ResearchService(market=Market(),news=News())
            runtime.close();runtime=AgentRuntime(directory,OfflineModel(),{'research':service.collect})
            result=runtime.run('研究 QQQ')
            assert 'ETF' in result['text'] and 'not_applicable' in result['text']
            print(json.dumps(result,ensure_ascii=False))
        elif args.scenario=='portfolio':
            from lib.portfolio import PortfolioService
            from tests_data import demo_bars
            service=PortfolioService(runtime.store,ibkr=lambda account:{'AAPL':{'shares':0.5,'avg_cost':100}},
                                     image=lambda path:{'NVDA':{'shares':2,'avg_cost':None}},bars=demo_bars)
            identity=service.import_image('synthetic','demo')
            runtime.close()
            runtime=AgentRuntime(directory,OfflineModel(),{'portfolio':service.report})
            # Bind service to the reopened business connection.
            service.store=runtime.store
            result=runtime.run('检查截图持仓',session='demo',snapshot=identity)
            assert 'NVDA' in result['text'] and 'image' in result['text']
            print(json.dumps(result,ensure_ascii=False))
        elif args.scenario=='routing':
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
