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
            tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='usage_calls'").fetchone()
            usage=[dict(r) for r in conn.execute("SELECT role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at FROM usage_calls WHERE task_id=? ORDER BY id",(args.task,))] if tables else []
            data=redact({'task':task,'traces':traces})
            from stockman.usage import summary
            data['usage_calls']=usage
            data['usage_summary']=summary(usage)
            budget_table=conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='task_budget_limits'").fetchone()
            budget=conn.execute('SELECT limit_micro_usd FROM task_budget_limits WHERE task_id=?',(args.task,)).fetchone() if budget_table else None
            if budget:
                data['usage_summary']['limit_micro_usd']=budget['limit_micro_usd']
                data['usage_summary']['limit_reached']=data['usage_summary']['known_estimated_micro_usd']>=budget['limit_micro_usd']
            data['usage_note']='逻辑调用；内部重试及搜索附加费用可能未知；估算不是供应商账单。'
        else:data=[dict(r) for r in conn.execute('SELECT id,session,status,created,updated FROM tasks ORDER BY created DESC LIMIT 20')]
        print(json.dumps(data if args.task else redact(data),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
