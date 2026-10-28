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

class ServiceBrokerTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'db');self.cfg=fixture(self.temp.name)
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_explicit_connection_values_and_concurrent_reads_sync_once(self):
        reader=Mock(return_value={'AAPL':{'shares':0.5,'avg_cost':100,'conid':1}})
        service=BrokerService(self.store,create_broker(self.cfg.broker,reader))
        with ThreadPoolExecutor(max_workers=5) as pool:rows=list(pool.map(lambda _:service.load(),range(5)))
        self.assertEqual(reader.call_count,1);self.assertEqual(rows[0]['positions']['AAPL']['shares'],0.5)
        self.assertEqual(reader.call_args.kwargs,{'timeout':12,'host':'127.0.0.1','port':4001,'client_id':17})
    def test_incomplete_wrong_account_and_bad_position_do_not_write(self):
        class Adapter:
            provider='ibkr';account='U1234567'
            def sync(self):return self.snapshot
        for snapshot in [BrokerSnapshot('ibkr','U9999999',time.time(),{}),BrokerSnapshot('ibkr','U1234567',time.time(),{},False),BrokerSnapshot('ibkr','U1234567',time.time(),{'AAPL':{'shares':True}})]:
            adapter=Adapter();adapter.snapshot=snapshot
            with self.assertRaises(BrokerError):BrokerService(self.store,adapter).load()
            self.assertIsNone(self.store.latest('ibkr','U1234567'))
    def test_real_empty_snapshot_and_same_account_cache_are_distinct_from_failure(self):
        reader=Mock(return_value={});service=BrokerService(self.store,create_broker(self.cfg.broker,reader))
        self.assertEqual(service.load()['positions'],{})
        reader.side_effect=TimeoutError('SECRET')
        row=service.load(force=True);self.assertTrue(row['cached']);self.assertEqual(row['positions'],{})
        self.assertNotIn('SECRET',str(service.health()));self.assertEqual(service.health()['status'],'failed')
        other=BrokerService(self.store,create_broker(replace(self.cfg.broker,account='U9999999'),reader))
        with self.assertRaises(BrokerError):other.load()
    def test_legacy_image_records_are_never_used_as_broker_cache(self):
        legacy=self.store.snapshot('image','123',{'NVDA':{'shares':2}})
        reader=Mock(side_effect=TimeoutError('private'))
        broker=BrokerService(self.store,create_broker(self.cfg.broker,reader))
        portfolio=PortfolioService(self.store,broker=broker)
        with self.assertRaises(BrokerError):portfolio.load('123')
        self.assertEqual(set(self.store.get_snapshot(legacy)['positions']),{'NVDA'})
        reader.side_effect=None;reader.return_value={'AAPL':{'shares':1}}
        broker.load(force=True)  # Explicit retry after the failed-sync backoff.
        self.assertEqual(set(portfolio.load('123')['positions']),{'AAPL'})

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
