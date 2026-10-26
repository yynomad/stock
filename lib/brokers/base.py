"""Single-account snapshot contract and serialized, durable synchronization."""
from dataclasses import dataclass,field
import math
import threading
import time
from typing import Protocol

class BrokerError(RuntimeError):pass

@dataclass(frozen=True)
class BrokerSnapshot:
    provider:str
    account:str=field(repr=False)
    asof:float=0
    positions:dict=field(default_factory=dict)
    complete:bool=True
    def validate(self,provider,account,now):
        if self.provider!=provider or self.account!=account or self.complete is not True:raise BrokerError('incomplete or wrong-scope broker snapshot')
        if isinstance(self.asof,bool) or not isinstance(self.asof,(int,float)) or not math.isfinite(self.asof) or not 0<self.asof<=now+30:raise BrokerError('invalid snapshot timestamp')
        from ..portfolio import normalize_positions
        return normalize_positions(self.positions)

class BrokerAdapter(Protocol):
    provider:str
    account:str
    def sync(self)->BrokerSnapshot:...

class BrokerService:
    def __init__(self,store,adapter,interval=900):
        self.store=store;self.adapter=adapter;self.interval=interval;self.lock=threading.RLock();self.last_attempt=None
        store.execute('CREATE TABLE IF NOT EXISTS broker_health(provider TEXT,scope TEXT,status TEXT,last_attempt REAL,last_success REAL,error TEXT,PRIMARY KEY(provider,scope))')
    def _row(self,row,now,cached,error=None):
        row=dict(row);row.update(cached=cached,age_seconds=max(0,now-row['asof']),stale=now-row['asof']>86400,provider=self.adapter.provider)
        if error:row['sync_error']=error
        return row
    def load(self,force=False,now=None):
        now=time.time() if now is None else now
        with self.lock:
            adapter=self.adapter;cached=self.store.latest(adapter.provider,adapter.account)
            if cached and not force and 0<=now-cached['asof']<self.interval:return self._row(cached,now,True)
            if not force and self.last_attempt is not None and 0<=now-self.last_attempt<min(60,self.interval):
                if cached:return self._row(cached,now,True,'recent_sync_failure')
                raise BrokerError('broker unavailable; no snapshot for configured account')
            self.last_attempt=now
            try:
                snapshot=adapter.sync();positions=snapshot.validate(adapter.provider,adapter.account,now)
                self.store.snapshot(adapter.provider,adapter.account,positions,asof=snapshot.asof)
                self.store.execute('INSERT OR REPLACE INTO broker_health VALUES(?,?,?,?,?,?)',(adapter.provider,adapter.account,'ok',now,now,None))
                return self._row(self.store.latest(adapter.provider,adapter.account),now,False)
            except Exception as exc:
                previous=self.health()
                self.store.execute('INSERT OR REPLACE INTO broker_health VALUES(?,?,?,?,?,?)',(adapter.provider,adapter.account,'failed',now,previous.get('last_success'),type(exc).__name__))
                if cached:return self._row(cached,now,True,type(exc).__name__)
                raise BrokerError('broker unavailable; no snapshot for configured account') from None
    def health(self):
        rows=self.store.execute('SELECT status,last_attempt,last_success,error FROM broker_health WHERE provider=? AND scope=?',(self.adapter.provider,self.adapter.account))
        return rows[0] if rows else {'status':'not_synced'}
