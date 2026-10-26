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
    if scenario!='config':raise ValueError('unsupported demo scenario')
    with tempfile.TemporaryDirectory() as d:
        config=fixture(d)
        assert not config.directory.exists()
        return {'synthetic':True,'scenario':scenario,'broker':config.broker.provider,'channel':config.channel.provider,'check_without_runtime_write':'passed'}
