import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import tempfile
from datetime import datetime,timezone,timedelta
import app
from integration import Publisher,PublishRejected

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        app.DATA=Path(self.tmp.name)
        app.DB=app.DATA/'test.sqlite3'
        app.init()
        with app.connect() as c:
            for i in range(3):
                c.execute('INSERT INTO accounts VALUES(?,?,?,?)',(str(i),'Brand '+str(i),'facebook',''))
    def tearDown(self):
        import gc
        gc.collect()
        try:
            self.tmp.cleanup()
        except Exception:
            pass
    def body(self,**kw):
        return dict(caption='Test content',account_ids=['0','1','2'],timezone='Asia/Karachi',scheduled_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat(),**kw)
    def due(self,pid):
        with app.connect() as c:c.execute('UPDATE posts SET scheduled_at=? WHERE id=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),pid))
    def statuses(self,pid):
        with app.connect() as c:return [r[0] for r in c.execute('SELECT status FROM deliveries WHERE post_id=?',(pid,))]
    def test_three_destinations_no_fake_success(self):
        pid=app.save_post(self.body());self.assertFalse(app.tick());self.due(pid)
        for _ in range(3):self.assertTrue(app.tick())
        self.assertEqual(self.statuses(pid),['blocked']*3);self.assertFalse(app.tick())
    def test_confirmed_success_only_claimed_once(self):
        class Success:
            def publish(self,**kw):return 'remote-'+kw['idempotency_key']
        pid=app.save_post(self.body());self.due(pid)
        for _ in range(3):app.tick(Success())
        self.assertFalse(app.tick(Success()));self.assertEqual(self.statuses(pid),['published']*3)
        with app.connect() as c:self.assertEqual(c.execute('SELECT sum(attempts) FROM deliveries').fetchone()[0],3)
    def test_draft_edit_and_schedule(self):
        body=self.body(draft=True);pid=app.save_post(body);self.assertFalse(app.tick())
        body['draft']=False;body['caption']='Updated caption';app.save_post(body,pid)
        with app.connect() as c:self.assertEqual(c.execute('SELECT caption FROM posts').fetchone()[0],'Updated caption')
        self.assertEqual(len(self.statuses(pid)),3)
    def test_invalid_selection_and_past_time(self):
        body=self.body();body['account_ids']=['0','0']
        with self.assertRaises(ValueError):app.save_post(body)
        body=self.body();body['scheduled_at']='2020-01-01T00:00:00Z'
        with self.assertRaises(ValueError):app.save_post(body)
    def test_instagram_requires_media(self):
        with app.connect() as c:c.execute("UPDATE accounts SET platform='instagram' WHERE id='0'")
        with self.assertRaises(ValueError):app.save_post(self.body())
    def test_edit_processing_rejected(self):
        pid=app.save_post(self.body());self.due(pid);app.tick()
        with self.assertRaises(ValueError):app.save_post(self.body(),pid)
    def test_cancelled_never_processed(self):
        pid=app.save_post(self.body());self.due(pid)
        with app.connect() as c:c.execute("UPDATE posts SET state='cancelled' WHERE id=?",(pid,))
        self.assertFalse(app.tick())
    def test_recovery_marks_inflight_unknown(self):
        pid=app.save_post(self.body())
        with app.connect() as c:c.execute("UPDATE deliveries SET status='publishing' WHERE post_id=?",(pid,))
        app.init();self.assertEqual(self.statuses(pid),['unknown']*3)
    def test_definite_rejection_failed(self):
        class Reject:
            def publish(self,**kw):raise PublishRejected('Invalid destination')
        pid=app.save_post(self.body());self.due(pid);app.tick(Reject());self.assertEqual(self.statuses(pid).count('failed'),1)
    def test_uncertain_outcome_never_retried(self):
        class Uncertain:
            def publish(self,**kw):raise TimeoutError("Connection lost")
        from unittest.mock import patch
        pid=app.save_post(self.body());self.due(pid)
        with patch("traceback.print_exc"):
            for _ in range(3):app.tick(Uncertain())
        self.assertEqual(self.statuses(pid),["unknown"]*3);self.assertFalse(app.tick(Uncertain()))
    def test_concurrent_claims(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        calls=[];lock=threading.Lock()
        class Success:
            def publish(self,**kw):
                with lock:calls.append(kw['idempotency_key'])
                return kw['idempotency_key']
        pid=app.save_post(self.body());self.due(pid)
        with ThreadPoolExecutor(max_workers=6) as pool:list(pool.map(lambda _:app.tick(Success()),range(6)))
        self.assertEqual(len(calls),3);self.assertEqual(len(set(calls)),3)

if __name__=='__main__':unittest.main()
