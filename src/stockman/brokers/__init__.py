"""Read-only broker adapters. Registry contains implemented providers only."""
from .base import BrokerSnapshot,BrokerError,BrokerService
from .ibkr import IBKRBroker

def create_broker(config,reader=None):
    if config.provider!='ibkr':raise ValueError('unsupported broker provider')
    return IBKRBroker(config,reader)
