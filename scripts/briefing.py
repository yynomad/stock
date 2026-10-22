#!/usr/bin/env python3
"""Briefing CLI. Offline demos are explicit and never send real messages."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--scenario',default='pipeline',choices=['pipeline','sources'])
    args=parser.parse_args()
    if not args.offline:parser.error('live preview will be added after source implementation; use --offline')
    os.environ['STOCKMAN_OFFLINE']='1';os.environ['LANGSMITH_TRACING']='false'
    from lib.briefing.demo import run
    with tempfile.TemporaryDirectory() as directory:
        if args.scenario=='sources':
            from lib.stock_briefing.demo import sources_demo
            result=sources_demo(directory)
        else:result=run(args.scenario,directory)
        print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
