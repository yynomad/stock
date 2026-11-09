"""The daily researcher can read only reviewed evidence and mapped SEC facts."""
import json

from ..research import financial_facts
from ..research_queries import ResearchQueries
from ..sec_filings import FilingReader, filing_request


def parse_lookup(request):
    if isinstance(request, dict) and request.get('tool') == 'lookup_filing':
        required = {'tool','symbol','form','section'}
        if set(request) not in (required, required | {'report_end'}) or not isinstance(request['symbol'],str):
            raise ValueError('invalid daily filing lookup')
        return 'lookup_filing',request['symbol'],filing_request(request['form'],request['section'],request.get('report_end'))
    if not isinstance(request, dict) or set(request) not in ({'tool','symbol'}, {'tool','symbol','period'}):
        raise ValueError('invalid daily lookup request')
    tool, symbol = request['tool'], request['symbol']
    if tool not in ('lookup_evidence','lookup_financials') or not isinstance(symbol,str):
        raise ValueError('unknown daily research tool')
    period = request.get('period','latest')
    if period not in ('latest','quarter') or (tool == 'lookup_evidence' and 'period' in request):
        raise ValueError('invalid daily lookup period')
    return tool,symbol,period


class DailyInvestigation:
    def __init__(self, store, task, config, now, items, candidates, symbols, issuers, http):
        self.queries = ResearchQueries(store, task, config.max_research_queries, config.research_timeout)
        self.items = {item.identity: item for item in items}
        self.candidates = candidates
        self.symbols = set(symbols)
        self.issuers = issuers
        self.http = http
        self.now = now
        self.filing_citations = []
        self.filing_summary = None

    def lookup(self, request):
        tool,symbol,period = parse_lookup(request)
        if symbol not in self.symbols and not (tool=='lookup_evidence' and symbol=='macro'):
            return self.queries.reject(tool,'scope_denied')
        if tool == 'lookup_evidence':
            return self.queries.lookup((tool,symbol),lambda:self._read_evidence(symbol))
        if tool == 'lookup_filing':
            form,section,report_end = period
            key = (tool,symbol,form,section,report_end)
            reader = FilingReader(self.issuers,self.http)
            return self.queries.lookup(key,lambda:reader.read(symbol,form,section,report_end,self.now),once=True)
        issuer = self.issuers.get(symbol, {})
        cik = str(issuer.get('cik',''))
        if not cik.isascii() or not cik.isdigit() or not 1 <= len(cik) <= 10 or int(cik)==0:
            return self.queries.reject(tool,'missing')
        return self.queries.lookup((tool,symbol,period),lambda:self._read_financials(symbol,cik,period))

    def _read_evidence(self, symbol):
        matches = [item for item in self.candidates if symbol in item.entities][:3]
        self.items.update((item.identity,item) for item in matches)
        return {'status':'ok' if matches else 'missing', 'evidence_ids':[item.identity for item in matches]}

    def _read_financials(self, symbol, cik, period):
        url = f'https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json'
        payload = json.loads(self.http.read(url, {'data.sec.gov'}, max_bytes=16*1024*1024))
        facts = financial_facts(payload,cik,period_filter=None if period=='latest' else period,asof=self.now)
        available = {key: fact for key,fact in facts.items() if 'value' in fact}
        return {'status':'ok' if available else 'missing', 'symbol':symbol, 'source':'SEC Company Facts',
                'url':url, 'financials':available}
