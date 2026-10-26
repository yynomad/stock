#!/usr/bin/env python3
"""Standalone configured backend. --check is side-effect-free; --offline uses fixtures."""
import argparse
import json
import os

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='stock.conf')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true')
    mode.add_argument('--status',action='store_true')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--scenario',default='end-to-end',choices=['config','broker','channel','service','end-to-end'])
    args=parser.parse_args(argv)
    if args.offline and (args.check or args.status):parser.error('offline scenarios do not use live check/status')
    os.environ['STOCKMAN_EXPLICIT_CONFIG']='1'
    if args.offline:os.environ['STOCKMAN_OFFLINE']='1'
    try:
        if args.offline:
            from lib.service_demo import run
            result=run(args.scenario)
        else:
            from lib.service_config import load_config
            config=load_config(args.config)
            if args.status:
                from lib.service import read_status
                result=read_status(config)
            elif not args.check:
                from lib.service import BackendService
                service=BackendService(config)
                service.run()
                return 0
            else:result={'status':'config_valid','broker':config.broker.provider,'channel':config.channel.provider,'network':False,'sent':False}
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:
        from lib.service_config import ConfigError
        result={'status':'failed','error_type':type(exc).__name__}
        if isinstance(exc,ConfigError):result['field_error']=str(exc)
        print(json.dumps(result,ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
