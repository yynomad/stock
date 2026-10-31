from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from stockman.storage import Store
from stockman.brokers import BrokerSnapshot,BrokerError,BrokerService,create_broker
from stockman.service_demo import fixture
from stockman.portfolio import PortfolioService
from test_snaptrade import FakeClient, OTHER

class ServiceBrokerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db');self.cfg=fixture(self.temp.name)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_explicit_connection_values_and_concurrent_reads_sync_once(self):
        client=FakeClient()
        client.get_all_account_positions=Mock(wraps=client.get_all_account_positions)
        service=BrokerService(self.store,create_broker(self.cfg.broker,client))
        with ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(lambda _:service.load(),range(5)))
        self.assertEqual(client.get_all_account_positions.call_count,1)
        self.assertEqual(rows[0]['positions']['AAPL']['shares'],1.5)
        self.assertEqual(client.get_all_account_positions.call_args.kwargs,{'account_id':self.cfg.broker.account})
    def test_incomplete_wrong_account_and_bad_position_do_not_write(self):
        class Adapter:
            provider='ibkr';account='U1234567'
            def sync(self):return self.snapshot
        for snapshot in [BrokerSnapshot('ibkr','U9999999',time.time(),{}),BrokerSnapshot('ibkr','U1234567',time.time(),{},False),BrokerSnapshot('ibkr','U1234567',time.time(),{'AAPL':{'shares':True}})]:
            adapter=Adapter();adapter.snapshot=snapshot
            with self.assertRaises(BrokerError):BrokerService(self.store,adapter).load()
            self.assertIsNone(self.store.latest('ibkr','U1234567'))
    def test_real_empty_snapshot_and_same_account_cache_are_distinct_from_failure(self):
        client=FakeClient();client.data['results']=[]
        service=BrokerService(self.store,create_broker(self.cfg.broker,client))
        self.assertEqual(service.load()['positions'],{})
        client.list_all_user_accounts=Mock(side_effect=TimeoutError('SECRET'))
        row=service.load(force=True);self.assertTrue(row['cached']);self.assertEqual(row['positions'],{})
        self.assertNotIn('SECRET',str(service.health()));self.assertEqual(service.health()['status'],'failed')
        other=BrokerService(self.store,create_broker(replace(self.cfg.broker,account=OTHER),client))
        with self.assertRaises(BrokerError):other.load()
    def test_legacy_image_records_are_never_used_as_broker_cache(self):
        legacy=self.store.snapshot('image','123',{'NVDA':{'shares':2}})
        client=FakeClient();original=client.list_all_user_accounts
        client.list_all_user_accounts=Mock(side_effect=TimeoutError('private'))
        broker=BrokerService(self.store,create_broker(self.cfg.broker,client))
        portfolio=PortfolioService(self.store,broker=broker)
        with self.assertRaises(BrokerError):portfolio.load('123')
        self.assertEqual(set(self.store.get_snapshot(legacy)['positions']),{'NVDA'})
        client.list_all_user_accounts=original
        broker.load(force=True)
        self.assertEqual(set(portfolio.load('123')['positions']),{'AAPL'})

    def test_old_source_timestamp_does_not_cause_repeated_requests_or_fake_errors(self):
        class Adapter:
            provider='snaptrade';account='target';calls=0
            def sync(self):
                self.calls+=1
                return BrokerSnapshot(self.provider,self.account,100,{'AAPL':{'shares':1}})
        adapter=Adapter();service=BrokerService(self.store,adapter,interval=900)
        first=service.load(force=True,now=100000)
        second=service.load(now=100001)
        self.assertEqual(adapter.calls,1)
        self.assertTrue(first['stale']);self.assertTrue(second['stale'])
        self.assertNotIn('sync_error',second)
        self.assertEqual(second['asof'],100)
    def test_changed_data_with_same_source_timestamp_is_not_saved(self):
        class Adapter:
            provider='snaptrade';account='target'
            def sync(self):return self.snapshot
        adapter=Adapter();adapter.snapshot=BrokerSnapshot('snaptrade','target',100,{'AAPL':{'shares':1}})
        service=BrokerService(self.store,adapter);service.load(now=100)
        adapter.snapshot=BrokerSnapshot('snaptrade','target',100,{'AAPL':{'shares':2}})
        row=service.load(force=True,now=101)
        self.assertTrue(row['cached']);self.assertEqual(row['positions']['AAPL']['shares'],1)
    def test_out_of_order_and_future_cache_never_replace_valid_snapshot(self):
        class Adapter:
            provider='ibkr';account='U1234567'
            def sync(self):return self.snapshot
        adapter=Adapter();adapter.snapshot=BrokerSnapshot('ibkr',adapter.account,200,{'AAPL':{'shares':1}})
        service=BrokerService(self.store,adapter);service.load(now=200)
        adapter.snapshot=BrokerSnapshot('ibkr',adapter.account,100,{'AAPL':{'shares':9}})
        row=service.load(force=True,now=300)
        self.assertTrue(row['cached']);self.assertEqual(row['positions']['AAPL']['shares'],1)
        adapter.snapshot=BrokerSnapshot('ibkr',adapter.account,500,{'AAPL':{'shares':9}})
        with self.assertRaises(BrokerError):service.load(force=True,now=100)
