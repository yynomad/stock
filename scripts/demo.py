#!/usr/bin/env python3
"""Explicit offline protocol demos; no real account, model or messages."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true',required=True)
    parser.add_argument('--scenario',choices=['routing','persistence','portfolio','research','monitor','end-to-end'],required=True)
    args=parser.parse_args()
    os.environ['STOCKMAN_OFFLINE']='1'
    os.environ['LANGSMITH_TRACING']='false'
    from stockman.agents import AgentRuntime,OfflineModel
    with tempfile.TemporaryDirectory() as directory:
        runtime=AgentRuntime(directory,OfflineModel())
        if args.scenario=='end-to-end':
            runtime.close()
            from stockman.offline import end_to_end
            print(json.dumps(end_to_end(directory),ensure_ascii=False))
            return
        if args.scenario=='monitor':
            from stockman.monitoring import MonitoringService
            service=MonitoringService(runtime.store,None,in_session=lambda now:True)
            runtime.store.execute('INSERT INTO rules(symbol,spec) VALUES(?,?)',('AAPL',json.dumps({'support':100,'resistance':120,'buffer':0.02})))
            quote={'price':125,'asof':1000,'kind':'regular','valid':True}
            event=service.check('AAPL',quote,1001)
            assert event and service.check('AAPL',quote,1002) is None
            assert service.check('AAPL',dict(quote,asof=1),1002) is None
            print('PASS: synthetic crossing event, stale filtering and persistent deduplication')
        elif args.scenario=='research':
            from stockman.research import ResearchService
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
            from stockman.portfolio import PortfolioService
            from tests_data import demo_bars
            service=PortfolioService(runtime.store,ibkr=lambda account:{'AAPL':{'shares':0.5,'avg_cost':100}},
                                     bars=demo_bars)
            runtime.close()
            runtime=AgentRuntime(directory,OfflineModel(),{'portfolio':service.report})
            # Bind service to the reopened business connection.
            service.store=runtime.store
            result=runtime.run('检查持仓',session='demo')
            assert 'AAPL' in result['text'] and 'ibkr' in result['text']
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
