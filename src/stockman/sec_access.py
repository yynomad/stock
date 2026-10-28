"""One SEC pacing gate shared by on-demand research and daily collectors."""
import threading
import time

class RateGate:
    def __init__(self,interval=0.16):self.interval=interval;self.lock=threading.Lock();self.last=None
    def wait(self,sleep=None,clock=None):
        sleep=sleep or time.sleep;clock=clock or time.monotonic
        with self.lock:
            delay=0 if self.last is None else max(0,self.interval-(clock()-self.last))
            if delay:sleep(delay)
            self.last=clock()

SEC_GATE=RateGate()
