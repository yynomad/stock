#!/usr/bin/env python3
"""Default preview; --send is an explicit owner-only one-shot action, never a Bot start."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--scenario',default='end-to-end',choices=['pipeline','sources','research','budget','schedule','end-to-end'])
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run',action='store_true',help='default: real sources/model, no Telegram send')
    mode.add_argument('--send',action='store_true',help='send today once to configured private owner')
    mode.add_argument('--status',action='store_true',help='local read-only delivery and coverage state')
    parser.add_argument('--data-dir')
    parser.add_argument('--session',help='preview session; send must match private TG_CHAT_ID')
    parser.add_argument('--public-symbols',nargs='+',help='public watchlist preview, no broker connection')
    args=parser.parse_args(argv)
    if args.offline and (args.send or args.status or args.public_symbols):parser.error('offline demos cannot use live inputs/send/status')
    os.environ['LANGSMITH_TRACING']='false'
    if args.offline:os.environ['STOCKMAN_OFFLINE']='1'
    try:
        if args.offline:
            from stockman.briefing.demo import run
            from stockman.stock_briefing.demo import sources_demo,research_demo,schedule_demo,end_to_end
            with tempfile.TemporaryDirectory() as directory:
                if args.scenario=='pipeline':result=run('pipeline',directory)
                elif args.scenario=='sources':result=sources_demo(directory)
                elif args.scenario in ('research','budget'):result=research_demo(directory,args.scenario=='budget')
                elif args.scenario=='schedule':result=schedule_demo(directory)
                else:result=end_to_end(directory)
        else:
            if args.status:os.environ['STOCKMAN_OFFLINE']='1'
            from stockman.stock_briefing.cli import live
            result=live(args)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result.get('status') in ('failed','unknown') else 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'note':'检查配置／本地记录；没有自动启动 Bot。'},ensure_ascii=False))
        return 2
if __name__=='__main__':raise SystemExit(main())
