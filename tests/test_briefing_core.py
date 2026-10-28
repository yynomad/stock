import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from stockman.storage import Store
from stockman.briefing.models import SourcePolicy,Evidence,canonical_url
from stockman.briefing.history import History
from stockman.briefing.pipeline import collect,preview
from stockman.briefing.demo import SyntheticSource

class BriefingCoreTest(unittest.TestCase):
    def test_url_time_and_source_permissions(self):
        for url in ['file:///etc/passwd','http://example.com','https://user:password@example.com']:
            with self.assertRaises(ValueError):canonical_url(url)
        self.assertEqual(canonical_url('https://EXAMPLE.com/a?utm_source=x&id=2#top'),'https://example.com/a?id=2')
        item=SyntheticSource().fetch(1000)[0]
        for stamp in [True,float('nan'),None,0,2000]:
            with self.assertRaises((ValueError,TypeError)):replace(item,published=stamp).validate(1000)
        with self.assertRaises(PermissionError):SourcePolicy('x','https://example.com').validate()
    def test_source_failures_are_distinct_from_no_news(self):
        class Broken(SyntheticSource):
            def fetch(self,now):raise TimeoutError('never log this')
        items,policies,health=collect([Broken()],1000)
        self.assertEqual(items,[]);self.assertEqual(health[0]['status'],'failed');self.assertNotIn('never log this',str(health))
        class Empty(SyntheticSource):
            def fetch(self,now):return []
        self.assertEqual(collect([Empty()],1000)[2][0]['status'],'ok')
    def test_preview_does_not_suppress_later_delivery_and_body_not_saved(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db.sqlite')
            try:
                first=preview(store,[SyntheticSource()],now=1000)
                self.assertEqual(preview(store,[SyntheticSource()],now=1001)['item_count'],1)
                self.assertNotIn('body',json.loads(store.execute('SELECT metadata FROM brief_evidence')[0]['metadata']))
                history=History(store);history.deliver(first['report_id'],first,lambda x:'sent')
                self.assertEqual(preview(store,[SyntheticSource()],now=1002)['item_count'],0)
                self.assertEqual(preview(store,[SyntheticSource()],owner='other',now=1002)['item_count'],1)
            finally:store.close()
    def test_same_url_new_facts_remain_new(self):
        item=SyntheticSource().fetch(1000)[0]
        self.assertNotEqual(item.fingerprint,replace(item,body='New factual update 42').fingerprint)
    def test_concurrent_delivery_is_claimed_once_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db.sqlite');history=History(store)
            result=preview(store,[SyntheticSource()],now=1000);sent=[]
            def send(r):sent.append(r);return 'sent'
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda x:history.deliver(result['report_id'],result,send),range(4)))
            self.assertEqual(len(sent),1)
            store.close();store=Store(Path(d)/'db.sqlite')
            try:self.assertEqual(History(store).deliver(result['report_id'],result,send),'sent');self.assertEqual(len(sent),1)
            finally:store.close()
    def test_unknown_delivery_and_interruption_are_never_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(Path(d)/'db.sqlite');history=History(store)
            result=preview(store,[SyntheticSource()],now=1000)
            self.assertEqual(history.deliver(result['report_id'],result,lambda x:(_ for _ in ()).throw(TimeoutError())),'unknown')
            self.assertEqual(history.deliver(result['report_id'],result,lambda x:self.fail('replayed')),'unknown')
            result=preview(store,[SyntheticSource()],now=1000)
            store.execute("UPDATE brief_reports SET status='sending' WHERE id=?",(result['report_id'],));history.recover()
            self.assertEqual(history.deliver(result['report_id'],result,lambda x:self.fail('replayed')),'unknown')
            store.close()
