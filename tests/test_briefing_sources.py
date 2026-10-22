import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from lib.storage import Store
from lib.briefing.models import SourcePolicy,Evidence
from lib.briefing.pipeline import collect
from lib.stock_briefing.sources import SECSource,RSSSource,PublicHTTP,configured_sources
from lib.stock_briefing.relations import Relations,scope,relevant

POLICY=SourcePolicy('rss','https://example.com/policy',True,True,True,False,True)
class HTTP:
    def __init__(self,raw):self.raw=raw
    def read(self,*args):return self.raw

class BriefingSourcesTest(unittest.TestCase):
    def test_sec_links_timestamps_and_bad_json(self):
        data={'filings':{'recent':{'form':['8-K'],'accessionNumber':['0000320193-26-000001'],'primaryDocument':['event.htm'],'acceptanceDateTime':['2026-10-22T00:00:00Z']}}}
        source=SECSource('AAPL','320193',HTTP(json.dumps(data).encode()))
        row=source.fetch(1800000000)[0]
        self.assertIn('/320193/000032019326000001/event.htm',row.url);self.assertGreater(row.published,0)
        self.assertIn('not retrieved',row.body)
        data['filings']['recent']['form']=[]
        with self.assertRaises(ValueError):SECSource('AAPL','320193',HTTP(json.dumps(data).encode())).fetch(1800000000)
        self.assertEqual(collect([SECSource('AAPL','320193',HTTP(b'not json'))],1800000000)[2][0]['status'],'failed')
    def test_rss_invalid_xml_and_missing_time_are_visible(self):
        raw=b'<rss><channel><item><title>Update</title><link>https://example.com/x</link></item></channel></rss>'
        source=RSSSource('rss','https://example.com/feed',POLICY,['AAPL'],HTTP(raw))
        rows,_,health=collect([source],1800000000)
        self.assertFalse(rows);self.assertEqual(health[0]['invalid'],1)
        for raw in [b'broken',b'<!DOCTYPE rss><rss/>']:
            source.http=HTTP(raw);self.assertEqual(collect([source],1800000000)[2][0]['status'],'failed')
    def test_atom_and_rss_parse_real_publication_dates(self):
        raw=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>A &amp; B</title><link href="https://example.com/a"/><updated>2026-10-22T00:00:00Z</updated><summary>Fact 42</summary></entry></feed>'
        row=RSSSource('rss','https://example.com/feed',POLICY,['AAPL'],HTTP(raw)).fetch(1800000000)[0]
        self.assertEqual(row.title,'A & B');self.assertEqual(row.body,'Fact 42');self.assertGreater(row.published,0)
    def test_unreviewed_feed_and_unmapped_etf(self):
        with self.assertRaises(PermissionError):RSSSource('x','https://example.com/feed',SourcePolicy('x','https://example.com'),[],HTTP(b''))
        sources,missing=configured_sources({'issuers':{},'rss':[]},['QQQ'],HTTP(b''))
        self.assertFalse(sources);self.assertIn('ETF',missing[0])
    def test_public_http_rejects_redirects_private_hosts_and_retries_bounded(self):
        response=unittest.mock.MagicMock();response.__enter__.return_value=response
        response.status_code=429;response.raise_for_status.side_effect=ValueError('429')
        get=unittest.mock.Mock(return_value=response);http=PublicHTTP('Stock reader real@example.com',get,sleep=lambda _:None)
        with patch('socket.getaddrinfo',return_value=[(0,0,0,'',('93.184.216.34',443))]):
            with self.assertRaises(ValueError):http.read('https://www.sec.gov/x',{'www.sec.gov'})
            self.assertEqual(get.call_count,3)
            response.status_code=302
            with self.assertRaises(ValueError):http.read('https://www.sec.gov/x',{'www.sec.gov'})
        with patch('socket.getaddrinfo',return_value=[(0,0,0,'',('127.0.0.1',443))]):
            with self.assertRaises(PermissionError):http.read('https://example.com',{'example.com'})
    def test_relationship_requires_exact_explicit_sentence_not_cooccurrence(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db');relations=Relations(store)
            issuers={'AAA':{'name':'Alpha Corp'},'BBB':{'name':'Beta Corp'},'CCC':{'name':'Gamma Corp'}}
            item=Evidence('rss','Relation','https://example.com/relation',1000,1000,'Beta Corp supplies Alpha Corp.',('AAA','BBB'))
            proposal={'held':'AAA','related':'BBB','kind':'supplier','quote':item.body,'evidence_id':item.identity}
            self.assertTrue(relations.verify(proposal,[item],{'rss':POLICY},issuers,1000))
            self.assertEqual(relations.discover(['AAA'],[item],{'rss':POLICY},issuers,1000),1)
            for change in [{'quote':'Beta Corp and Alpha Corp.'},{'related':'CCC'},{'evidence_id':'invented'},{'quote':'Beta Corp does not supply Alpha Corp.'}]:
                self.assertFalse(relations.verify({**proposal,**change},[item],{'rss':POLICY},issuers,1000))
            self.assertTrue(relations.direct(['AAA'],20000000)[0]['stale'])
            self.assertFalse(relations.direct(['CCC'],1000))
            self.assertTrue(relevant([item],['AAA'],relations.direct(['AAA'],1000)))
            store.close()
    def test_holdings_scope_does_not_expose_account_or_costs(self):
        result=scope({'source':'image','asof':1000,'account':'SECRET','positions':{'AAPL':{'shares':2,'avg_cost':99}}},100000)
        self.assertNotIn('SECRET',str(result));self.assertNotIn('99',str(result));self.assertTrue(result['stale'])

    def test_sec_pacing_gate_is_shared_with_existing_research(self):
        from lib.sec_access import RateGate,SEC_GATE
        from lib import research
        from lib.stock_briefing import sources
        self.assertIs(research.SEC_GATE,SEC_GATE);self.assertIs(sources.SEC_GATE,SEC_GATE)
        clock=[100.0];starts=[]
        def sleep(seconds):clock[0]+=seconds
        gate=RateGate()
        for _ in range(12):
            gate.wait(sleep=sleep,clock=lambda:clock[0]);starts.append(clock[0])
        self.assertTrue(all(b-a>=0.159999 for a,b in zip(starts,starts[1:])))
