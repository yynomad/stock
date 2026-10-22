"""Explicit one-shot live preview/send. No Bot polling or webhook operations."""
import json
import os
from pathlib import Path
import sqlite3
import time
from ..briefing.config import BriefingConfig
from ..briefing.locking import DirectoryLock
from ..briefing.schedule import Scheduler
from ..storage import Store
from .service import StockBriefing

def execute(args,store,config,service=None,portfolio=None,transport=None,now=None):
    from ..portfolio import PortfolioService,valid_symbol
    now=time.time() if now is None else now
    owner=args.session or os.getenv('TG_CHAT_ID') or 'local'
    if args.send:
        target=os.getenv('TG_CHAT_ID','')
        allowed={x.strip() for x in os.getenv('TG_ALLOWED_USERS','').split(',') if x.strip()}
        if not allowed and target.isdigit() and int(target)>0:allowed={target}
        if owner!=target or not owner.isdigit() or int(owner)<=0 or owner not in allowed:raise PermissionError('send requires configured private TG_CHAT_ID and authorized owner')
        if args.public_symbols:raise ValueError('public symbols are preview-only; use your explicit holdings source for sending')
    service=service or StockBriefing(store,config)
    portfolio=portfolio or PortfolioService(store)
    def build(owner,stamp,day=None):
        if args.public_symbols:
            symbols=[valid_symbol(s) for s in args.public_symbols]
            snapshot={'source':'public_watchlist (not actual holdings)','asof':stamp,'positions':{s:{'shares':None} for s in symbols}}
        elif args.snapshot:snapshot=portfolio.load(owner,args.snapshot)
        elif config.portfolio_source=='image':
            snapshot=store.latest('image',owner)
            if not snapshot:raise ValueError('no image snapshot for this session; upload/import a screenshot first')
            snapshot['cached']=True
        else:snapshot=portfolio.load(owner)
        return service.generate(owner,snapshot,stamp,day)
    if not args.send:return build(owner,now)
    if transport is None:
        from ..telegram import Telegram
        transport=Telegram()
    if str(transport.chat)!=owner:raise PermissionError('transport destination mismatch')
    scheduler=Scheduler(store,config,owner,allowed,build,transport.deliver)
    scheduler.recover()
    return scheduler.tick(now,force=True)

def status(directory):
    path=(Path(directory)/'stockman.sqlite').resolve()
    if not path.is_file():return {'daily':[],'reports':[],'note':'no briefing database yet'}
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        daily=[dict(r) for r in conn.execute('SELECT * FROM brief_daily ORDER BY day DESC LIMIT 10')] if 'brief_daily' in names else []
        reports=[dict(r) for r in conn.execute('SELECT id,owner,day,status,meta FROM brief_reports ORDER BY at DESC LIMIT 10')] if 'brief_reports' in names else []
        return {'daily':daily,'reports':reports,'note':'unknown requires manual Telegram verification; never auto-replayed'}

def live(args):
    # dotenv is loaded through the existing config, then the daily config is validated.
    from .. import config as existing_config
    config=BriefingConfig.load()
    directory=args.data_dir or os.getenv('STOCKMAN_DATA_DIR','runtime')
    if args.status:return status(directory)
    lock=DirectoryLock(directory);store=None
    try:
        store=Store(Path(directory)/'stockman.sqlite')
        return execute(args,store,config)
    finally:
        if store:store.close()
        lock.close()
