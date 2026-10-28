#!/usr/bin/env python3
"""Local Agent CLI; --offline uses synthetic dependencies and a separate database."""
import argparse
import json
import os
from pathlib import Path
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('query',nargs='?',default='检查持仓')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--session',default='local')
    parser.add_argument('--data-dir')
    parser.add_argument('--resume-task')
    args=parser.parse_args()
    if args.offline:os.environ['STOCKMAN_OFFLINE']='1'
    from stockman.application import Application
    from stockman.offline import create_offline_app
    directory=args.data_dir or ('runtime/offline' if args.offline else None)
    app=None
    try:
        app=create_offline_app(directory) if args.offline else Application(directory)
        result=app.runtime.resume(args.resume_task) if args.resume_task else app.handle(args.query,args.session)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'note':'查看本地任务记录；不表示真实接入成功'},ensure_ascii=False));return 2
    finally:
        if app:app.runtime.close()
if __name__=='__main__':raise SystemExit(main())
