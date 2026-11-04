"""Explicit holdings scope, counted by source position records, never market value."""
import re

SCOPE='usd_equities_v1'
FORMAT='stockman-scoped-snapshot-v1'
NOTE='仅覆盖美元美股/ETF/ADR；不代表完整账户资产，不计算跨币种总额。'


def make_coverage(total,included,excluded):
    value={'scope':SCOPE,'total_positions':total,'included_positions':included,'excluded':excluded}
    return validate_coverage(value,included)


def validate_coverage(value,included):
    if not isinstance(value,dict) or set(value)!={'scope','total_positions','included_positions','excluded'} or value['scope']!=SCOPE:
        raise ValueError('invalid coverage schema')
    total=value['total_positions'];count=value['included_positions'];excluded=value['excluded']
    if type(total) is not int or total<0 or type(count) is not int or count<0 or count!=included or not isinstance(excluded,list) or total!=count+len(excluded):
        raise ValueError('invalid coverage counts')
    clean=[];seen=set()
    for row in excluded:
        if not isinstance(row,dict) or set(row)!={'index','symbol','currency','reason'}:
            raise ValueError('invalid excluded position')
        index=row['index'];symbol=row['symbol'];currency=row['currency']
        if type(index) is not int or not 0<=index<total or index in seen:
            raise ValueError('invalid exclusion index')
        if symbol is not None and (not isinstance(symbol,str) or not re.fullmatch(r'[A-Z0-9][A-Z0-9.:-]{0,31}',symbol)):
            raise ValueError('invalid excluded symbol')
        if not isinstance(currency,str) or not re.fullmatch('[A-Z]{3}',currency) or currency=='USD' or row['reason']!='non_usd':
            raise ValueError('invalid exclusion reason')
        seen.add(index);clean.append(dict(row))
    return {'scope':SCOPE,'total_positions':total,'included_positions':count,'excluded':clean}


def coverage_info(snapshot):
    raw=snapshot.get('coverage')
    if raw is None:
        if str(snapshot.get('source','')).startswith('public_watchlist'):
            count=len(snapshot['positions'])
            return {'scope':'public_watchlist','included_positions':count,'total_positions':count,
                    'excluded_positions':0,'excluded':[],'partial':False,'count_basis':'position_records_not_market_value',
                    'note':'公开观察清单，不是实际持仓或完整账户资产。'}
        return {'scope':'unknown','included_positions':len(snapshot['positions']),'total_positions':None,
                'excluded_positions':None,'excluded':[], 'partial':True,
                'count_basis':'position_records_not_market_value',
                'note':'持仓覆盖范围未知：此快照没有排除记录，不能认定为全账户组合。'}
    value=validate_coverage(raw,len(snapshot['positions']))
    return {**value,'excluded_positions':len(value['excluded']),'partial':bool(value['excluded']),
            'count_basis':'position_records_not_market_value','note':NOTE}


def same_coverage(left,right):
    """Source row order is not an economic change; preserve duplicate row counts."""
    if left is None or right is None:return left==right
    def signature(value):
        rows=sorted((row['symbol'] or '',row['currency'],row['reason']) for row in value['excluded'])
        return value['scope'],value['total_positions'],value['included_positions'],rows
    return signature(left)==signature(right)


def coverage_notice(info):
    if info['scope']=='unknown':return info['note']
    text=f'分析范围：{info["note"]} 覆盖 {info["included_positions"]}/{info["total_positions"]} 条持仓记录（非市值覆盖率），排除 {info["excluded_positions"]} 条。'
    if info['excluded']:
        text+=' 范围外：'+'；'.join(f'{row["symbol"] or "标识缺失"}（{row["currency"]}，非美元）' for row in info['excluded'])+'。'
    if not info['included_positions']:
        text+=' 范围内无持仓；这不表示账户空仓。'
    return text
