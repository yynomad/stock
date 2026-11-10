"""Explicit one-shot live preview/send. No Bot polling or webhook operations."""
import json
import os
from pathlib import Path
import time
from ..briefing.config import BriefingConfig
from ..briefing.locking import DirectoryLock
from ..briefing.schedule import Scheduler
from ..database import Database
from ..storage import Store
from .service import StockBriefing

def execute(args,store,config,service=None,portfolio=None,transport=None,now=None,writer_check=None):
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
        else:snapshot=portfolio.load(owner)
        return service.generate(owner,snapshot,stamp,day)
    if not args.send:return build(owner,now)
    if transport is None:
        from ..telegram import Telegram
        transport=Telegram()
    if str(transport.chat)!=owner:raise PermissionError('transport destination mismatch')
    def deliver(payload):
        if writer_check is not None:writer_check()
        return transport.deliver(payload)
    scheduler=Scheduler(store,config,owner,allowed,build,deliver)
    scheduler.recover()
    return scheduler.tick(now,force=True)

def status(directory):
    with Database(Path(directory)/'stockman').read_only() as conn:
        names={r['tablename'] for r in conn.execute('SELECT tablename FROM pg_tables WHERE schemaname=current_schema()')}
        if 'brief_daily' not in names and 'brief_reports' not in names:
            return {'daily':[],'reports':[],'note':'no briefing database yet'}
        # Status queries omit report bodies, owner IDs, and raw diagnostics.
        daily=[dict(r) for r in conn.execute('SELECT day,status,report,attempts,scheduled,actual,next_try FROM brief_daily ORDER BY day DESC LIMIT 10')] if 'brief_daily' in names else []
        reports=[dict(r) for r in conn.execute('SELECT id,day,status,at FROM brief_reports ORDER BY at DESC LIMIT 10')] if 'brief_reports' in names else []
        return {'daily':daily,'reports':reports,'note':'unknown requires manual Telegram verification; never auto-replayed'}

def live(args):
    # dotenv is loaded through the existing config, then the daily config is validated.
    from .. import config as existing_config
    directory=args.data_dir or os.getenv('STOCKMAN_DATA_DIR','runtime')
    if args.status:return status(directory)
    config=BriefingConfig.load()
    lock=DirectoryLock(directory)
    try:
        path=Path(directory)/'stockman'
        database=Database(path)
        with database.writer() as lease:
            store=Store(path,database)
            try:
                return execute(args,store,config,writer_check=lease.check)
            finally:
                store.close()
    finally:
        lock.close()
