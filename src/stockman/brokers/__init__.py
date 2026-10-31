"""Read-only broker adapters."""
from .base import BrokerSnapshot,BrokerError,BrokerService
from .snaptrade import SnapTradeBroker

def create_broker(config,client=None):
    if config.provider!='snaptrade':raise ValueError('unsupported broker provider')
    return SnapTradeBroker(config,client)
