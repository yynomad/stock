import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.agents import AgentRuntime,OfflineModel
from lib.storage import Store
from lib.portfolio import PortfolioService,normalize_positions
from lib.ibkr import fetch_ibkr_positions,IBKRUnavailable
from premarket_grid_calculator import _build_symbols


def bars(symbol='AAPL'):
    return [{'date':f'2026-09-{i+1:02}','open':100+i,'close':101+i,'high':103+i,'low':99+i,'volume':100} for i in range(45)]


class PortfolioTest(unittest.TestCase):
    def test_fractional_shares_and_null_cost(self):
        result=normalize_positions({'AAPL':{'shares':0.5,'avg_cost':None}})
        self.assertEqual(result['AAPL']['shares'],0.5)
        self.assertIsNone(result['AAPL']['avg_cost'])

    def test_invalid_and_unsupported_data_rejected(self):
        for data in [{'AAPL':{'shares':float('nan')}},{'AAPL':{'shares':True}},{'1321.T':{'shares':1}},{'AAPL':{'currency':'JPY'}},{'AAPL':{'avg_cost':0}}]:
            with self.assertRaises(ValueError):normalize_positions(data)

    def test_image_reads_once_and_does_not_replace_ibkr(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite');calls=[]
            def image(path):calls.append(path);return {'NVDA':{'shares':2,'avg_cost':90}}
            service=PortfolioService(store,ibkr=lambda account:{'AAPL':{'shares':0.5,'avg_cost':100}},image=image,bars=bars)
            with patch.dict(os.environ,{'IBKR_ACCOUNT':'U1234567'}):
                first=service.load();snapshot=service.import_image('synthetic','chat')
                self.assertEqual(service.load('chat',snapshot)['source'],'image')
                self.assertEqual(store.latest('ibkr','U1234567')['positions'],first['positions'])
                with self.assertRaises(ValueError):service.load('different-chat',snapshot)
            self.assertEqual(calls,['synthetic']);store.close()

    def test_disconnect_reports_cache_and_never_another_account(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite');store.snapshot('ibkr','U1234567',{},asof=100)
            def broken(account):raise TimeoutError()
            service=PortfolioService(store,ibkr=broken,bars=bars)
            with patch.dict(os.environ,{'IBKR_ACCOUNT':'U1234567'}):
                row=service.load();self.assertTrue(row['cached']);self.assertTrue(row['stale'])
            with patch.dict(os.environ,{'IBKR_ACCOUNT':'U7654321'}):
                with self.assertRaises(RuntimeError):service.load()
            store.close()

    def test_unknown_and_short_positions_are_not_long_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite')
            service=PortfolioService(store,ibkr=lambda account:{'AAPL':{'shares':None},'NVDA':{'shares':-2}},bars=bars)
            result=json.loads(service.report('检查持仓'))
            self.assertEqual([r['decision']['action'] for r in result['positions']],['wait_position','unsupported_short'])
            store.close()

    def test_legacy_image_failure_does_not_fallback(self):
        with patch('premarket_grid_calculator.fetch_positions',return_value={}):
            self.assertEqual(_build_symbols('synthetic.png'),{})
        self.assertEqual(_build_symbols(positions={'AAPL':{'shares':1}})['AAPL']['shares'],1)

    def test_missing_account_fails_before_network(self):
        with self.assertRaises(IBKRUnavailable):fetch_ibkr_positions('')

    def test_real_agent_uses_image_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'stockman.sqlite')
            service=PortfolioService(store,image=lambda path:{'AAPL':{'shares':2,'avg_cost':100}},bars=bars)
            snapshot=service.import_image('synthetic','owner')
            runtime=AgentRuntime(d,OfflineModel(),{'portfolio':service.report})
            result=runtime.run('检查截图持仓',session='owner',snapshot=snapshot)
            self.assertIn('image',result['text']);self.assertIn('AAPL',result['text'])
            runtime.close();store.close()


class IBKRProtocolTest(unittest.TestCase):
    def reader(self, mode):
        from types import SimpleNamespace
        called=[]
        class Wrapper:pass
        class Client:
            def __init__(self, wrapper):pass
            def connect(self,*args):
                self.managedAccounts('U1234567' if mode!='wrong-account' else 'U7654321')
                if mode!='no-handshake':self.nextValidId(1)
            def run(self):pass
            def reqPositionsMulti(self, req, account, model):
                called.append(account)
                contract=SimpleNamespace(symbol='AAPL',currency='USD',secType='STK',conId=123)
                self.positionMulti(req,account,model,contract,0.5,100)
                if mode!='partial':self.positionMultiEnd(req)
            def cancelPositionsMulti(self, req):pass
            def disconnect(self):called.append('disconnected')
        with patch('ibapi.client.EClient',Client),patch('ibapi.wrapper.EWrapper',Wrapper):
            if mode=='ok':
                result=fetch_ibkr_positions('U1234567',timeout=0.1)
                self.assertEqual(result['AAPL']['shares'],0.5)
                self.assertEqual(called,['U1234567','disconnected'])
            else:
                with self.assertRaises(IBKRUnavailable):fetch_ibkr_positions('U1234567',timeout=0.01)
                self.assertEqual(called[-1],'disconnected')
                if mode=='wrong-account':self.assertNotIn('U1234567',called)
    def test_complete_scoped_snapshot(self):self.reader('ok')
    def test_partial_snapshot_rejected(self):self.reader('partial')
    def test_wrong_account_never_requested(self):self.reader('wrong-account')
    def test_handshake_timeout(self):self.reader('no-handshake')
