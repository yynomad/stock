"""Synthetic dependencies for local learning, never live financial evidence."""
from datetime import date,timedelta
import time
from .agents import OfflineModel
from .application import Application
from .portfolio import PortfolioService
from .research import ResearchService


def demo_bars(symbol):
    start=date(2026,8,3)
    return [{'date':(start+timedelta(days=i)).isoformat(),'open':100+i,'close':101+i,
             'high':103+i,'low':99+i,'volume':100} for i in range(60)]


class SyntheticMarket:
    def __init__(self):self.quotes={}
    def info(self,symbol):
        return {'quoteType':'ETF' if symbol=='QQQ' else 'EQUITY','longName':'SYNTHETIC '+symbol,
                'regularMarketPrice':160,'regularMarketTime':time.time(),'fundFamily':'SYNTHETIC'}
    bars=staticmethod(demo_bars)
    def quote(self,symbol):return self.quotes.get(symbol,{'valid':False})


class SyntheticSEC:
    def facts(self,symbol):return {'synthetic':True,'revenue':{'missing':True},'note':'合成财务，不代表真实披露'}


class SyntheticNews:
    def fetch(self,symbol):
        return {'text':'SYNTHETIC NEWS '+symbol+'：离线新闻占位，不是真实检索。',
                'sources':[{'title':'合成示例','url':'https://example.com','published_at':None}],
                'suggestions_html':'<p>合成搜索建议</p>','queried_at':time.time(),'queries':[],'usage':{}}


def create_offline_app(directory,transport=None):
    market=SyntheticMarket()
    def portfolio(store):
        return PortfolioService(store,ibkr=lambda account:{'AAPL':{'shares':0.5,'avg_cost':100}},
                                bars=demo_bars)
    app=Application(directory,OfflineModel(),portfolio_factory=portfolio,
                    research=ResearchService(market,SyntheticSEC(),SyntheticNews()),market=market,transport=transport)
    app.monitor.in_session=lambda now:True
    return app


def end_to_end(directory):
    """Actual LangChain tool loops + SQLite + business services, with all I/O replaced."""
    app=create_offline_app(directory)
    results=[]
    try:
        result=app.handle('检查持仓','demo');results.append(result)
        assert 'ibkr' in result['text'] and 'AAPL' in result['text']
        for symbol in ('AAPL','QQQ'):
            result=app.handle('研究 '+symbol,'demo');results.append(result)
            assert result['news'] and 'SYNTHETIC NEWS' not in result['text']
        results.append(app.handle('开始监控 AAPL','demo'))
        import json
        spec=json.loads(app.runtime.store.execute('SELECT spec FROM rules')[0]['spec'])
        stamp=time.time()
        app.monitor.market.quotes['AAPL']={'price':spec['resistance']*1.05,'asof':stamp,'kind':'regular','valid':True}
        sent=[]
        def send(result):sent.append(result);return 'sent'
        assert len(app.monitor.tick(app.runtime,send,'demo',now=stamp+1))==1
        assert not app.monitor.tick(app.runtime,send,'demo',now=stamp+2)
        task=results[0]['task_id']
        app.runtime.close();app=create_offline_app(directory)
        assert app.runtime.store.task(task)['status']=='completed'
        app.monitor.market.quotes['AAPL']={'price':spec['resistance']*1.05,'asof':stamp+3,'kind':'regular','valid':True}
        assert not app.monitor.tick(app.runtime,send,'demo',now=stamp+4)
        results.append(app.handle('停止监控 AAPL','demo'))
        assert app.runtime.store.execute('SELECT enabled FROM rules')[0]['enabled']==0
        assert len(sent)==1
        return {'synthetic':True,'tasks':len(results)+len(sent),'deliveries':len(sent),
                'sources':['ibkr (synthetic)'],'restart_and_dedup':'passed',
                'database':str(app.directory/'stockman.sqlite')}
    finally:app.runtime.close()
