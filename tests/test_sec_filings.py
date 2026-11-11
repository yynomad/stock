import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from requests import HTTPError, Response, Timeout

from test_postgres_storage import assert_text_not_persisted
from stockman.agents import AgentRuntime, OfflineModel
from stockman.briefing.config import BriefingConfig
from stockman.briefing.models import Evidence, SourcePolicy
from stockman.research import ResearchService
from stockman.sec_filings import FilingReader, validate_citations
from stockman.service_config import ModelConfig
from stockman.stock_briefing.service import StockBriefing
from stockman.stock_briefing.sources import SourceTooLarge
from stockman.storage import Store


NOW = 1793832155
ACCESSION = '0000320193-25-000008'
URL = 'https://www.sec.gov/Archives/edgar/data/320193/000032019325000008/aapl-20241228.htm'
END = '2024-12-28'
ISSUERS = {'AAPL': {'cik': '320193', 'filing_body_reviewed': True}}
QUOTE = 'Net sales increased in the Americas.'
CANARY = 'Uncited candidate retained exclusively in this task.'
HTML = f"""<!DOCTYPE html><html><body>
<div><a href="#discussion" id="toc">Item 2. Management Discussion</a></div>
<div>PART I — FINANCIAL INFORMATION</div>
<div>Item 1. Financial Statements</div><p>Outside of the management section.</p>
<div id="discussion"><b>Item 2.</b> Management Discussion and Analysis</div>
<p>  Net&nbsp;sales <span>increased</span> in the Americas. Product mix contributed.</p>
<ix:hidden><p>Hidden text must never be a passage.</p></ix:hidden>
<p aria-hidden="true">Invisible text must never be a passage.</p>
<table><tr><td>Table sales were off by a billion dollars.</td></tr></table>
<p>{CANARY}</p>
<div>PART II — OTHER INFORMATION</div>
<div>Item 1A. Risk Factors</div><p>Supply disruptions may adversely affect results.</p>
</body></html>""".encode()
LOOKUP = {'tool': 'lookup_filing', 'symbol': 'AAPL', 'form': '10-Q',
          'section': 'management', 'report_end': END}


def submissions():
    columns = {
        'form': ['10-Q/A', '10-Q', '10-Q', '10-Q', '10-K'],
        'reportDate': [END, '2026-12-27', '2025-03-29', END, '2025-09-27'],
        'acceptanceDateTime': ['2025-08-01T20:00:00Z', '2027-01-30T20:00:00Z',
                               '2025-05-01T20:00:00Z', '2025-01-30T20:00:00Z', '2025-10-30T20:00:00Z'],
        'accessionNumber': ['0000320193-25-000010', '0000320193-27-000001',
                            '0000320193-25-000009', ACCESSION, '0000320193-25-000011'],
        'primaryDocument': ['amended.htm', 'future.htm', 'next.htm', 'aapl-20241228.htm', 'annual.htm'],
    }
    return {'filings': {'recent': columns}}


def mock_http(body=HTML, index=None):
    return Mock(**{'read.side_effect': [json.dumps(index or submissions()).encode(), body]})


def model_result(**kwargs):
    return ChatResult(generations=[ChatGeneration(message=AIMessage(
        usage_metadata={'input_tokens': 10, 'output_tokens': 10, 'total_tokens': 20}, **kwargs))])


class FilingChatModel(OfflineModel):
    invalid: bool = False

    def _generate(self, messages, **kwargs):
        if 'lookup_filing' not in self.names:
            return super()._generate(messages, **kwargs)
        last = messages[-1]
        if not isinstance(last, ToolMessage):
            # Deliberately supplies the wrong form and period. The user's explicit request wins.
            return model_result(content='', tool_calls=[{'name': 'lookup_filing', 'id': 'sec',
                                'args': {'symbol': 'AAPL', 'form': '10-K', 'section': 'management',
                                         'report_end': '2025-09-27'}}])
        lookup = json.loads(last.content)
        passage = next(row for row in lookup['passages'] if QUOTE in row['text'])
        return model_result(content=json.dumps({
            'answer': '管理层披露美洲地区销售额上升。' if not self.invalid else CANARY,
            'filing_citations': [{'id': passage['id'], 'quote': QUOTE if not self.invalid else 'Invented disclosure.'}],
        }))


