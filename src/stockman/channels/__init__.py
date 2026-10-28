"""Notification and optional incoming-event adapters."""
from .base import IncomingEvent,Channel

def create_channel(config):
    if config.provider!='telegram':raise ValueError('unsupported notification provider')
    from .telegram import TelegramChannel
    return TelegramChannel(config)
