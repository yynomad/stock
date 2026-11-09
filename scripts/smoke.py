#!/usr/bin/env python3
"""Opt-in real read-only probes; failures never count as verified."""
import argparse
import json
import subprocess
from pathlib import Path
import sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--service',choices=['snaptrade','gemini','telegram'],required=True)
    parser.add_argument('--read-only',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--list-accounts',action='store_true',help='list account IDs for local configuration')
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
        if args.service=='snaptrade':
            if not args.read_only:raise ValueError('SnapTrade probe requires --read-only')
            import os
            import stockman.config
            from stockman.service_config import BrokerConfig
            from stockman.brokers.snaptrade import SnapTradeBroker
            from uuid import UUID
            account=os.getenv('SNAPTRADE_ACCOUNT_ID','')
            client_id=os.getenv('SNAPTRADE_CLIENT_ID','')
            consumer_key=os.getenv('SNAPTRADE_CONSUMER_KEY','')
            if not client_id or not consumer_key:raise ValueError('missing SnapTrade Personal credentials')
            if args.list_accounts:
                from collections.abc import Mapping
                from snaptrade_client import SnapTrade,SnapTradeAuth
                client=SnapTrade(auth=SnapTradeAuth.personal_api_key(client_id=client_id,consumer_key=consumer_key))
                payload=client.account_information.list_all_user_accounts().body
                if not isinstance(payload,Mapping) or not isinstance(payload.get('results'),(tuple,list)):raise ValueError('invalid account list')
                accounts=payload['results']
                safe=[]
                for item in accounts:
                    if not isinstance(item,Mapping):raise ValueError('invalid account')
                    identity=str(item.get('id',''))
                    if str(UUID(identity))!=identity.lower():raise ValueError('invalid account id')
                    number=str(item.get('masked_account_number') or '')
                    suffix=number[-4:] if len(number)>=4 else None
                    safe.append({'account_id':identity,'kind':str(item.get('kind') or 'unknown'),'account_suffix':suffix})
                print(json.dumps({'status':'accounts_listed','accounts':safe,'note':'仅在本机查看；选择联名账户 ID，勿公开'},ensure_ascii=False))
                return 0
            if str(UUID(account))!=account.lower():raise ValueError('missing target account id')
            snapshot=SnapTradeBroker(BrokerConfig('snaptrade',account,client_id,consumer_key)).sync()
            import time
            positions=snapshot.validate('snaptrade',account,time.time())
            from stockman.brokers.coverage import coverage_info,coverage_notice
            coverage=coverage_info({'positions':positions,'coverage':snapshot.coverage})
            print(json.dumps({'status':'read_verified','position_count':len(positions),'asof':snapshot.asof,
                              'coverage':{key:value for key,value in coverage.items() if key!='excluded'},'scope_note':coverage_notice({**coverage,'excluded':[]}),
                              'limitations':'用户需核对目标联名账户、持仓数量及数据时效；结果不证明实时或完整覆盖'},ensure_ascii=False))
        elif args.service=='gemini':
            from stockman.research import GoogleNews,valid_symbol
            bundle=GoogleNews().fetch(valid_symbol(args.symbol))
            print(json.dumps({'status':'grounding_verified','sources':bundle['sources'],'usage':bundle['usage'],'queries':len(bundle['queries'])},ensure_ascii=False))
        else:
            if not args.dry_run:raise ValueError('Telegram probe requires --dry-run; actual delivery is user-tested')
            from stockman.telegram import message_chunks
            chunks=message_chunks('<Synthetic report> & evidence '*300)
            assert chunks and all(len(c)>0 for c in chunks)
            print(json.dumps({'status':'dry_run_passed','chunks':len(chunks),'sent':False}))
        return 0
    except Exception as exc:
        from stockman.brokers.base import BrokerError
        detail={'status':'unverified','error_type':type(exc).__name__,'reason':'检查所需配置、服务权限及网络；不打印可能包含凭据的原始异常'}
        if isinstance(exc,BrokerError):detail['failure_code']=exc.code
        print(json.dumps(detail,ensure_ascii=False))
        return 2
if __name__=='__main__':raise SystemExit(main())
