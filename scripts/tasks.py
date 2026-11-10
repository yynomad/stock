#!/usr/bin/env python3
"""Read task diagnostics from PostgreSQL without starting agents or a service."""
import argparse
import json
from pathlib import Path
from stockman.database import Database
import sys
from stockman.storage import redact

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default='runtime')
    parser.add_argument('--task')
    args=parser.parse_args()
    with Database(Path(args.data_dir)/'stockman').read_only() as conn:
        if not conn.execute("SELECT to_regclass('tasks') IS NOT NULL AS present").fetchone()['present']:
            parser.error('尚无任务记录；请先运行离线演示或本地任务')
        if args.task:
            task=[dict(r) for r in conn.execute('SELECT * FROM tasks WHERE id=%s',(args.task,))]
            traces=[dict(r) for r in conn.execute('SELECT * FROM traces WHERE task_id=%s ORDER BY id',(args.task,))]
            tables=conn.execute("SELECT to_regclass('usage_calls') IS NOT NULL AS present").fetchone()['present']
            usage=[dict(r) for r in conn.execute("SELECT role,kind,provider,model,elapsed_ms,input_tokens,output_tokens,total_tokens,estimated_micro_usd,extra_cost_unknown,status,at FROM usage_calls WHERE task_id=%s ORDER BY id",(args.task,))] if tables else []
            data=redact({'task':task,'traces':traces})
            from stockman.usage import summary
            data['usage_calls']=usage
            data['usage_summary']=summary(usage)
            budget_table=conn.execute("SELECT to_regclass('task_budget_limits') IS NOT NULL AS present").fetchone()['present']
            budget=conn.execute('SELECT limit_micro_usd FROM task_budget_limits WHERE task_id=%s',(args.task,)).fetchone() if budget_table else None
            if budget:
                data['usage_summary']['limit_micro_usd']=budget['limit_micro_usd']
                data['usage_summary']['limit_reached']=data['usage_summary']['known_estimated_micro_usd']>=budget['limit_micro_usd']
            data['usage_note']='逻辑调用；内部重试及搜索附加费用可能未知；估算不是供应商账单。'
        else:data=[dict(r) for r in conn.execute('SELECT id,session,status,created,updated FROM tasks ORDER BY created DESC,seq DESC LIMIT 20')]
        print(json.dumps(data if args.task else redact(data),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
