"""Read-only, account-scoped IBKR position adapter. No order methods are called."""
import os
import re
import threading


class IBKRUnavailable(RuntimeError):pass


def fetch_ibkr_positions(account=None, timeout=12):
    account=os.getenv('IBKR_ACCOUNT','') if account is None else account
    if not re.fullmatch(r'U[0-9]{5,}',account):
        raise IBKRUnavailable('请配置目标联名账户 IBKR_ACCOUNT')
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper

    class Reader(EWrapper,EClient):
        def __init__(self):
            EWrapper.__init__(self);EClient.__init__(self,self)
            self.ready=threading.Event();self.done=threading.Event()
            self.accounts=[];self.positions={};self.failure=None
        def nextValidId(self, orderId):
            # Handshake callback only; the order id is never used.
            self.ready.set()
        def managedAccounts(self, accountsList):self.accounts=accountsList.split(',')
        def positionMulti(self, reqId, accountName, modelCode, contract, pos, avgCost):
            if reqId!=7101 or accountName!=account:return
            symbol=contract.symbol
            if symbol in self.positions and self.positions[symbol]['conid']!=contract.conId:
                self.failure='Multiple distinct contracts share one symbol; incomplete snapshot rejected'
                return
            self.positions[symbol]={'shares':float(pos),'avg_cost':float(avgCost) if avgCost>0 else None,
                                    'currency':contract.currency,'asset_type':contract.secType,
                                    'conid':contract.conId}
        def positionMultiEnd(self, reqId):
            if reqId==7101:self.done.set()
        def error(self, reqId, errorCode, errorString, *extra):
            if reqId==7101 or errorCode in (502,503,504,1100,1300,321,322):
                self.failure=f'IBKR connection/request error {errorCode}'
                self.ready.set();self.done.set()

    reader=Reader();thread=None
    try:
        reader.connect(os.getenv('IBKR_HOST','127.0.0.1'),int(os.getenv('IBKR_PORT','4001')),int(os.getenv('IBKR_CLIENT_ID','17')))
        thread=threading.Thread(target=reader.run,daemon=True,name='IBKR-read-only')
        thread.start()
        if not reader.ready.wait(timeout) or reader.failure:raise IBKRUnavailable(reader.failure or 'Gateway 登录或连接超时')
        if account not in reader.accounts:raise IBKRUnavailable('目标联名账户不在此用户名可访问账户中')
        reader.reqPositionsMulti(7101,account,'')
        if not reader.done.wait(timeout) or reader.failure:raise IBKRUnavailable(reader.failure or '持仓快照未完整返回')
        reader.cancelPositionsMulti(7101)
        return reader.positions
    except IBKRUnavailable:raise
    except Exception as exc:raise IBKRUnavailable('Gateway 查询失败：'+type(exc).__name__) from None
    finally:
        reader.disconnect()
        if thread:thread.join(timeout=2)
