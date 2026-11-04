import tempfile
import time
import io
import json
import os
import sys
from contextlib import redirect_stdout
from unittest.mock import patch
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from stockman.brokers.snaptrade import SnapTradeBroker
from stockman.brokers.base import BrokerError, BrokerService
from stockman.storage import Store

ACCOUNT='11111111-1111-4111-8111-111111111111'
OTHER='22222222-2222-4222-8222-222222222222'

@dataclass
class Config:
    account:str=ACCOUNT
    client_id:str='synthetic-client'
    consumer_key:str='synthetic-secret'

class FakeClient:
    def __init__(self):
        self.account_information=self;self.connections=self
        self.asof=datetime.now(timezone.utc).isoformat()
        self.accounts=[{'id':ACCOUNT,'kind':'investment','status':'open','connection_id':'connection-1','sync_status':{'holdings':{'initial_sync_completed':True,'holdings_unavailable':False,'last_successful_sync':self.asof}}}]
        self.data={'data_freshness':{'as_of':self.asof},'results':[{'instrument':{'kind':'stock','symbol':'AAPL','currency':'USD'},'currency':'USD','units':'1.5','cost_basis':'100'}]}
        self.calls=[]
    def list_all_user_accounts(self):return SimpleNamespace(body={'results':self.accounts})
    def detail_brokerage_authorization(self,authorization_id):
        return SimpleNamespace(body={'id':authorization_id,'disabled':False,'type':'read'})
    def get_all_account_positions(self,account_id):
        self.calls.append(account_id);return SimpleNamespace(body=self.data)