class FilingDailyModel(BaseChatModel):
    invalid: bool = False

    @property
    def _llm_type(self):
        return 'scripted-filing-daily-test'

    def _generate(self, messages, **kwargs):
        payload = json.loads(messages[-1].content)
        if 'lookup_result' not in payload:
            return model_result(content=json.dumps({'lookup': LOOKUP}))
        item = payload['evidence'][0]
        passage = next(row for row in payload['lookup_result']['passages'] if QUOTE in row['text'])
        return model_result(content=json.dumps({
            'events': [{'id': item['id'], 'quote': item['body'], 'impact': '销售表现仍需核实',
                        'unknown': '不清楚未来表现', 'watch': '关注后续披露'}],
            'filing_summary': '管理层披露美洲地区销售额上升。',
            'filing_citations': [{'id': passage['id'], 'quote': 'Invented disclosure.' if self.invalid else QUOTE}],
        }))


class FilingSource:
    policy = SourcePolicy('test-sec', 'https://example.com/policy', True, True, True, False, True)

    def fetch(self, now):
        return [Evidence('test-sec', 'Apple filing', 'https://example.com/filing', now-10, now,
                         'The issuer submitted a filing.', ('AAPL',))]


class SECReaderTest(unittest.TestCase):
    def test_reads_exact_period_and_omits_toc_hidden_content_tables_and_other_sections(self):
        http = mock_http()
        result = FilingReader(ISSUERS, http).read('AAPL','10-Q','management',END,NOW)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['filing']['report_end'], END)
        self.assertEqual(result['filing']['published'], 1738267200)
        self.assertEqual(result['filing']['url'], URL)
        self.assertEqual([p['text'] for p in result['passages']], [QUOTE+' Product mix contributed.', CANARY])
        first = result['passages'][0]
        self.assertIn('Part I Item 2', first['section'])
        self.assertEqual(first['anchor'], 'discussion')
        http.read.assert_called_with(URL, {'www.sec.gov'}, max_bytes=16*1024*1024,
                                    accept='text/html, application/xhtml+xml')

    def test_latest_respects_cutoff_and_missing_exact_period_does_not_substitute(self):
        result = FilingReader(ISSUERS, mock_http()).read('AAPL','10-Q','management',None,NOW)
        self.assertEqual(result['filing']['report_end'], '2025-03-29')
        http = mock_http()
        result = FilingReader(ISSUERS,http).read('AAPL','10-Q','management','2025-06-28',NOW)
        self.assertEqual(result, {'status':'filing_missing'})
        http.read.assert_called_once()

    def test_rejects_unreviewed_issuer_and_unsupported_form_section_before_network(self):
        http = Mock()
        self.assertEqual(FilingReader({},http).read('AAPL','10-Q','management',None,NOW)['status'], 'filing_not_reviewed')
        self.assertEqual(FilingReader(ISSUERS,http).read('AAPL','10-Q','business',None,NOW)['status'], 'filing_section_missing')
        http.read.assert_not_called()

    def test_invalid_index_cannot_redirect_to_untrusted_url(self):
        index = submissions()
        index['filings']['recent']['primaryDocument'][3] = 'https://example.org/bad.htm'
        http = mock_http(index=index)
        result = FilingReader(ISSUERS,http).read('AAPL','10-Q','management',END,NOW)
        self.assertEqual(result, {'status':'filing_metadata_invalid'})
        http.read.assert_called_once()

    def test_body_failures_keep_only_safe_metadata(self):
        rate = Response()
        rate.status_code = 429
        missing = Response()
        missing.status_code = 404
        for body,status in [(Timeout('PRIVATE_BODY'), 'source_timeout'),
                            (HTTPError('PRIVATE_BODY', response=rate), 'filing_rate_limited'),
                            (HTTPError('PRIVATE_BODY', response=missing), 'filing_missing'),
                            (SourceTooLarge('PRIVATE_BODY'), 'filing_too_large'),
                            (b'PRIVATE_BODY', 'filing_parse_failed'),
                            (b'<html><body><p>PRIVATE_BODY</p></body></html>', 'filing_section_missing')]:
            with self.subTest(status=status):
                result = FilingReader(ISSUERS,mock_http(body)).read('AAPL','10-Q','management',END,NOW)
                self.assertEqual(result['status'],status)
                self.assertEqual(result['filing']['url'],URL)
                self.assertNotIn('PRIVATE_BODY',str(result))

    def test_annual_part_item_and_citation_fallback_without_unique_anchor(self):
        body = b"""<html><body><div>Part I</div><div>Item 7. Not the correct part</div>
        <p>Prior part contains unrelated management discussion.</p>
        <div>Part II</div><div id="duplicate">Item 7. Management Discussion</div>
        <p>Net sales increased in the Americas.</p><div id="duplicate">Item 8. Financial Statements</div>
        </body></html>"""
        result = FilingReader(ISSUERS,mock_http(body)).read('AAPL','10-K','management',None,NOW)
        row, = result['passages']
        self.assertIsNone(row['anchor'])
        citation, = validate_citations([{'id':row['id'],'quote':QUOTE}], [result])
        self.assertIn('Part II Item 7',citation['section'])
        self.assertEqual(citation['url'], result['filing']['url'])
        with self.assertRaises(ValueError):
            validate_citations([{'id':row['id'],'quote':'Wrong short quotation.'}], [result])
        with self.assertRaises(ValueError):
            validate_citations([{'id':'unknown','quote':QUOTE}], [result])


