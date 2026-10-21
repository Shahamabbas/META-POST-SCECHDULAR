import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app
import base64,json,tempfile,threading,unittest,urllib.request,urllib.error,http.cookiejar
from datetime import datetime,timezone,timedelta

class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();app.DATA=Path(self.tmp.name);app.DB=app.DATA/'db';app.init();app.PASSWORD='test-password-long';app.SESSIONS.clear()
        self.server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf=''
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()
    def call(self,path,body=None,csrf=True):
        headers={'Content-Type':'application/json'}
        if csrf:headers['X-CSRF-Token']=self.csrf
        r=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with self.client.open(r) as response:return response.status,json.loads(response.read())
        except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
    def test_authenticated_end_to_end(self):
        self.assertEqual(self.call('/api/state')[0],401)
        self.assertEqual(self.call('/api/login',{'password':'wrong'})[0],401)
        self.assertEqual(self.call('/api/login',{'password':app.PASSWORD})[0],200)
        self.csrf=self.call('/api/state')[1]['csrf']
        self.assertEqual(self.call('/api/accounts',{'name':'X','platform':'facebook'},csrf=False)[0],403)
        for platform in ('facebook','instagram','facebook'):
            self.assertEqual(self.call('/api/accounts',{'name':'Brand','platform':platform})[0],200)
        self.assertEqual(self.call('/api/accounts',{'name':'Fourth','platform':'facebook'})[0],400)
        code,media=self.call('/api/media',{'name':'test.png','data':base64.b64encode(b'\x89PNG\r\n\x1a\nminimal-test').decode()});self.assertEqual(code,200)
        with self.client.open(self.base+'/media/'+media['id']) as r:self.assertEqual(r.status,200)
        accounts=self.call('/api/state')[1]['accounts']
        self.assertEqual(self.call('/api/accounts',{'id':accounts[0]['id'],'name':'Updated Brand','platform':accounts[0]['platform'],'external_id':'12345'})[0],200)
        body=dict(caption='A scheduled post',account_ids=[a['id'] for a in accounts],media_id=media['id'],timezone='UTC',draft=False,scheduled_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())
        code,result=self.call('/api/posts',body);self.assertEqual(code,200)
        pid=result['id'];self.assertEqual(self.call('/api/posts/cancel',{'id':pid})[0],200)
        self.assertEqual(self.call('/api/accounts/delete',{'id':accounts[0]['id']})[0],400)
        self.assertEqual(self.call('/api/logout',{})[0],200);self.assertEqual(self.call('/api/state')[0],401)

if __name__=='__main__':unittest.main()