class SnapTradeTest(unittest.TestCase):
    def test_position_rejections_have_specific_safe_codes(self):
        from stockman.brokers.snaptrade import _positions
        edits=[
            ('asset_unsupported',lambda r:r['instrument'].update(kind='option')),
            ('currency_unsupported',lambda r:r.update(currency='HKD')),
            ('currency_unsupported',lambda r:(r.pop('currency'),r['instrument'].pop('currency'))),
            ('instrument_missing',lambda r:r.pop('instrument')),
            ('symbol_missing',lambda r:r['instrument'].pop('symbol')),
            ('units_missing',lambda r:r.update(units=None)),
            ('units_invalid',lambda r:r.update(units='NaN')),
            ('cost_invalid',lambda r:r.update(cost_basis='private-value-must-not-appear')),
        ]
        for code,edit in edits:
            fake=FakeClient();edit(fake.data['results'][0])
            with self.subTest(code=code),self.assertRaises(BrokerError) as caught:
                SnapTradeBroker(Config(),fake).sync()
            self.assertEqual(caught.exception.code,code)
            self.assertNotIn('private-value',str(caught.exception))
        for data,code in (({},'positions_shape_invalid'),([None],'position_shape_invalid')):
            with self.subTest(code=code),self.assertRaises(BrokerError) as caught:_positions(data)
            self.assertEqual(caught.exception.code,code)
        fake=FakeClient();fake.data['results']*=2
        with self.assertRaises(BrokerError) as caught:SnapTradeBroker(Config(),fake).sync()
        self.assertEqual(caught.exception.code,'symbol_duplicate')

    def test_response_shape_rejections_have_specific_safe_codes(self):
        cases=[('accounts_shape_invalid',lambda f:setattr(f,'list_all_user_accounts',lambda:SimpleNamespace(body=[]))),
               ('sync_shape_invalid',lambda f:f.accounts[0].update(sync_status='invalid')),
               ('positions_response_invalid',lambda f:setattr(f,'data',[])),
               ('freshness_shape_invalid',lambda f:f.data.update(data_freshness='invalid'))]
        for code,edit in cases:
            fake=FakeClient();edit(fake)
            with self.subTest(code=code),self.assertRaises(BrokerError) as caught:SnapTradeBroker(Config(),fake).sync()
            self.assertEqual(caught.exception.code,code)

    def test_sdk_contract_and_only_target_account(self):
        fake=FakeClient();fake.accounts.append({'id':OTHER})
        snap=SnapTradeBroker(Config(),fake).sync()
        self.assertEqual(fake.calls,[ACCOUNT])
        self.assertEqual(snap.positions['AAPL']['shares'],1.5)
        self.assertEqual(snap.positions['AAPL']['avg_cost'],100)
        self.assertAlmostEqual(snap.asof,datetime.fromisoformat(fake.asof).timestamp())
    def test_real_sdk_decoded_response_uses_immutable_mappings_and_sequences(self):
        from snaptrade_client.model.all_account_positions_response import AllAccountPositionsResponse
        fake=FakeClient()
        fake.data=AllAccountPositionsResponse(results=[],data_freshness={'as_of':fake.asof})
        self.assertEqual(SnapTradeBroker(Config(),fake).sync().positions,{})
    def test_sdk_boolean_fields_are_interpreted_without_identity_assumptions(self):
        from snaptrade_client.schemas import BoolSchema
        fake=FakeClient()
        fake.detail_brokerage_authorization=lambda authorization_id:SimpleNamespace(body={'id':authorization_id,'disabled':BoolSchema(False),'type':'read'})
        fake.accounts[0]['sync_status']['holdings']['initial_sync_completed']=BoolSchema(True)
        fake.accounts[0]['sync_status']['holdings']['holdings_unavailable']=BoolSchema(False)
        self.assertIn('AAPL',SnapTradeBroker(Config(),fake).sync().positions)
        fake.detail_brokerage_authorization=lambda authorization_id:SimpleNamespace(body={'id':authorization_id,'disabled':BoolSchema(True),'type':'read'})
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_v2_sdk_account_response_and_position_sync_time(self):
        from snaptrade_client.model.all_user_accounts_response import AllUserAccountsResponse
        fake=FakeClient()
        row={'kind':'investment','id':ACCOUNT,'connection_id':OTHER,'status':'open','is_paper':False,'display_name':'Synthetic','masked_account_number':'****1234','sync_status':{'positions':fake.asof}}
        decoded=AllUserAccountsResponse(results=[row])
        fake.list_all_user_accounts=lambda:SimpleNamespace(body=decoded)
        fake.data['data_freshness']={}
        self.assertEqual(SnapTradeBroker(Config(),fake).sync().positions['AAPL']['shares'],1.5)
        fake.data['results']=[]
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_real_sdk_decoded_position_maps_fractional_units(self):
        from snaptrade_client.model.all_account_positions_response import AllAccountPositionsResponse
        fake=FakeClient()
        row={'instrument':{'kind':'stock','id':ACCOUNT,'symbol':'AAPL','raw_symbol':'AAPL','description':'Apple','currency':'USD','exchange':'XNAS','figi_instrument':None},'units':'1.5','price':'120','cost_basis':'100','currency':'USD','cash_equivalent':False}
        fake.data=AllAccountPositionsResponse(results=[row],data_freshness={'as_of':fake.asof})
        self.assertEqual(SnapTradeBroker(Config(),fake).sync().positions['AAPL']['shares'],1.5)
    def test_wrong_account_initial_sync_unavailable_and_unknown_time_fail(self):
        for mutate in (
            lambda f:setattr(f,'accounts',[{'id':OTHER}]),
            lambda f:f.accounts[0]['sync_status']['holdings'].update(initial_sync_completed=False),
            lambda f:f.accounts[0]['sync_status']['holdings'].update(holdings_unavailable=True),
            lambda f:(f.data.update(data_freshness={}),f.accounts[0]['sync_status']['holdings'].update(last_successful_sync=None)),
        ):
            fake=FakeClient();mutate(fake)
            with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_empty_only_after_explicit_completed_sync(self):
        fake=FakeClient();fake.data['results']=[]
        self.assertEqual(SnapTradeBroker(Config(),fake).sync().positions,{})
        fake.accounts[0]['sync_status']['holdings'].pop('initial_sync_completed')
        with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_rejects_incomplete_mixed_unsupported_or_duplicate_positions(self):
        for edit in (
            lambda f:f.data['results'][0]['instrument'].update(kind='option'),
            lambda f:f.data['results'][0].update(currency='JPY'),
            lambda f:f.data['results'][0].update(units=None),
            lambda f:f.data['results'].append(f.data['results'][0]),
            lambda f:f.data['results'][0].update(units='NaN'),
        ):
            fake=FakeClient();edit(fake)
            with self.assertRaises(BrokerError):SnapTradeBroker(Config(),fake).sync()
    def test_failures_use_only_same_source_account_cache(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db')
            try:
                fake=FakeClient();service=BrokerService(store,SnapTradeBroker(Config(),fake))
                first=service.load(force=True);self.assertFalse(first['cached'])
                fake.accounts=[]
                fallback=service.load(force=True);self.assertTrue(fallback['cached'])
                other=BrokerService(store,SnapTradeBroker(Config(account=OTHER),fake))
                with self.assertRaises(BrokerError):other.load(force=True)
            finally:store.close()
    def test_disabled_connection_reports_a_safe_diagnostic_code(self):
        fake=FakeClient()
        fake.detail_brokerage_authorization=lambda authorization_id:SimpleNamespace(body={'id':authorization_id,'disabled':True,'type':'read'})
        with self.assertRaises(BrokerError) as caught:SnapTradeBroker(Config(),fake).sync()
        self.assertEqual(caught.exception.code,'connection_unavailable')

    def test_error_does_not_expose_credentials(self):
        fake=FakeClient()
        def error():raise RuntimeError('synthetic-secret')
        fake.list_all_user_accounts=error
        with self.assertRaises(BrokerError) as caught:SnapTradeBroker(Config(),fake).sync()
        self.assertNotIn('synthetic-secret',str(caught.exception))


class SnapTradeSmokeTest(unittest.TestCase):
    def test_smoke_reports_currency_failure_without_position_details(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('snap_smoke_currency',Path(__file__).resolve().parents[1]/'scripts'/'smoke.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fake=FakeClient();fake.data['results'][0].update(currency='HKD')
        env={'SNAPTRADE_CLIENT_ID':'private-client','SNAPTRADE_CONSUMER_KEY':'private-secret','SNAPTRADE_ACCOUNT_ID':ACCOUNT}
        with patch.dict(os.environ,env),patch('snaptrade_client.SnapTrade',return_value=fake),patch.object(sys,'argv',['smoke.py','--service','snaptrade','--read-only']),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(),2)
        result=json.loads(output.getvalue())
        self.assertEqual(result['failure_code'],'currency_unsupported')
        for private in ('private-secret',ACCOUNT,'AAPL','HKD'):
            self.assertNotIn(private,output.getvalue())

    def test_local_account_discovery_uses_v2_and_masks_broker_number(self):
        import importlib.util
        spec=importlib.util.spec_from_file_location('snap_smoke',Path(__file__).resolve().parents[1]/'scripts'/'smoke.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        fake=FakeClient()
        fake.accounts[0]['masked_account_number']='****1234'
        fake.accounts[0]['number']='U98765432'
        env={'SNAPTRADE_CLIENT_ID':'private-client','SNAPTRADE_CONSUMER_KEY':'private-secret','SNAPTRADE_ACCOUNT_ID':''}
        with patch.dict(os.environ,env),patch('snaptrade_client.SnapTrade',return_value=fake),patch.object(sys,'argv',['smoke.py','--service','snaptrade','--read-only','--list-accounts']),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(module.main(),0)
        result=json.loads(output.getvalue())
        self.assertEqual(result['accounts'][0]['account_id'],ACCOUNT)
        self.assertEqual(result['accounts'][0]['account_suffix'],'1234')
        self.assertNotIn('private-secret',output.getvalue())
        self.assertNotIn('U98765432',output.getvalue())
