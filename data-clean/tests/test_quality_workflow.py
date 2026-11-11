"""Stage 1 contracts: strict interpretation, bounded previews and reuse in cleaning."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
import pandas as pd
import polars as pl

import main_api as api
from src.tools.dataset_preview import dataset_preview
from src.tools.tabular_io import TabularReadError, read_with_options, read_tabular


class FileReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, text, name="sample.csv", encoding="utf-8"):
        path = self.root / name
        path.write_text(text, encoding=encoding, newline="")
        return path

    def test_delimiters_and_identifier_preservation(self):
        for sep in [",", ";", "\t", "|"]:
            with self.subTest(sep=sep):
                path = self.source(sep.join(["customer_id", "phone", "postal_code", "score", "date"]) + "\n" + sep.join(["0001", "0501234567", "00123", "8", "03/04/2026"]) + "\n")
                frame, info = read_with_options(path)
                self.assertEqual(info["reading_options"]["delimiter"], sep)
                self.assertEqual(frame["customer_id"][0], "0001")
                self.assertEqual(frame["phone"][0], "0501234567")
                self.assertEqual(frame["postal_code"][0], "00123")
                self.assertEqual(frame["date"][0], "03/04/2026")
                self.assertEqual(frame["score"][0], 8)

    def test_header_bom_arabic_quotes_and_multiline(self):
        path = self.source('title;note\r\n"فيلم عربي";"a;b\nsecond line"\r\n', encoding="utf-8-sig")
        frame, _ = read_with_options(path)
        self.assertEqual(frame.columns, ["title", "note"])
        self.assertEqual(frame["title"][0], "فيلم عربي")
        self.assertEqual(frame["note"][0], "a;b\nsecond line")

    def test_utf16_and_explicit_arabic_encoding(self):
        for enc in ["utf-16", "cp1256"]:
            path = self.source('name;value\nالرياض;5\n', encoding=enc)
            frame, info = read_with_options(path, {"encoding": enc})
            self.assertEqual(frame["name"][0], "الرياض")

    def test_selected_header_row_and_wrong_delimiter(self):
        path = self.source("Report\nname;value\nAlice;1\nBob;2\n")
        frame, info = read_with_options(path, {"header_row": 2})
        self.assertEqual(frame.shape, (2, 2))
        wrong, warning = read_with_options(self.source('name;value\nAlice;1\n'), {"delimiter": ","})
        self.assertEqual(wrong.width, 1)
        self.assertTrue(any("one column" in text for text in warning["warnings"]))

    def test_malformed_width_short_or_long_reports_line(self):
        for record in ["1,2,3", "1"]:
            path = self.source(f"a,b\n{record}\n")
            with self.assertRaises(TabularReadError) as caught:
                read_with_options(path, {"delimiter": ","})
            self.assertEqual(caught.exception.issues[0]["line"], 2)

    def test_bad_quoting_empty_headers_and_no_data(self):
        for text in ['', ' \n', 'a,b\n', 'a,a\n1,2\n', ',b\n1,2\n', 'a,b\n"unclosed,2\n']:
            with self.subTest(text=text):
                with self.assertRaises(TabularReadError):
                    read_with_options(self.source(text), {"delimiter": ","})

    def test_wrong_encoding_fails_without_replacement(self):
        path = self.source('name\nالرياض\n', encoding='cp1256')
        with self.assertRaises(TabularReadError):
            read_with_options(path, {"encoding": "utf-8"})

    def test_blanks_are_distinct_from_quoted_empty_and_whitespace(self):
        frame, info = read_with_options(self.source('value,other\n,x\n"",x\n ,x\n\n'), {"delimiter": ","})
        self.assertEqual(frame["value"].to_list(), [None, "", " "])
        self.assertEqual(info["blank_records_ignored"], 1)
        preview = dataset_preview(frame)
        self.assertEqual(preview["schema"][0]["missing"], 1)
        self.assertEqual(preview["schema"][0]["empty_strings"], 1)

    def test_precision_sensitive_and_mixed_values_stay_text(self):
        frame, _ = read_with_options(self.source('long_value,decimal_value,mixed,leading\n12345678901234567890,1.1234567890123456789,1,01\n12345678901234567891,2.0,text,02\n'))
        self.assertTrue(all(dtype == pl.String for dtype in frame.dtypes))
        self.assertEqual(frame["long_value"][0], '12345678901234567890')

    def test_excel_worksheet_header_and_text_identifiers(self):
        path = self.root / 'book.xlsx'
        with pd.ExcelWriter(path, engine='openpyxl') as writer:
            pd.DataFrame([['wrong'],['data']]).to_excel(writer, sheet_name='Cover', header=False, index=False)
            pd.DataFrame([['Report',None],['postal_code','name'],['00123','الرياض']]).to_excel(writer, sheet_name='Data', header=False, index=False)
        frame, info = read_with_options(path, {'sheet_name':'Data','header_row':2})
        self.assertEqual(info['worksheets'], ['Cover','Data'])
        self.assertEqual(frame['postal_code'][0], '00123')
        with self.assertRaises(TabularReadError):
            read_with_options(path, {'sheet_name':'Missing'})

    def test_preview_search_paging_sorting_missing_and_columns(self):
        frame = pl.DataFrame({'id':[f'{i:04}' for i in range(120)],'score':list(range(119))+[None],
                              'name':['ordinary']*100+['unique late value']+['ordinary']*19})
        self.assertEqual(dataset_preview(frame)['shown_rows'], 50)
        second = dataset_preview(frame, offset=50)
        self.assertEqual(second['row_numbers'][0], 51)
        found = dataset_preview(frame, query='late value', columns=['id'])
        self.assertEqual(found['rows'], [{'id':'0100'}])
        self.assertEqual(found['row_numbers'], [101])
        sorted_rows = dataset_preview(frame,sort_column='score',descending=True)
        self.assertEqual(sorted_rows['rows'][0]['score'],118)
        self.assertEqual(dataset_preview(frame,missing_column='score')['matched_rows'],1)
        self.assertEqual(dataset_preview(frame,query='absent')['shown_rows'],0)
        self.assertEqual(dataset_preview(frame,query='[')['matched_rows'],0)
        for options in [{'columns':[]},{'columns':['absent']},{'sort_column':'absent'},{'limit':201},{'offset':-1}]:
            with self.assertRaises(ValueError): dataset_preview(frame,**options)


class ConfirmedReadingApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(api,'UPLOAD_DIR',self.root)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    async def upload(self, contents=b'id;phone;name;score\n001;0500000001; Alice ;8\n002;0500000002;Bob;\n'):
        return await api.inspect_upload(UploadFile(filename='customers.csv',file=BytesIO(contents)))

    async def test_inspect_analyze_clean_uses_confirmed_cache(self):
        inspected = await self.upload()
        self.assertTrue(inspected['valid'])
        self.assertEqual(inspected['preview']['total_rows'],2)
        self.assertNotEqual(inspected['filename'],'customers.csv')
        source = self.root / inspected['filename']
        original = source.read_bytes()
        analyzed = await api.analyze_inspected(inspected['dataset_id'],api.InspectionConfirmation(revision=inspected['revision']))
        self.assertEqual(analyzed['analysis']['schema']['phone'],'String')
        risky_phone = [step for step in analyzed['cleaning_plan'] if step['column']=='phone' and step['action']=='coerce_numeric']
        self.assertTrue(all(step['risk']=='HIGH IMPACT' and not step['auto_eligible'] for step in risky_phone))
        result = await api.clean_data(api.CleaningRequest(filename=analyzed['filename'],dataset_id=inspected['dataset_id'],
            reading_revision=inspected['revision'],actions=[api.CleaningAction(action='trim_whitespace',column='name')]))
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['preview'][0]['phone'],'0500000001')
        self.assertEqual(result['preview'][0]['name'],'Alice')
        self.assertEqual(source.read_bytes(), original)
        output = self.root/result['cleaned_file']
        self.assertEqual(read_tabular(output)['phone'][0],'0500000001')

    async def test_changed_options_invalidate_analysis_and_cleaning(self):
        old = await self.upload()
        await api.analyze_inspected(old['dataset_id'],api.InspectionConfirmation(revision=old['revision']))
        new = await api.update_inspection(old['dataset_id'],api.ReadingOptions(delimiter=','))
        self.assertTrue(new['valid'])
        self.assertEqual(new['preview']['columns'],['id;phone;name;score'])
        with self.assertRaises(HTTPException) as caught:
            await api.clean_data(api.CleaningRequest(filename=old['filename'],dataset_id=old['dataset_id'],reading_revision=old['revision']))
        self.assertEqual(caught.exception.status_code,409)
        with self.assertRaises(HTTPException):
            await api.analyze_inspected(old['dataset_id'],api.InspectionConfirmation(revision=old['revision']))
        with self.assertRaises(HTTPException):
            await api.query_dataset_preview(old['dataset_id'],api.PreviewQuery(revision=old['revision']))

    async def test_failed_read_keeps_source_for_correction(self):
        result = await self.upload(b'Report,metadata\nid;name\n001;Alice\n')
        self.assertFalse(result['valid'])
        fixed = await api.update_inspection(result['dataset_id'],api.ReadingOptions(header_row=2,delimiter=';'))
        self.assertTrue(fixed['valid'])
        self.assertEqual(fixed['preview']['rows'][0]['id'],'001')

    async def test_preview_endpoint_full_dataset_and_invalid_identifiers(self):
        data = await self.upload(b'name,value\n'+b''.join(f'row{i},{i}\n'.encode() for i in range(100)))
        page = await api.query_dataset_preview(data['dataset_id'],api.PreviewQuery(revision=data['revision'],query='row99'))
        self.assertEqual(page['matched_rows'],1)
        self.assertEqual(page['row_numbers'],[100])
        with self.assertRaises(HTTPException):
            await api.query_dataset_preview('../source',api.PreviewQuery(revision='x'))


class ReadingHttpTests(unittest.TestCase):
    def test_multipart_confirmation_and_validation_over_http(self):
        from fastapi.testclient import TestClient
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'UPLOAD_DIR', Path(directory)), TestClient(api.app) as client:
            response = client.post('/inspect', files={'file':('sample.csv', b'id;name\n001;Alice\n', 'text/csv')})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            dataset_id, revision = data['dataset_id'], data['revision']
            self.assertTrue(data['valid'])
            invalid = client.post(f'/datasets/{dataset_id}/preview', json={'revision':revision,'limit':201})
            self.assertEqual(invalid.status_code, 422)
            analyzed = client.post(f'/datasets/{dataset_id}/analyze', json={'revision':revision})
            self.assertEqual(analyzed.status_code, 200)
            self.assertEqual(analyzed.json()['reading_revision'], revision)
            clean = client.post('/clean', json={'filename':data['filename'],'dataset_id':dataset_id,'reading_revision':revision,'actions':[]})
            self.assertEqual(clean.status_code, 200)
            self.assertEqual(clean.json()['preview'][0]['id'], '001')
            self.assertEqual(clean.json()['display_cleaned_file'], 'cleaned_sample.csv')
            download = client.get(clean.json()['download_url'])
            self.assertEqual(download.status_code, 200)
            self.assertIn('cleaned_sample.csv', download.headers['content-disposition'])
            self.assertEqual(client.post('/inspect',files={'file':('bad.txt', b'x')}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
