import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from stockman.agents import AgentRuntime,OfflineModel
from stockman.storage import Store
from stockman.portfolio import PortfolioService,normalize_positions
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

    def test_broker_load_ignores_and_preserves_legacy_image_records(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite')
            legacy=store.snapshot('image','chat',{'NVDA':{'shares':2,'avg_cost':90}})
            service=PortfolioService(store,reader=lambda account:{'AAPL':{'shares':0.5,'avg_cost':100}},bars=bars)
            try:
                with patch.dict(os.environ,{'SNAPTRADE_ACCOUNT_ID':'U1234567'}):
                    row=service.load('chat')
                self.assertEqual(row['source'],'snaptrade');self.assertEqual(set(row['positions']),{'AAPL'})
                self.assertEqual(set(store.get_snapshot(legacy)['positions']),{'NVDA'})
            finally:store.close()

    def test_disconnect_reports_cache_and_never_another_account(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite');store.snapshot('snaptrade','U1234567',{},asof=100)
            def broken(account):raise TimeoutError()
            service=PortfolioService(store,reader=broken,bars=bars)
            with patch.dict(os.environ,{'SNAPTRADE_ACCOUNT_ID':'U1234567'}):
                row=service.load();self.assertTrue(row['cached']);self.assertTrue(row['stale'])
            with patch.dict(os.environ,{'SNAPTRADE_ACCOUNT_ID':'U7654321'}):
                with self.assertRaises(RuntimeError):service.load()
            store.close()

    def test_unknown_and_short_positions_are_not_long_decisions(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'data.sqlite')
            service=PortfolioService(store,reader=lambda account:{'AAPL':{'shares':None},'NVDA':{'shares':-2}},bars=bars)
            result=json.loads(service.report('检查持仓'))
            self.assertEqual([r['decision']['action'] for r in result['positions']],['wait_position','unsupported_short'])
            store.close()

    def test_explicit_empty_positions_do_not_fallback_to_watchlist(self):
        with patch('premarket_grid_calculator.WatchlistManager') as watchlist:
            watchlist.return_value.get_all.return_value={'NVDA':{'shares':99}}
            self.assertEqual(_build_symbols(positions={}),{})
            watchlist.return_value.get_all.assert_not_called()
        self.assertEqual(_build_symbols(positions={'AAPL':{'shares':1}})['AAPL']['shares'],1)

    def test_missing_account_fails_before_network(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ,{'SNAPTRADE_ACCOUNT_ID':'','SNAPTRADE_CLIENT_ID':'','SNAPTRADE_CONSUMER_KEY':''}):
            store=Store(Path(d)/'db')
            try:
                with self.assertRaises(RuntimeError):PortfolioService(store).load()
            finally:store.close()

    def test_real_agent_uses_broker_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'stockman.sqlite')
            service=PortfolioService(store,reader=lambda account:{'AAPL':{'shares':2,'avg_cost':100}},bars=bars)
            runtime=AgentRuntime(d,OfflineModel(),{'portfolio':service.report})
            try:
                result=runtime.run('检查持仓',session='owner')
                self.assertIn('snaptrade',result['text']);self.assertIn('AAPL',result['text'])
            finally:runtime.close();store.close()


class SnapTradeProtocolBoundaryTest(unittest.TestCase):
    def test_snapshot_rejects_future_timestamp(self):
        from stockman.brokers.base import BrokerSnapshot,BrokerError
        import time
        with self.assertRaises(BrokerError):BrokerSnapshot('snaptrade','target',time.time()+100,{}).validate('snaptrade','target',time.time())
    def test_sdk_can_construct_without_network(self):
        from snaptrade_client import SnapTrade,SnapTradeAuth
        with patch('socket.socket.connect',side_effect=AssertionError('network')):
            client=SnapTrade(auth=SnapTradeAuth.personal_api_key(client_id='synthetic',consumer_key='synthetic'))
        self.assertTrue(callable(client.account_information.get_all_account_positions))
    def test_wrong_account_never_requests_positions(self):
        from test_snaptrade import FakeClient,Config
        from stockman.brokers.snaptrade import SnapTradeBroker
        from stockman.brokers import BrokerError
        fake=FakeClient();fake.accounts=[]
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
        self.assertEqual(fake.calls,[])
    def test_empty_initial_sync_is_not_treated_as_empty_account(self):
        from test_snaptrade import FakeClient,Config
        from stockman.brokers.snaptrade import SnapTradeBroker
        from stockman.brokers import BrokerError
        fake=FakeClient();fake.data['results']=[];fake.accounts[0]['sync_status']['holdings']['initial_sync_completed']=False
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_network_error_is_sanitized(self):
        from test_snaptrade import FakeClient,Config
        from stockman.brokers.snaptrade import SnapTradeBroker
        from stockman.brokers import BrokerError
        fake=FakeClient()
        def fail():raise TimeoutError('secret')
        fake.list_all_user_accounts=fail
        with self.assertRaises(BrokerError) as caught:SnapTradeBroker(Config(),fake).sync()
        self.assertNotIn('secret',str(caught.exception))
    def test_malformed_positions_are_rejected(self):
        from test_snaptrade import FakeClient,Config
        from stockman.brokers.snaptrade import SnapTradeBroker
        from stockman.brokers import BrokerError
        fake=FakeClient();fake.data={'results':None}
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
