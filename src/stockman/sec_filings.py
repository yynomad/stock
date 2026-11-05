"""Bounded SEC primary document reads and verifiable, task-local passages."""
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import re
from urllib.parse import quote as url_quote

from requests.exceptions import HTTPError, RequestException, Timeout

from .stock_briefing.sources import SourceTooLarge, timestamp

FORMS = ('10-K', '10-Q')
SECTIONS = ('management', 'risks', 'business')
SECTION_IDS = {
    '10-K': {'management': ('II', '7'), 'risks': ('I', '1A'), 'business': ('I', '1')},
    '10-Q': {'management': ('I', '2'), 'risks': ('II', '1A')},
}
SECTION_NAMES = {'management': 'Management’s Discussion and Analysis', 'risks': 'Risk Factors', 'business': 'Business'}
SECTION_HINTS = {'management': ('revenue', 'net sales', 'increased', 'decreased', 'margin'),
                 'risks': ('risk', 'uncertain', 'competition', 'adverse'),
                 'business': ('products', 'services', 'market', 'business')}
MAX_QUOTE = 240
BODY_LIMIT = 16 * 1024 * 1024


def valid_report_end(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def filing_request(form, section, report_end=None):
    if form not in FORMS or section not in SECTIONS or (report_end is not None and not valid_report_end(report_end)):
        raise ValueError('invalid filing request')
    return form, section, report_end


def choose_filing(payload, cik, form, report_end, now):
    rows = payload['filings']['recent']
    fields = ('form', 'reportDate', 'acceptanceDateTime', 'accessionNumber', 'primaryDocument')
    columns = [rows.get(name) for name in fields]
    if not all(isinstance(column, list) for column in columns) or len({len(column) for column in columns}) != 1:
        raise ValueError('invalid SEC submissions columns')
    candidates = []
    invalid_time = False
    for kind, period, accepted, accession, filename in zip(*columns):
        if kind != form or (report_end is not None and period != report_end):
            continue
        published = timestamp(accepted)
        if published <= 0:
            invalid_time = True
        elif published <= now:
            candidates.append((published, period, accession, filename))
    if not candidates:
        if invalid_time:
            raise ValueError('missing acceptance time')
        return None
    published, period, accession, filename = max(candidates, key=lambda row: row[0])
    if not valid_report_end(period) or period > datetime.fromtimestamp(published, timezone.utc).date().isoformat():
        raise ValueError('missing or invalid report period')
    if not isinstance(accession, str) or not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', accession):
        raise ValueError('invalid accession')
    if not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,175}\.html?', filename, re.IGNORECASE):
        raise ValueError('unsupported primary document name')
    return {'form': form, 'report_end': period, 'published': published, 'accession': accession,
            'url': f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace("-", "")}/{filename}'}


