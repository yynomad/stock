#!/usr/bin/env python3
"""Read local tasks without loading credentials, constructing agents or connecting externally."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from stockman.storage import redact

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default='runtime')
    parser.add_argument('--task')
    args=parser.parse_args()
    path=(Path(args.data_dir)/'stockman.sqlite').resolve()
    if not path.is_file():parser.error('尚无任务数据库；请先运行离线演示或本地任务')
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        if args.task:
            task=[dict(r) for r in conn.execute('SELECT * FROM tasks WHERE id=?',(args.task,))]
            traces=[dict(r) for r in conn.execute('SELECT * FROM traces WHERE task_id=? ORDER BY id',(args.task,))]
            data={'task':task,'traces':traces}
        else:data=[dict(r) for r in conn.execute('SELECT id,session,status,created,updated FROM tasks ORDER BY created DESC LIMIT 20')]
        print(json.dumps(redact(data),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
