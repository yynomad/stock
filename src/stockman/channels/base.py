"""Domain-neutral channel contracts; sending does not imply receiving capabilities."""
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class IncomingEvent:
    channel:str
    identity:int
    sender:int | None
    target:int | None
    text:str=''

class Channel(Protocol):
    provider:str
    capabilities:frozenset
    def deliver(self,report:dict)->str:...
