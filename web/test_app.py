import json, threading, time, unittest
from urllib import request
import app

class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=app.ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.port=cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever,daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.server.shutdown()
    def fetch(self,path,method="GET",data=None):
        req=request.Request(f"http://127.0.0.1:{self.port}{path}",method=method,data=None if data is None else json.dumps(data).encode(),headers={"Content-Type":"application/json"})
        with request.urlopen(req,timeout=2) as r:return r.status,json.loads(r.read())
    def test_health(self): self.assertEqual(self.fetch('/api/health')[1]['ok'],True)
    def test_create_job(self):
        target=(app.datetime.now().astimezone()+app.timedelta(seconds=2)).strftime('%H:%M:%S.%f')[:-3]
        status,job=self.fetch('/api/jobs','POST',{'targetTime':target,'attempts':1,'intervalSeconds':1})
        self.assertEqual(status,201);self.assertIn(job['id'],app.jobs)
    def test_static_index(self):
        with request.urlopen(f"http://127.0.0.1:{self.port}/",timeout=2) as r:
            page=r.read().decode()
            self.assertIn('定时领券助手',page)
            self.assertIn('导入登录态',page)
            self.assertIn('harFile',page)
    def test_anonymous_analytics_and_feedback(self):
        install_id='12345678-1234-1234-1234-123456789abc'
        status,result=self.fetch('/api/analytics/events','POST',{
            'install_id':install_id,'event':'first_launch','app_version':'2.4.3',
            'android_version':'15','channel':'test','outcome':''})
        self.assertIn(status,(200,201));self.assertTrue(result['ok'])
        status,result=self.fetch('/api/analytics/feedback','POST',{
            'install_id':install_id,'app_version':'2.4.3','message':'测试反馈'})
        self.assertEqual(status,201);self.assertTrue(result['ok'])
        status,summary=self.fetch('/api/analytics/summary')
        self.assertEqual(status,200);self.assertGreaterEqual(summary['installs'],1)
        self.assertGreaterEqual(summary['feedbackCount'],1)
    def test_download_is_counted(self):
        with request.urlopen(f"http://127.0.0.1:{self.port}/download?from=test",timeout=2) as r:
            self.assertEqual(r.status,200);self.assertGreater(len(r.read()),1000)
        self.assertGreaterEqual(self.fetch('/api/analytics/summary')[1]['downloads'],1)

if __name__=='__main__': unittest.main()
