#!/usr/bin/env python3
"""Opt-in real read-only probes; failures never count as verified."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--service',choices=['ibkr','gemini','telegram'],required=True)
    parser.add_argument('--read-only',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--symbol',default='AAPL')
    args=parser.parse_args()
    try:
        if args.service=='ibkr':
            if not args.read_only:raise ValueError('IBKR probe requires --read-only')
            import lib.config
            from lib.ibkr import fetch_ibkr_positions
            positions=fetch_ibkr_positions()
            print(json.dumps({'status':'read_verified','position_count':len(positions),
                              'limitations':'用户仍需核对持仓、Gateway只读开关和手机并发登录；程序不验证此UI设置'},ensure_ascii=False))
        else:raise ValueError('该服务 smoke 尚未实现')
        return 0
    except Exception as exc:
        print(json.dumps({'status':'unverified','reason':str(exc)},ensure_ascii=False))
        return 2
if __name__=='__main__':raise SystemExit(main())
