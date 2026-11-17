"""Safety, bounded audit, admission control and explicit demo limits."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from threading import Event
from unittest.mock import patch
import json
import os
import subprocess
import sys
import time

import polars as pl
import pytest
from fastapi.testclient import TestClient
import main_api as api
from src.tools.cleaning_engine import CleaningEngine
from src.tools.tabular_io import conversion_review, read_with_options, TabularReadError
from src.tools.session_manager import OWNER, new_token, session_owner


def test_mixed_invoice_conversion_is_never_auto_selected_or_lossy(tmp_path):
    values = [str(i) for i in range(98)] + ['C123','C124']
    frame = pl.DataFrame({'Invoice': values})
    review = conversion_review(frame['Invoice'],'Invoice','coerce_numeric')
    assert review['blocked_count'] == 2 and review['examples'] == ['C123','C124']
    engine = CleaningEngine('unused');engine.df=frame
    with pytest.raises(ValueError,match='Conversion blocked'):
        engine.coerce_numeric('Invoice')
    assert engine.df.equals(frame)
    with patch.object(api,'UPLOAD_DIR',tmp_path),TestClient(api.app) as client:
        response=client.post('/analyze',files={'file':('invoices.csv',frame.write_csv().encode())})
        assert response.status_code==200
        data=response.json();step=next(s for s in data['cleaning_plan'] if s['action']=='coerce_numeric')
        assert not step['auto_eligible'] and not step['executable']
        assert step['conversion_review']['blocked_count']==2
        response=client.post('/clean',json={'filename':data['filename'],'actions':[{'action':'coerce_numeric','column':'Invoice'}]})
        assert response.status_code==422
        assert not list(tmp_path.glob('*.audit.pending.csv'))
        assert not list(tmp_path.glob('cleaned_*'))
        assert read_with_options(tmp_path/data['filename'])[0].equals(frame)


@pytest.mark.parametrize('name,values',[('value',['001','002']),('value',['12345678901234567']),('customer_id',['12','13']),('phone',['123','456']),('value',['NaN','inf'])])
def test_numeric_information_is_protected(name,values):
    frame=pl.DataFrame({name:values});engine=CleaningEngine('unused');engine.df=frame
    with pytest.raises(ValueError,match='Conversion blocked'):
        engine.coerce_numeric(name)
    assert engine.df.equals(frame)


def test_safe_numeric_conversion_preserves_existing_missing_values():
    engine=CleaningEngine('unused');engine.df=pl.DataFrame({'amount':['1',' 2.5 ',None]})
    engine.coerce_numeric('amount')
    assert engine.df['amount'].to_list()==[1.0,2.5,None]
    assert engine.change_totals['cleared']==0


@pytest.mark.parametrize('value,fmt',[('2025-01-01 12:30:00','%Y-%m-%d %H:%M:%S'),('2025-01-01T12:30:00Z',None),('01/02/2025',None)])
def test_date_conversion_never_discards_time_or_guesses_ambiguous_order(value,fmt):
    engine=CleaningEngine('unused');engine.df=pl.DataFrame({'release_date':[value]})
    with pytest.raises(ValueError,match='Conversion blocked'):
        engine.parse_date('release_date',fmt)
    assert engine.df['release_date'][0]==value


def test_disk_audit_is_complete_but_python_sample_is_bounded(tmp_path):
    frame=pl.DataFrame({'name':[' A ']*9000+[' B '], 'score':[None]*9000+[2]})
    engines=[CleaningEngine('unused'),CleaningEngine('unused',tmp_path/'audit.csv')]
    for engine in engines:
        engine.df=frame
        engine.trim_whitespace(['name']);engine.fill_nulls('score','median');engine.drop_duplicates()
    memory,disk=engines
    assert disk.df.equals(memory.df)
    assert disk.get_lineage_df().equals(memory.get_lineage_df())
    assert disk.log_entries==len(memory.change_log) and len(disk.change_log)<=20
    assert disk.get_lineage_summary()==memory.get_lineage_summary()
    assert disk.outcome_summary()==memory.outcome_summary()
    assert disk.preview_audit({9001})==[r for r in memory.change_log if r['source_row_id']==9001]


def test_streaming_csv_retains_quotes_multiline_and_null_semantics(tmp_path):
    path=tmp_path/'quoted.csv';path.write_bytes(b'id,text,empty\n1,"a\nb",""\n\n2,,\n')
    frame,info=read_with_options(path)
    assert frame['text'].to_list()==['a\nb',None]
    assert frame['empty'].to_list()==['',None]
    assert info['blank_records_ignored']==1


def test_row_limit_rejects_before_polars_allocation(tmp_path):
    path=tmp_path/'rows.csv';path.write_text('x\n1\n2\n3\n')
    with patch('src.tools.tabular_io.MAX_ROWS',2),patch('src.tools.tabular_io.pl.read_csv',side_effect=AssertionError('must not allocate')):
        with pytest.raises(TabularReadError,match='row limit'):
            read_with_options(path,{'delimiter':','})


def test_busy_admission_is_immediate_and_health_remains_responsive(tmp_path):
    entered,release=Event(),Event();owners=[]
    def slow(*args):
        owners.append(OWNER.get());entered.set()
        assert release.wait(5)
        return {'status':'success'}
    with patch.object(api,'UPLOAD_DIR',tmp_path),patch.object(api,'MAX_OPERATIONS',1),patch.object(api,'_inspect_source',slow),TestClient(api.app) as client,ThreadPoolExecutor() as executor:
        client.get('/health');owner=session_owner(client.cookies['data_clean_session'])
        future=executor.submit(client.post,'/inspect',files={'file':('test.csv',b'x\n1\n')})
        try:
            assert entered.wait(3)
            started=time.monotonic();assert client.get('/health').status_code==200
            assert time.monotonic()-started<1
            busy=client.post('/inspect',headers={'Cookie':f'data_clean_session={new_token()}','Origin':'http://127.0.0.1:5500'},files={'file':('other.csv',b'x\n2\n')})
            assert busy.status_code==429 and busy.json()['code']=='server_busy'
            assert busy.headers['retry-after']=='3'
            assert busy.headers['access-control-allow-origin']=='http://127.0.0.1:5500'
            assert len(owners)==1  # Rejected work never enters the processor.
        finally:
            release.set()
        assert future.result().status_code==200
        assert owners==[owner]  # Session capability survives thread offloading.
        assert client.post('/inspect',files={'file':('again.csv',b'x\n3\n')}).status_code==200
        assert api._active_operations==0 and not api._active_owners


def test_failed_worker_releases_admission(tmp_path):
    with patch.object(api,'UPLOAD_DIR',tmp_path),patch.object(api,'MAX_OPERATIONS',1),TestClient(api.app) as client:
        result=client.post('/inspect',files={'file':('bad.csv',b'x,y\n1,2,3\n')})
        assert result.status_code==200 and result.json()['valid'] is False
        assert client.post('/inspect',files={'file':('ok.csv',b'x,y\n1,2\n')}).json()['valid'] is True
        assert api._active_operations==0


def test_declared_and_chunked_body_limit_are_enforced(tmp_path):
    with patch.object(api,'UPLOAD_DIR',tmp_path),patch.object(api,'MAX_UPLOAD_BYTES',1),TestClient(api.app) as client:
        huge=b'x'*(1024*1024+10)
        assert client.post('/inspect',files={'file':('big.csv',huge)}).status_code==413
        body=b'--test\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\nContent-Type: text/csv\r\n\r\n'+huge+b'\r\n--test--\r\n'
        response=client.post('/inspect',content=iter([body[:100],body[100:]]),headers={'Content-Type':'multipart/form-data; boundary=test'})
        assert response.status_code==413
        assert api._active_operations==0


def test_demo_profile_does_not_reduce_local_capacity():
    for profile,rows,operations in [('local',2_000_000,2),('demo',25_000,1)]:
        env={k:v for k,v in os.environ.items() if not k.startswith('DATA_CLEAN_')}
        result=subprocess.run([sys.executable,'-c','import json;from src.tools.runtime_limits import public_limits;print(json.dumps(public_limits()))'],env={**env,'DATA_CLEAN_PROFILE':profile},capture_output=True,text=True,check=True)
        limits=json.loads(result.stdout)
        assert limits['profile']==profile
        assert limits['max_rows']==rows and limits['max_operations']==operations
        assert limits['max_upload_bytes']==(100 if profile=='local' else 5)*1024*1024
        assert limits['max_columns']==(500 if profile=='local' else 50)
        assert limits['file_ttl_seconds']==(24 if profile=='local' else 1)*3600
        assert limits['queue_enabled'] is False


def test_hosted_ui_uses_same_origin_without_exposing_source_or_storage(tmp_path):
    with patch.object(api,'UPLOAD_DIR',tmp_path),TestClient(api.app) as client:
        response=client.get('/')
        assert response.status_code==200 and 'window.DATA_CLEAN_API_BASE=location.origin' in response.text
        assert client.get('/assets/samples/demo.csv').status_code==200
        assert client.get('/src/tools/session_manager.py').status_code==404
        limits=client.get('/limits').json()['limits']
        assert limits['max_upload_bytes']>0 and limits['queue_enabled'] is False
