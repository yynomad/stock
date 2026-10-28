"""Explicit source permissions and validated, ephemeral evidence."""
from dataclasses import dataclass,asdict
from urllib.parse import urlsplit,urlunsplit,parse_qsl,urlencode
import hashlib
import math
import re

def canonical_url(value):
    parts=urlsplit(value)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.port not in (None,443):
        raise ValueError('evidence requires a public HTTPS URL without credentials')
    query=[(k,v) for k,v in parse_qsl(parts.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('fbclid','gclid')]
    return urlunsplit(('https',parts.netloc.lower(),parts.path or '/',urlencode(sorted(query)),''))

@dataclass(frozen=True)
class SourcePolicy:
    source: str
    review_url: str
    reviewed: bool=False
    analyze: bool=False
    store_metadata: bool=False
    store_body: bool=False
    store_analysis: bool=False

    def validate(self):
        canonical_url(self.review_url)
        if not self.source or not all(type(v) is bool for v in [self.reviewed,self.analyze,self.store_metadata,self.store_body,self.store_analysis]):raise ValueError('invalid source policy')
        if not self.reviewed or not self.store_metadata:raise PermissionError('unreviewed source or metadata processing disabled')

@dataclass(frozen=True)
class Evidence:
    source: str
    title: str
    url: str
    published: float
    fetched: float
    body: str=''
    entities: tuple=()
    event_key: str=''
    occurred: float | None=None

    def validate(self,now):
        canonical_url(self.url)
        if not self.source or not isinstance(self.title,str) or not self.title.strip() or len(self.title)>2000 or not isinstance(self.body,str) or len(self.body)>200000:raise ValueError('invalid evidence text')
        for stamp in [self.published,self.fetched]+([] if self.occurred is None else [self.occurred]):
            if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or stamp<=0 or stamp>now+30:raise ValueError('missing or invalid evidence timestamp')
        if not all(isinstance(x,str) and x for x in self.entities):raise ValueError('invalid entities')

    @property
    def fingerprint(self):
        # Never suppress an update just because it reuses the previous title or URL.
        content=re.sub(r'\s+',' ',self.body).strip().casefold()
        if not content:content=canonical_url(self.url)+' '+self.title.strip()
        # Identical boilerplate about different companies is not the same event.
        return hashlib.sha256((repr(sorted(self.entities))+' '+content).encode()).hexdigest()

    @property
    def identity(self):return hashlib.sha256((self.source+canonical_url(self.url)+self.fingerprint).encode()).hexdigest()[:24]

    def metadata(self,policy):
        result=asdict(self)
        if not policy.store_body:result.pop('body')
        return result