class SECAgentTest(unittest.TestCase):
    def test_question_defaults_to_latest_and_ignores_model_supplied_period(self):
        with TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory,FilingChatModel(),research=ResearchService(
                filings=FilingReader(ISSUERS,mock_http())))
            try:
                result = runtime.run('研究AAPL最新10-Q的MD&A')
                self.assertEqual(result['status'],'completed')
                self.assertEqual(result['research']['filing_citations'][0]['report_end'],'2025-03-29')
            finally:
                runtime.close()

    def test_question_verifies_quote_and_never_checkpoints_raw_passages(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid), TemporaryDirectory() as directory:
                runtime = AgentRuntime(directory,FilingChatModel(invalid=invalid),research=ResearchService(
                    filings=FilingReader(ISSUERS,mock_http())))
                try:
                    result = runtime.run('研究 AAPL 10-Q 2024-12-28 的 MD&A')
                    self.assertEqual(result['research']['queries'],1)
                    self.assertEqual(result['research']['filings'][0]['filing']['url'],URL)
                    self.assertNotIn('passages',result['research']['filings'][0])
                    if invalid:
                        self.assertEqual(result['status'],'partial')
                        self.assertNotIn('filing_citations',result['research'])
                        self.assertIn('未取得可核验的 SEC 正文引文',result['text'])
                    else:
                        self.assertEqual(result['status'],'completed')
                        self.assertIn(QUOTE,result['text'])
                        self.assertIn(URL+'#discussion',result['text'])
                    self.assertNotIn(CANARY,str(result))
                finally:
                    runtime.close()
                assert_text_not_persisted(self,runtime.store.database,CANARY)

    def test_interrupted_question_keeps_filing_metadata_without_claiming_citation(self):
        with TemporaryDirectory() as directory:
            runtime = AgentRuntime(directory,FilingChatModel(),settings=ModelConfig(key='',max_calls=2),
                research=ResearchService(filings=FilingReader(ISSUERS,mock_http())))
            try:
                result = runtime.run('研究 AAPL 10-Q 2024-12-28 的 MD&A')
                self.assertEqual(result['status'],'partial')
                self.assertEqual(result['research']['stop_reason'],'CallLimit')
                self.assertIn(URL,result['text'])
                self.assertNotIn(QUOTE,result['text'])
                self.assertNotIn(CANARY,str(result))
            finally:
                runtime.close()

    def test_daily_checked_quote_and_invalid_quote_fallback(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid), TemporaryDirectory() as directory:
                store = Store(Path(directory)/'db')
                try:
                    service = StockBriefing(store, BriefingConfig(), FilingDailyModel(invalid=invalid),
                        registry={'issuers':ISSUERS,'rss':[]},http=mock_http())
                    result = service.generate('owner',{'source':'snaptrade','asof':NOW,'positions':{'AAPL':{'shares':1}}},
                                              now=NOW,sources=[FilingSource()])
                    self.assertEqual(result['meta']['budget']['calls'],2)
                    self.assertEqual(result['meta']['research']['queries'],1)
                    self.assertIn('https://example.com/filing',result['text'])
                    if invalid:
                        self.assertIn('deterministic_source_fallback',result['meta']['status'])
                        self.assertIn(URL,result['text'])
                        self.assertNotIn(QUOTE,result['text'])
                        self.assertNotIn('filing_citations',result['meta'])
                    else:
                        self.assertIn(QUOTE,result['text'])
                        self.assertIn(URL+'#discussion',result['text'])
                        self.assertEqual(result['meta']['filing_citations'][0]['accession'],ACCESSION)
                    stored = store.execute('SELECT text,meta FROM brief_reports')[0]
                    self.assertNotIn(CANARY,str(stored))
                    self.assertNotIn(CANARY,str(store.execute('SELECT metadata FROM brief_evidence')))
                finally:
                    store.close()


if __name__ == '__main__':
    unittest.main()
