"""Stable record identities, deletion audit, typed exports and UI result contracts."""
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import polars as pl
from fastapi.testclient import TestClient
import main_api as api
from src.tools.cleaning_engine import CleaningEngine
from src.tools.tabular_io import write_tabular, verify_export


class StableTraceTests(unittest.TestCase):
    def test_original_record_ids_survive_all_row_deletions_and_later_edits(self):
        engine=CleaningEngine('unused.csv')
        engine.df=pl.DataFrame({'name':[' A ',' A ',' B ',' C ',' D '],
                                'flag':['ok','ok',None,'ok','ok'], 'score':[1,1,None,None,100]})
        self.assertEqual(engine.drop_duplicates(),1)
        self.assertEqual(engine.fill_nulls('flag','drop'),1)
        self.assertEqual(engine.handle_outliers('score','drop',0,10),1)
        self.assertEqual(engine.fill_nulls('score','median'),1)
        engine.trim_whitespace(['name'])
        self.assertEqual(engine.source_row_ids(),[1,4])
        removed=[r for r in engine.change_log if r['change_kind']=='removed']
        self.assertEqual([r['source_row_id'] for r in removed],[2,3,5])
        self.assertIn('"score": 100',removed[-1]['old_value'])
        filled=next(r for r in engine.change_log if r['change_kind']=='filled')
        self.assertEqual(filled['source_row_id'],4);self.assertEqual(filled['row_index'],'3')
        self.assertEqual(filled['method'],'fill_nulls_median');self.assertTrue(filled['old_is_null'])
        self.assertEqual(engine.row_mapping().to_dicts(),[{'output_row':1,'source_row_id':1},{'output_row':2,'source_row_id':4}])
        self.assertEqual(engine.df.columns,['name','flag','score'])
        self.assertEqual(engine.outcome_summary()['removed'],3)

    def test_null_and_empty_text_are_distinct_in_audit(self):
        engine=CleaningEngine('unused.csv');engine.df=pl.DataFrame({'text':[None,'', 'NULL']})
        engine.fill_nulls('text','constant','')
        record=engine.change_log[0]
        self.assertEqual(record['old_value'],record['new_value'])
        self.assertTrue(record['old_is_null']);self.assertFalse(record['new_is_null'])
        self.assertEqual(record['change_kind'],'filled')
        self.assertEqual(engine.get_lineage_df()['source_row_id'].to_list(),[1])

    def test_preview_bounds_log_but_keeps_exact_operation_totals(self):
        engine=CleaningEngine('unused.csv');engine.df=pl.DataFrame({'x':[' A ']*100})
        engine.lineage_limit=5
        self.assertEqual(engine.trim_whitespace(['x']),100)
        self.assertEqual(engine.outcome_summary()['modified'],100)
        self.assertEqual(engine.drop_duplicates(),99)
        self.assertEqual(engine.outcome_summary()['removed'],99)
        self.assertEqual(len(engine.change_log),5)
        self.assertEqual(engine.source_row_ids(),[1])

    def test_empty_dataset_and_colliding_user_column_names(self):
        engine=CleaningEngine('unused.csv');engine.df=pl.DataFrame({'source_row_id':['0001','0002'],'row_index':[None,None]})
        engine.fill_nulls('row_index','drop')
        self.assertEqual(engine.row_mapping().height,0)
        self.assertEqual(engine.df.columns,['source_row_id','row_index'])
        self.assertEqual([r['source_row_id'] for r in engine.change_log],[1,2])


