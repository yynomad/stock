"""SnapTrade Personal read-only account adapter."""
from datetime import datetime
from collections.abc import Mapping
import math
from .base import BrokerError, BrokerSnapshot
from snaptrade_client.schemas import BoolSchema

def _flag(value):
    return bool(value) if isinstance(value,(bool,BoolSchema)) else None


def _timestamp(value):
    if not isinstance(value,str) or not value:
        raise BrokerError('SnapTrade position freshness unavailable',code='source_time_unknown')
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError()
        return stamp.timestamp()
    except (ValueError,OverflowError):
        raise BrokerError('SnapTrade position freshness invalid',code='source_time_invalid') from None


def _positions(data):
    if not isinstance(data,(list,tuple)):raise BrokerError('invalid SnapTrade positions')
    result={}
    for row in data:
        if not isinstance(row,Mapping):raise BrokerError('invalid position')
        instrument=row.get('instrument')
        if not isinstance(instrument,Mapping):raise BrokerError('missing instrument')
        if instrument.get('kind') not in ('stock','etf','adr'):
            raise BrokerError('unsupported asset; snapshot incomplete')
        symbol=instrument.get('symbol')
        if not isinstance(symbol,str) or not symbol:raise BrokerError('missing symbol')
        if symbol in result:raise BrokerError('duplicate symbol; snapshot incomplete')
        currency=row.get('currency') or instrument.get('currency')
        if currency!='USD':raise BrokerError('unsupported or unknown currency')
        units=row.get('units')
        if units is None or isinstance(units,bool):raise BrokerError('missing position units')
        try: shares=float(units)
        except (ValueError,TypeError,OverflowError):raise BrokerError('invalid units') from None
        if not math.isfinite(shares):raise BrokerError('invalid units')
        cost=row.get('cost_basis')
        if cost is not None:
            if isinstance(cost,bool):raise BrokerError('invalid cost')
            try:cost=float(cost)
            except (ValueError,TypeError,OverflowError):raise BrokerError('invalid cost') from None
            if not math.isfinite(cost) or cost<0:raise BrokerError('invalid cost')
            if cost==0:cost=None
        result[symbol]={'shares':shares,'avg_cost':cost,'currency':currency,'asset_type':'STK'}
    return result


class SnapTradeBroker:
    provider='snaptrade'
    def __init__(self,config,client=None):
        self.config=config
        self.account=config.account
        self.client=client

    def _client(self):
        if self.client is None:
            from snaptrade_client import SnapTrade, SnapTradeAuth
            self.client=SnapTrade(auth=SnapTradeAuth.personal_api_key(
                client_id=self.config.client_id,consumer_key=self.config.consumer_key))
        return self.client

    def sync(self):
        try:
            client=self._client()
            payload=client.account_information.list_all_user_accounts().body
            if not isinstance(payload,Mapping) or not isinstance(payload.get('results'),(list,tuple)):
                raise BrokerError('invalid account list')
            accounts=payload['results']
            selected=[a for a in accounts if isinstance(a,Mapping) and str(a.get('id'))==self.account]
            if len(selected)!=1:raise BrokerError('configured account unavailable',code='account_unavailable')
            account=selected[0]
            if account.get('kind')!='investment' or account.get('status') not in (None,'open'):
                raise BrokerError('account not an open investment account',code='account_invalid')
            authorization=account.get('connection_id')
            if not authorization:raise BrokerError('account connection unavailable',code='connection_unknown')
            connection=client.connections.detail_brokerage_authorization(authorization_id=str(authorization)).body
            if not isinstance(connection,Mapping) or str(connection.get('id'))!=str(authorization) or _flag(connection.get('disabled')) is not False:
                raise BrokerError('connection disabled or status unknown',code='connection_unavailable')
            if connection.get('type')!='read':
                raise BrokerError('connection is not read only',code='connection_not_read_only')
            sync=account.get('sync_status') or {}
            if not isinstance(sync,Mapping):raise BrokerError('invalid sync status')
            holdings=sync.get('holdings') or {}
            if not isinstance(holdings,Mapping):raise BrokerError('invalid sync status')
            if _flag(holdings.get('holdings_unavailable')) is True or _flag(holdings.get('initial_sync_completed')) is False:
                raise BrokerError('holdings unavailable or initial sync incomplete',code='holdings_unavailable')
            data=client.account_information.get_all_account_positions(account_id=self.account).body
            if not isinstance(data,Mapping) or not isinstance(data.get('results'),(list,tuple)):
                raise BrokerError('invalid position response')
            # V2 account metadata does not promise that an empty result means a
            # complete account. Do not invent an empty account without proof.
            if not data['results'] and _flag(holdings.get('initial_sync_completed')) is not True:
                raise BrokerError('empty positions without explicit completed initial sync',code='empty_unverified')
            freshness=data.get('data_freshness') or {}
            if not isinstance(freshness,Mapping):raise BrokerError('invalid freshness')
            asof=_timestamp(freshness.get('as_of') or sync.get('positions') or holdings.get('last_successful_sync'))
            return BrokerSnapshot(self.provider,self.account,asof,_positions(data['results']))
        except BrokerError:raise
        except Exception as exc:raise BrokerError('SnapTrade request failed: '+type(exc).__name__,code='request_failed') from None
