"""Explicit fixture config and later service demonstrations; never a real account."""
from pathlib import Path
import tempfile
from .service_config import load_config

ENV={'IBKR_ACCOUNT':'U1234567','GEMINI_API_KEY':'synthetic-model-key','TG_BOT_TOKEN':'synthetic-bot-token','TG_CHAT_ID':'123'}

def fixture(directory):
    root=Path(__file__).resolve().parents[2]
    path=Path(directory)/'stock.conf'
    text=(root/'stock.conf.example').read_text().replace('file = briefing.sources.json','file = '+str(root/'briefing.sources.json'))
    path.write_text(text)
    return load_config(path,ENV)

def run(scenario):
    if scenario=='broker':return broker_demo()
    if scenario=='channel':return channel_demo()
    if scenario in ('service','end-to-end'):return service_demo(scenario)
    if scenario!='config':raise ValueError('unsupported demo scenario')
    with tempfile.TemporaryDirectory() as d:
        config=fixture(d)
        assert not config.directory.exists()
        return {'synthetic':True,'scenario':scenario,'broker':config.broker.provider,'channel':config.channel.provider,'check_without_runtime_write':'passed'}

def broker_demo():
    from .storage import Store
    from .brokers import create_broker,BrokerService
    with tempfile.TemporaryDirectory() as d:
        config=fixture(d);store=Store(Path(d)/'db')
        try:
            calls=[]
            def read(*args,**kwargs):calls.append(1);return {'AAPL':{'shares':0.5,'avg_cost':100}}
            broker=BrokerService(store,create_broker(config.broker,read))
            first=broker.load();assert broker.load()['cached'] and len(calls)==1
            return {'synthetic':True,'scenario':'broker','source':first['source'],'complete_single_account':'passed','native_calls':len(calls)}
        finally:store.close()

def channel_demo():
    from .channels import create_channel
    from .channels.telegram import decode
    with tempfile.TemporaryDirectory() as d:
        channel=create_channel(fixture(d).channel)
        text=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'text':'研究 AAPL'}})
        unsupported=decode({'update_id':2,'message':{'photo':[{'file_id':'synthetic'}],'caption':'检查持仓'}})
        assert text.text=='研究 AAPL' and not unsupported.text and channel.chat==123
        return {'synthetic':True,'scenario':'channel','normalized_text_and_unsupported_media':'passed','real_messages_sent':0}

class FakeBroker:
    provider='ibkr'
    def __init__(self,account):self.account=account;self.calls=0;self.fail=False
    def sync(self):
        import time
        from .brokers import BrokerSnapshot
        self.calls+=1
        if self.fail:raise TimeoutError('synthetic-secret-must-not-be-recorded')
        return BrokerSnapshot('ibkr',self.account,time.time(),{'AAPL':{'shares':0.5,'avg_cost':100}})

class FakeChannel:
    provider='telegram';capabilities=frozenset({'send','text'})
    def __init__(self):self.chat=123;self.sent=[];self.outcome='sent';self.events=[];self.fail_receive=False;self.after_send=None
    def deliver(self,result):
        self.sent.append(result)
        if self.after_send:self.after_send()
        return self.outcome
    def receive(self,offset):
        if self.fail_receive:raise TimeoutError('synthetic-private-error')
        return iter(e for e in self.events if e.identity>=offset)

def build_synthetic(config,channel=None):
    import json
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration,ChatResult
    from dataclasses import replace
    from .agents import OfflineModel
    from .offline import SyntheticMarket,SyntheticSEC,SyntheticNews
    from .research import ResearchService
    from .briefing.demo import SyntheticSource
    from .service import BackendService
    class BriefModel(BaseChatModel):
        @property
        def _llm_type(self):return 'synthetic-service-research'
        def _generate(self,messages,**kwargs):
            data=json.loads(messages[-1].content)
            rows=[{'id':i['id'],'quote':i['body'][:200],'impact':'可能影响订单，仍需验证。','unknown':'尚无财务影响证据。','watch':'关注后续公告。'} for i in data['evidence'][:data['max_events']]]
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps({'events':rows}),usage_metadata={'input_tokens':100,'output_tokens':100,'total_tokens':200}))])
    class Source(SyntheticSource):
        def fetch(self,now):return [replace(super().fetch(now)[0],entities=('AAPL',))]
    market=SyntheticMarket()
    app=BackendService(config,OfflineModel(),BriefModel(),FakeBroker(config.broker.account),channel or FakeChannel(),market,
        ResearchService(market,SyntheticSEC(),SyntheticNews()),sources=[Source()])
    app.monitor.in_session=lambda now:True
    return app

def service_demo(scenario):
    from dataclasses import replace
    import time
    from .channels.telegram import decode
    with tempfile.TemporaryDirectory() as d:
        config=fixture(d);config=replace(config,briefing=replace(config.briefing,at='00:00'))
        app=build_synthetic(config);channel=app.transport
        try:
            now=time.time();first=app.tick(now)
            assert first['status']=='sent' and len(channel.sent)==1
            assert app.tick(now+1)['status']=='sent' and len(channel.sent)==1
            text=decode({'update_id':1,'message':{'from':{'id':123},'chat':{'id':123},'text':'研究 AAPL'}})
            assert app.handle_event(text)=='sent' and 'AAPL' in channel.sent[-1]['text']
            image=decode({'update_id':2,'message':{'from':{'id':123},'chat':{'id':123},'photo':[{'file_id':'SYNTHETIC'}]}})
            assert app.handle_event(image)=='ignored' and len(channel.sent)==2
            assert app.handle_event(image)=='duplicate'
            assert set(app.broker.load()['positions'])=={'AAPL'}
            app.close();app=build_synthetic(config,channel)
            assert app.tick(now+2)['status']=='sent' and len(channel.sent)==2
            unknown='not_exercised'
            if scenario=='end-to-end':
                from unittest.mock import patch
                channel.outcome='unknown'
                with patch('time.time',return_value=now+86400):
                    assert app.tick(now+86400)['status']=='unknown'
                    count=len(channel.sent)
                    assert app.tick(now+86401)['status']=='unknown' and len(channel.sent)==count
                unknown='retained_without_replay'
            return {'synthetic':True,'scenario':scenario,'automatic_reports_without_input':1,'on_demand_responses':1,
                'unsupported_media_ignored':'passed','restart_dedup':'passed','unconfirmed_delivery':unknown,'broker':'ibkr (synthetic)','channel':'telegram (synthetic)','real_messages_sent':0}
        finally:app.close()
