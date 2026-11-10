#!/usr/bin/env python3
"""Local Agent CLI; --offline uses synthetic dependencies and a separate database."""
import argparse
import json
import os
from pathlib import Path
import sys

from stockman.storage import RESPONSE_STYLES, Store


def response_style(directory, session, action):
    from stockman.briefing.locking import DirectoryLock
    from stockman.database import Database
    lock = DirectoryLock(directory)
    try:
        location = Path(directory) / 'stockman'
        database = Database(location)
        with database.writer():
            store = Store(location,database)
            try:
                if action != 'show':
                    store.set_response_style(session, None if action == 'clear' else action)
                return {'response_style': store.response_style(session)}
            finally:
                store.close()
    finally:
        lock.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('query',nargs='?')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--session',default='local')
    parser.add_argument('--data-dir')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--resume-task')
    action.add_argument('--response-style', choices=(*RESPONSE_STYLES, 'show', 'clear'))
    args=parser.parse_args()
    if args.offline:os.environ['STOCKMAN_OFFLINE']='1'
    directory=args.data_dir or ('runtime/offline' if args.offline else None)
    app=None
    try:
        if args.response_style:
            if args.query is not None:
                parser.error('--response-style cannot be combined with a question')
            result = response_style(directory or os.getenv('STOCKMAN_DATA_DIR', 'runtime'), args.session, args.response_style)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        from stockman.application import Application
        from stockman.offline import create_offline_app
        app=create_offline_app(directory) if args.offline else Application(directory)
        result=app.runtime.resume(args.resume_task) if args.resume_task else app.handle('检查持仓' if args.query is None else args.query,args.session)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except Exception as exc:
        notes = {'TaskBudgetExceeded': '已知费用达到任务阈值，剩余调查未完成；未知费用仍可能产生',
                 'ContextOverflow': '当前问题与证据超出上下文容量，请缩小调查范围'}
        from stockman.model_providers import ModelConfigurationError
        note = str(exc) if isinstance(exc,ModelConfigurationError) else notes.get(type(exc).__name__, '查看本地任务记录')
        print(json.dumps({'status':'failed','error_type':type(exc).__name__,'note':note},ensure_ascii=False));return 2
    finally:
        if app:app.runtime.close()
if __name__=='__main__':raise SystemExit(main())