class ExportIntegrityTests(unittest.TestCase):
    def test_csv_roundtrip_preserves_identifiers_arabic_dates_and_empty_text(self):
        frame=pl.DataFrame({'id':['0001','0002'], 'name':['الرياض',''], 'date':[date(2026,1,1),None],
                            'text':[None,''], 'decimal':[1.25,2.5], 'empty':[None,None]})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'clean.csv';write_tabular(frame,path)
            report=verify_export(frame,path)
            self.assertEqual(report['status'],'PASS');self.assertEqual(report['rows_checked'],2)
            self.assertIn('0001',path.read_text(encoding='utf-8'))
            path.write_text(path.read_text(encoding='utf-8').replace('0001','0009'),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'values changed'):
                verify_export(frame,path)

    def test_csv_time_and_timezone_roundtrip(self):
        from datetime import time
        frame=pl.DataFrame({'timestamp':[datetime(2026,1,1,12,30,0,123456,tzinfo=timezone.utc),None],
                            'time':[time(12,30,0,123456),None]})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'temporal.csv';write_tabular(frame,path)
            self.assertEqual(verify_export(frame,path)['status'],'PASS')

    def test_excel_keeps_text_literal_and_precision_sensitive_values(self):
        from openpyxl import load_workbook
        frame=pl.DataFrame({'=header':['=1+1','0001'], 'arabic':['الرياض','جدة'],
                            'big':[1234567890123456789,1234567890123456788],
                            'date':[datetime(2026,1,1,tzinfo=timezone.utc),None]})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'clean.xlsx';write_tabular(frame,path)
            self.assertEqual(verify_export(frame,path)['status'],'PASS')
            book=load_workbook(path);sheet=book.active
            self.assertEqual(sheet['A1'].data_type,'s');self.assertEqual(sheet['A2'].data_type,'s')
            self.assertEqual(sheet['A2'].value,'=1+1');self.assertEqual(sheet['A3'].value,'0001')
            self.assertEqual(sheet['C2'].value,'1234567890123456789');book.close()

    def test_zero_row_csv_and_excel(self):
        frame=pl.DataFrame(schema={'id':pl.String,'date':pl.Date})
        with tempfile.TemporaryDirectory() as d:
            for extension in ['csv','xlsx']:
                path=Path(d)/f'empty.{extension}';write_tabular(frame,path)
                self.assertEqual(verify_export(frame,path)['rows_checked'],0)


class TraceApiTests(unittest.TestCase):
    def test_downloads_mapping_annotations_and_schema_agree_with_output(self):
        with tempfile.TemporaryDirectory() as d, patch.object(api,'UPLOAD_DIR',Path(d)), TestClient(api.app) as client:
            source=b'name,flag,score\n A ,ok,1\n A ,ok,1\n B ,,\n C ,ok,\n D ,ok,100\n'
            inspected=client.post('/inspect',files={'file':('trace.csv',source)}).json()
            id,rev=inspected['dataset_id'],inspected['revision']
            self.assertEqual(client.post(f'/datasets/{id}/analyze',json={'revision':rev}).status_code,200)
            actions=[{'action':'drop_duplicates','column':'dataset'},{'action':'fill_nulls','column':'flag','strategy':'drop'},
                     {'action':'handle_outliers','column':'score','strategy':'drop','lower_bound':0,'upper_bound':10},
                     {'action':'fill_nulls','column':'score','strategy':'median'},{'action':'trim_whitespace','column':'name'}]
            dry=client.post(f'/datasets/{id}/effects',json={'revision':rev,'actions':actions});self.assertEqual(dry.status_code,200,dry.text)
            self.assertEqual(dry.json()['preview_source_rows'],[1,4])
            response=client.post('/clean',json={'filename':inspected['filename'],'dataset_id':id,'reading_revision':rev,'actions':actions})
            self.assertEqual(response.status_code,200,response.text);result=response.json()
            self.assertEqual(result['preview_source_rows'],[1,4]);self.assertEqual(result['outcomes']['filled'],1)
            self.assertTrue(any(a['source_row_id']==4 and a['change_kind']=='filled' for a in result['preview_annotations']))
            mapping=client.get(result['row_mapping_download_url']).text
            self.assertEqual(mapping.strip(),'output_row,source_row_id\n1,1\n2,4')
            manifest=client.get(result['schema_download_url']).json()
            self.assertEqual(manifest['schema']['score'],result['after_profile']['schema']['score'])
            self.assertEqual(result['validation']['export']['status'],'PASS')
            self.assertEqual((Path(d)/inspected['filename']).read_bytes(),source)
            self.assertNotIn('source_row_id',client.get(result['download_url']).text.splitlines()[0])
            self.assertEqual(client.delete(f'/datasets/{id}').status_code,200)
            self.assertEqual(client.get(result['row_mapping_download_url']).status_code,404)
            self.assertEqual(client.get(result['schema_download_url']).status_code,404)

if __name__=='__main__':unittest.main()
