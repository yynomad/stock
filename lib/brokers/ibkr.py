"""IBKR adapter wraps the existing official ibapi read-only reader."""
import time
from .base import BrokerSnapshot

class IBKRBroker:
    provider='ibkr'
    def __init__(self,config,reader=None):self.config=config;self.account=config.account;self.reader=reader
    def sync(self):
        from ..ibkr import fetch_ibkr_positions
        cfg=self.config
        positions=(self.reader or fetch_ibkr_positions)(self.account,timeout=cfg.timeout,host=cfg.host,port=cfg.port,client_id=cfg.client_id)
        return BrokerSnapshot(self.provider,self.account,time.time(),positions)
