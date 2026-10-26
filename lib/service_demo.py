"""Explicit fixture config and later service demonstrations; never a real account."""
from pathlib import Path
import tempfile
from .service_config import load_config

ENV={'IBKR_ACCOUNT':'U1234567','GEMINI_API_KEY':'synthetic-model-key','TG_BOT_TOKEN':'synthetic-bot-token','TG_CHAT_ID':'123'}

def fixture(directory):
    root=Path(__file__).resolve().parents[1]
    path=Path(directory)/'stock.conf'
    text=(root/'stock.conf.example').read_text().replace('file = briefing.sources.json','file = '+str(root/'briefing.sources.json'))
    path.write_text(text)
    return load_config(path,ENV)

def run(scenario):
    if scenario=='broker':return broker_demo()
    if scenario=='channel':return channel_demo()
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
        image=decode({'update_id':2,'message':{'photo':[{'file_id':'synthetic-image'}]}})
        assert text.text=='研究 AAPL' and image.image_id=='synthetic-image' and channel.chat==123
        return {'synthetic':True,'scenario':'channel','normalized_text_and_image':'passed','real_messages_sent':0}
