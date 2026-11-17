"""Cross-session access, origin restrictions, expiry and bounded uploads."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import main_api as api
from src.tools.session_manager import registry,cleanup

class SessionSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.patch=patch.object(api,'UPLOAD_DIR',self.root);self.patch.start()
        self.a=TestClient(api.app);self.a.__enter__()
        self.b=TestClient(api.app);self.b.__enter__()
    def tearDown(self):
        self.b.__exit__(None,None,None);self.a.__exit__(None,None,None);self.patch.stop();self.temp.cleanup()
    def upload(self):
        return self.a.post('/inspect',files={'file':('customers.csv',b'id,name\n001,A\n')}).json()
    def test_other_session_cannot_inspect_preview_clean_download_or_list_plans(self):
        data=self.upload();id=data['dataset_id'];rev=data['revision']
        self.assertEqual(self.b.post(f'/inspect/{id}',json={}).status_code,404)
        self.assertEqual(self.b.post(f'/datasets/{id}/preview',json={'revision':rev}).status_code,404)
        self.assertEqual(self.b.post(f'/datasets/{id}/analyze',json={'revision':rev}).status_code,404)
        self.assertEqual(self.b.get(f'/datasets/{id}/plans').status_code,404)
        self.assertEqual(self.b.post('/clean',json={'filename':data['filename'],'actions':[]}).status_code,404)
        self.assertEqual(self.b.get('/download/'+data['filename']).status_code,404)
        self.assertEqual(self.a.get('/download/'+data['filename']).status_code,200)
        cookie=self.a.get('/health').headers['set-cookie'].lower()
        self.assertIn('httponly',cookie);self.assertIn('samesite=strict',cookie)
    def test_expiry_deletes_artifacts_but_not_remembered_decisions(self):
        data=self.upload();state=self.root/'.decisions.sqlite3';state.write_bytes(b'keep')
        with registry(self.root) as db:
            db.execute('UPDATE artifacts SET expires=0')
        self.assertEqual(self.a.get('/download/'+data['filename']).status_code,404)
        cleanup(self.root)
        self.assertFalse((self.root/data['filename']).exists())
        self.assertFalse((self.root/f".{data['dataset_id']}.reading.json").exists())
        self.assertTrue(state.exists())
    def test_origin_upload_limit_hidden_files_and_shape_limit(self):
        self.assertEqual(self.a.post('/inspect',headers={'Origin':'https://evil.invalid'},files={'file':('x.csv',b'a\n1\n')}).status_code,403)
        with patch('src.tools.session_manager.MAX_UPLOAD_BYTES',4):
            self.assertEqual(self.a.post('/inspect',files={'file':('x.csv',b'a\n12345\n')}).status_code,413)
        self.assertEqual(self.a.get('/download/.artifacts.sqlite3').status_code,404)
        with patch('src.tools.session_manager.MAX_ROWS',1):
            response=self.a.post('/inspect',files={'file':('x.csv',b'a\n1\n2\n')})
            self.assertFalse(response.json()['valid'])
        allowed=self.a.options('/inspect',headers={'Origin':'http://127.0.0.1:5500','Access-Control-Request-Method':'POST'})
        self.assertEqual(allowed.headers['access-control-allow-origin'],'http://127.0.0.1:5500')
        self.assertEqual(allowed.headers['access-control-allow-credentials'],'true')
    def test_delete_removes_source_and_blocks_future_access(self):
        data=self.upload()
        self.assertEqual(self.a.delete('/datasets/'+data['dataset_id']).status_code,200)
        self.assertEqual(self.a.get('/download/'+data['filename']).status_code,404)
        self.assertFalse((self.root/data['filename']).exists())
    def test_legacy_analyze_clean_session_and_unique_filenames(self):
        contents=b'name,score\n A ,1\nB,3\nC,\n'
        first=self.a.post('/analyze',files={'file':('same.csv',contents)});self.assertEqual(first.status_code,200,first.text)
        second=self.b.post('/analyze',files={'file':('same.csv',contents)});self.assertEqual(second.status_code,200,second.text)
        self.assertNotEqual(first.json()['filename'],second.json()['filename'])
        cleaned=self.a.post('/clean',json={'filename':first.json()['filename'],'actions':[{'action':'fill_nulls','column':'score','strategy':'median'}]})
        self.assertEqual(cleaned.status_code,200,cleaned.text)
        self.assertEqual(cleaned.json()['preview'][2]['score'],2)
        self.assertEqual(self.b.get(cleaned.json()['download_url']).status_code,404)

    def test_active_upload_quota_also_applies_to_legacy_uploads(self):
        first=self.a.post('/analyze',files={'file':('a.csv',b'name,value\nA,1\n')})
        self.assertEqual(first.status_code,200,first.text)
        with registry(self.root) as db:
            owner=db.execute('SELECT owner FROM artifacts WHERE name=?',(first.json()['filename'],)).fetchone()[0]
            import time
            db.executemany('INSERT INTO artifacts VALUES (?,?,?)',[(f'{i:032x}.csv',owner,time.time()+3600) for i in range(19)])
        response=self.a.post('/inspect',files={'file':('b.csv',b'name,value\nB,2\n')})
        self.assertEqual(response.status_code,429,response.text)

    def test_busy_response_is_bounded(self):
        with patch.object(api,'_active_operations',2):
            self.assertEqual(self.a.post('/inspect',files={'file':('x.csv',b'a\n1\n')}).status_code,429)

if __name__=='__main__':unittest.main()
