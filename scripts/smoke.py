#!/usr/bin/env python3
"""Opt-in real read-only probes; failures never count as verified."""
import argparse
import json
import subprocess
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--service',choices=['ibkr','gemini','telegram'],required=True)
    parser.add_argument('--read-only',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--symbol',default='AAPL')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.service=='gemini' and not args.worker:
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--service','gemini','--symbol',args.symbol,'--worker'],capture_output=True,text=True,timeout=75)
            print(result.stdout.strip());return result.returncode
        except subprocess.TimeoutExpired:
            print(json.dumps({'status':'unverified','reason':'Gemini probe exceeded 75-second total deadline'}));return 2
    try:
        if args.service=='ibkr':
            if not args.read_only:raise ValueError('IBKR probe requires --read-only')
            import lib.config
            from lib.ibkr import fetch_ibkr_positions
            positions=fetch_ibkr_positions()
            print(json.dumps({'status':'read_verified','position_count':len(positions),
                              'limitations':'用户仍需核对持仓、Gateway只读开关和手机并发登录；程序不验证此UI设置'},ensure_ascii=False))
        elif args.service=='gemini':
            from lib.research import GoogleNews,valid_symbol
            bundle=GoogleNews().fetch(valid_symbol(args.symbol))
            print(json.dumps({'status':'grounding_verified','sources':bundle['sources'],'usage':bundle['usage'],'queries':len(bundle['queries'])},ensure_ascii=False))
        else:
            if not args.dry_run:raise ValueError('Telegram probe requires --dry-run; actual delivery is user-tested')
            from lib.telegram import message_chunks
            chunks=message_chunks('<Synthetic report> & evidence '*300)
            assert chunks and all(len(c)>0 for c in chunks)
            print(json.dumps({'status':'dry_run_passed','chunks':len(chunks),'sent':False}))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'unverified','error_type':type(exc).__name__,'reason':'检查所需配置、服务权限及网络；不打印可能包含凭据的原始异常'},ensure_ascii=False))
        return 2
if __name__=='__main__':raise SystemExit(main())