class FilingHTML(HTMLParser):
    BLOCKS = {'article', 'blockquote', 'br', 'div', 'h1', 'h2', 'h3', 'h4', 'hr', 'li', 'p', 'section'}
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
    SKIP = {'head', 'script', 'style', 'noscript', 'template', 'svg', 'table', 'ix:header', 'ix:hidden', 'ix:references', 'ix:resources'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = []
        self.link = False
        self.anchor = None
        self.anchors = Counter()
        self.parts = []
        self.unlinked = False
        self.blocks = []

    def flush(self):
        text = ' '.join(''.join(self.parts).split())
        if text and self.unlinked:
            self.blocks.append((text, self.anchor))
        if text:
            self.anchor = None
        self.parts.clear()
        self.unlinked = False

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        anchor = attrs.get('id') or (attrs.get('name') if tag == 'a' else None)
        if isinstance(anchor, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', anchor):
            self.anchors[anchor] += 1
        else:
            anchor = None
        if self.skip:
            if tag not in self.VOID:
                self.skip.append(tag)
            return
        style = attrs.get('style') or ''
        hidden = ('hidden' in attrs or attrs.get('aria-hidden')=='true'
                  or re.search(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\b', style, re.IGNORECASE))
        if tag in self.SKIP or hidden:
            if tag not in self.VOID:
                self.skip.append(tag)
            return
        if tag in self.BLOCKS or anchor:
            self.flush()
        if anchor:
            self.anchor = anchor
        if tag == 'a':
            self.link = bool(attrs.get('href'))

    def handle_endtag(self, tag):
        if self.skip:
            for index in range(len(self.skip)-1, -1, -1):
                if self.skip[index] == tag:
                    del self.skip[index:]
                    break
            return
        if tag in self.BLOCKS:
            self.flush()
        if tag == 'a':
            self.link = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)
            self.unlinked |= bool(data.strip()) and not self.link


def filing_passages(raw, filing, section):
    html = raw.decode('utf-8-sig')
    if not re.search(r'<(?:html|body)(?:\s|>)', html, re.IGNORECASE):
        raise ValueError('not an HTML primary document')
    reader = FilingHTML()
    reader.feed(html)
    reader.close()
    reader.flush()
    target = SECTION_IDS[filing['form']].get(section)
    part = item = anchor = None
    candidates = []
    for text, block_anchor in reader.blocks:
        if len(text) <= 220:
            part_heading = re.fullmatch(r'part\s+(i{1,3}|iv)(?:\s*[.―—–:-]\s*[^.]+)?', text, re.IGNORECASE)
            if part_heading:
                part, item, anchor = part_heading[1].upper(), None, None
                continue
            heading = re.match(r'^item\s+(\d{1,2}[a-z]?)\s*[.:—–-]\s*(?:[a-z]|\[|$)', text, re.IGNORECASE)
            if heading:
                item, anchor = heading[1].upper(), block_anchor
                continue
        if (part, item) == target and len(text) >= 24:
            location = block_anchor or anchor
            ordinal = len(candidates)+1
            passage = text[:1200].rstrip()
            identity = hashlib.sha256(f'{filing["accession"]}:{part}:{item}:{ordinal}:{passage}'.encode()).hexdigest()[:20]
            candidates.append({'id': identity, 'text': passage, 'ordinal': ordinal,
                               'section': f'Part {part} Item {item} · {SECTION_NAMES[section]}',
                               'anchor': location if reader.anchors[location] == 1 else None})
    hints = SECTION_HINTS[section]
    return sorted(candidates, key=lambda row: -sum(word in row['text'].lower() for word in hints))[:6]


def _failed_request(exc):
    if isinstance(exc, SourceTooLarge):
        return 'filing_too_large'
    if isinstance(exc, (Timeout, TimeoutError)):
        return 'source_timeout'
    if isinstance(exc, HTTPError) and exc.response is not None:
        return {429:'filing_rate_limited', 404:'filing_missing', 410:'filing_missing', 403:'filing_access_denied'}.get(exc.response.status_code, 'source_failed')
    return 'source_failed'


class FilingReader:
    def __init__(self, issuers, http):
        self.issuers = issuers
        self.http = http

    def read(self, symbol, form, section, report_end, now):
        filing_request(form, section, report_end)
        issuer = self.issuers.get(symbol, {})
        cik = str(issuer.get('cik', ''))
        if issuer.get('filing_body_reviewed') is not True or not re.fullmatch('[0-9]{1,10}', cik) or int(cik)==0:
            return {'status':'filing_not_reviewed'}
        if section not in SECTION_IDS[form]:
            return {'status':'filing_section_missing'}
        try:
            raw = self.http.read(f'https://data.sec.gov/submissions/CIK{int(cik):010d}.json', {'data.sec.gov'}, max_bytes=4*1024*1024)
        except (RequestException, TimeoutError, SourceTooLarge) as exc:
            return {'status':_failed_request(exc)}
        except (ValueError, PermissionError):
            return {'status':'source_failed'}
        try:
            filing = choose_filing(json.loads(raw), cik, form, report_end, now)
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            return {'status':'filing_metadata_invalid'}
        if filing is None:
            return {'status':'filing_missing'}
        result = {'symbol':symbol, 'filing':filing}
        try:
            raw = self.http.read(filing['url'], {'www.sec.gov'}, max_bytes=BODY_LIMIT, accept='text/html, application/xhtml+xml')
        except (RequestException, TimeoutError, SourceTooLarge) as exc:
            return {**result, 'status':_failed_request(exc)}
        except (ValueError, PermissionError):
            return {**result, 'status':'source_failed'}
        try:
            passages = filing_passages(raw, filing, section)
        except (ValueError, AssertionError):
            return {**result, 'status':'filing_parse_failed'}
        return {**result, 'status':'ok' if passages else 'filing_section_missing', 'passages':passages}


def validate_citations(citations, lookups):
    if not isinstance(citations, list) or not 1 <= len(citations) <= 3:
        raise ValueError('filing references required')
    passages = {p['id']: (row, p) for row in lookups for p in row.get('passages', [])}
    result = []
    seen = set()
    for citation in citations:
        if not isinstance(citation, dict) or set(citation) != {'id', 'quote'}:
            raise ValueError('invalid filing citation')
        identity, excerpt = citation['id'], citation['quote']
        if not isinstance(identity, str) or identity not in passages or identity in seen or not isinstance(excerpt, str) or not 8 <= len(excerpt.strip()) <= MAX_QUOTE:
            raise ValueError('invalid filing passage or excerpt')
        row, passage = passages[identity]
        if excerpt not in passage['text']:
            raise ValueError('filing excerpt does not match its passage')
        seen.add(identity)
        filing = row['filing']
        url = filing['url'] + ('#' + url_quote(passage['anchor'], safe='') if passage['anchor'] else '')
        result.append({**filing, 'url':url, 'symbol':row['symbol'], 'section':passage['section'],
                       'ordinal':passage['ordinal'], 'passage_id':identity, 'quote':excerpt})
    return result


def guard_summary(text, lookups):
    """Keep verbatim content in validated quote fields, away from saved summaries."""
    if not isinstance(text, str) or not text.strip() or len(text)>700 or re.search(r'https?://|www\.', text):
        raise ValueError('invalid filing summary')
    normalized = ' '.join(text.split()).casefold()
    for row in lookups:
        for passage in row.get('passages', []):
            body = passage['text'].casefold()
            span = min(48, len(body))
            if any(normalized[index:index+span] in body for index in range(len(normalized)-span+1)):
                raise ValueError('verbatim text outside filing citation')
    return text.strip()


def citation_lines(citations):
    lines = []
    for row in citations:
        accepted = datetime.fromtimestamp(row['published'], timezone.utc).isoformat(timespec='seconds')
        lines.append(f'{row["symbol"]} {row["form"]} · 期末 {row["report_end"]} · SEC 接收 {accepted} · 编号 {row["accession"]} · {row["section"]}')
        lines.append(f'核验原文：“{row["quote"]}”\n{row["url"]}')
    return lines


def filing_metadata_lines(lookups):
    lines = []
    for result in lookups:
        row = result['filing']
        accepted = datetime.fromtimestamp(row['published'], timezone.utc).isoformat(timespec='seconds')
        lines.append(f'申报信息：{result["symbol"]} {row["form"]} · 期末 {row["report_end"]} · SEC 接收 {accepted} · 编号 {row["accession"]}\n{row["url"]}')
    return list(dict.fromkeys(lines))
