from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from lib.storage import Store
from lib.brokers import BrokerSnapshot,BrokerError,BrokerService,create_broker
from lib.service_demo import fixture
from lib.portfolio import PortfolioService

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
    def test_image_query_does_not_replace_broker_snapshot(self):
        broker=BrokerService(self.store,create_broker(self.cfg.broker,Mock(return_value={'AAPL':{'shares':1}})))
        portfolio=PortfolioService(self.store,broker=broker,image=lambda path:{'NVDA':{'shares':2}})
        self.assertEqual(set(portfolio.load()['positions']),{'AAPL'})
        image=portfolio.import_image('synthetic','123')
        self.assertEqual(set(portfolio.load('123',image)['positions']),{'NVDA'})
        self.assertEqual(set(portfolio.load('123')['positions']),{'AAPL'})
        with self.assertRaises(ValueError):portfolio.load('other',image)

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
