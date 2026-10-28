"""Telegram translates platform updates to standard events; retains proven transport."""
from .base import IncomingEvent
from ..telegram import Telegram

def decode(update):
    identity=update.get('update_id')
    if type(identity) is not int or identity<0:raise ValueError('invalid incoming identity')
    message=update.get('message',{})
    if not isinstance(message,dict):message={}
    text=message.get('text','')
    user=message.get('from',{});chat=message.get('chat',{})
    sender=user.get('id') if isinstance(user,dict) else None
    target=chat.get('id') if isinstance(chat,dict) else None
    return IncomingEvent('telegram',identity,sender if type(sender) is int else None,target if type(target) is int else None,
        text.strip() if isinstance(text,str) else '')

class TelegramChannel(Telegram):
    provider='telegram'
    capabilities=frozenset({'send','text'})
    def __init__(self,config):
        super().__init__(token=config.token,chat=config.chat,proxy='')
        self.interactive=config.interactive
    def receive(self,offset):
        for update in self.updates(offset):yield decode(update)
