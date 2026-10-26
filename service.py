#!/usr/bin/env python3
"""Standalone configured backend. --check is side-effect-free; --offline uses fixtures."""
import argparse
import json
import os

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='stock.conf')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--scenario',default='config',choices=['config'])
    args=parser.parse_args(argv)
    os.environ['STOCKMAN_EXPLICIT_CONFIG']='1'
    if args.offline:os.environ['STOCKMAN_OFFLINE']='1'
    try:
        if args.offline:
            from lib.service_demo import run
            result=run(args.scenario)
        else:
            from lib.service_config import load_config
            config=load_config(args.config)
            if not args.check:raise ValueError('service runtime is not implemented in this milestone')
            result={'status':'config_valid','broker':config.broker.provider,'channel':config.channel.provider,'network':False,'sent':False}
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:
        from lib.service_config import ConfigError
        result={'status':'failed','error_type':type(exc).__name__}
        if isinstance(exc,ConfigError):result['field_error']=str(exc)
        print(json.dumps(result,ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
