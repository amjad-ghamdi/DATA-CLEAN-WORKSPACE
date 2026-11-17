"""Semantic decisions, exact rule checks, dry runs and private reusable recipes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import polars as pl
from fastapi.testclient import TestClient
import main_api as api
from src.tools.column_semantics import suggest_roles, category_groups
from src.tools.validation_rules import ValidationRule, evaluate_rules
from src.tools.cleaning_engine import CleaningEngine


class SemanticRuleTests(unittest.TestCase):
    def test_roles_use_names_and_values_without_assuming_rating_scale(self):
        frame = pl.DataFrame({'customer_id':['001'],'Company Score':[3.9],'email_address':['a@b.com'],'Salary':['£30K'],'Skills':['R, SQL'],'mystery':['x@y.com']})
        roles = suggest_roles(frame)
        self.assertEqual(roles['customer_id']['role'], 'identifier')
        self.assertEqual(roles['Company Score']['role'], 'rating')
        self.assertEqual(roles['Salary']['role'], 'salary')
        self.assertEqual(roles['mystery']['role'], 'email')
        self.assertFalse(roles['Company Score']['confirmed'])
        self.assertNotIn('maximum', roles['Company Score'])

    def test_category_frequency_is_a_suggestion_and_ties_are_unresolved(self):
        frame = pl.DataFrame({'city':['Riyadh','RIYADH','Riyadh','Jeddah'], 'status':['Yes','YES',None,'No']})
        groups = category_groups(frame, {'city':{'role':'category'},'status':{'role':'category'}})
        self.assertEqual(groups[0]['suggested_target'],'Riyadh')
        self.assertIsNone(groups[1]['suggested_target'])
        self.assertTrue(all(g['needs_review'] for g in groups))
        self.assertEqual(category_groups(frame, {'city':{'role':'category'}}, ['city']), [])

    def test_exact_rules_handle_missing_nan_duplicates_invalid_and_empty(self):
        frame = pl.DataFrame({'score':[3.,8.,None,float('nan')], 'id':['1','1',None,'4'],
                              'email':['a@b.com','bad',None,''], 'date':['2026-01-02','02/01/2026',None,'bad']})
        rules = [ValidationRule(column='score',kind='range',minimum=0,maximum=5),
                 ValidationRule(column='id',kind='unique'),ValidationRule(column='score',kind='required'),
                 ValidationRule(column='email',kind='email'),ValidationRule(column='date',kind='date')]
        result = evaluate_rules(frame,rules)
        self.assertEqual([f['violations'] for f in result['findings']], [2,2,1,2,2])
        self.assertEqual(result['rows_checked'],4)
        self.assertEqual(result['findings'][0]['examples'][0]['row'],2)

    def test_allowed_length_native_date_and_row_name_collision(self):
        from datetime import date
        frame = pl.DataFrame({'__rule_row':[1,2], 'text':['ok','toolong'], 'date':[date(2026,1,1),None]})
        result = evaluate_rules(frame,[{'column':'text','kind':'allowed','values':['ok']},
                                       {'column':'text','kind':'length','maximum':3}, {'column':'date','kind':'date'}])
        self.assertEqual([f['violations'] for f in result['findings']],[1,1,0])
        with self.assertRaises(ValueError):
            ValidationRule(column='text',kind='range',minimum=5,maximum=2)
        with self.assertRaises(ValueError):
            ValidationRule(column='text',kind='email',pattern='user code')

    def test_mapping_is_simultaneous_and_requires_approval(self):
        engine = CleaningEngine('unused.csv'); engine.df = pl.DataFrame({'category':['A','B','C',None]})
        with self.assertRaises(ValueError):
            engine.map_categories('category',{'A':'B'})
        self.assertEqual(engine.map_categories('category',{'A':'B','B':'C'},True),2)
        self.assertEqual(engine.df['category'].to_list(),['B','C','C',None])

    def test_executor_coalesces_groups_before_formatting_and_rejects_conflicts(self):
        frame = pl.DataFrame({'city':[' A ','B','C']})
        actions = [api.CleaningAction(action='trim_whitespace',column='city'),
                   api.CleaningAction(action='map_categories',column='city',mapping={' A ':'B'},approved=True),
                   api.CleaningAction(action='map_categories',column='city',mapping={'B':'C'},approved=True)]
        engine, _ = api._execute_actions(frame, Path('unused.csv'), actions, preview=True)
        self.assertEqual(engine.df['city'].to_list(), ['B','C','C'])
        actions.append(api.CleaningAction(action='map_categories',column='city',mapping={'B':'D'},approved=True))
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            api._execute_actions(frame, Path('unused.csv'), actions)

    def test_mode_tie_does_not_pick_arbitrarily(self):
        engine = CleaningEngine('unused.csv'); engine.df = pl.DataFrame({'value':['A','B',None]})
        with self.assertRaisesRegex(ValueError,'tied'):
            engine.fill_nulls('value','mode')
        self.assertIsNone(engine.df['value'][2])


class DecisionHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(api,'UPLOAD_DIR',self.root); self.patch.start()
        self.client = TestClient(api.app); self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None); self.patch.stop(); self.temp.cleanup()

    def upload(self,contents=b'customer_id,city,score\n001,Riyadh,1\n002,RIYADH,3\n003,Riyadh,\n'):
        r=self.client.post('/inspect',files={'file':('test.csv',contents,'text/csv')}); self.assertEqual(r.status_code,200,r.text)
        data=r.json();self.assertTrue(data['valid'],data)
        r=self.client.post(f"/datasets/{data['dataset_id']}/analyze",json={'revision':data['revision']});self.assertEqual(r.status_code,200,r.text)
        return data,r.json()

    def decisions(self,data,**overrides):
        body={'revision':data['revision'],'roles':{'city':'category','score':'rating'},
              'rules':[{'column':'score','kind':'range','minimum':0,'maximum':5}]}
        body.update(overrides)
        r=self.client.post(f"/datasets/{data['dataset_id']}/decisions",json=body); self.assertEqual(r.status_code,200,r.text)
        return r.json()

    def test_full_workflow_effects_approval_lineage_rules_and_original_preservation(self):
        data,_=self.upload(); original=(self.root/data['filename']).read_bytes()
        decisions=self.decisions(data,rules=[{'column':'score','kind':'range','maximum':1}])
        body={'revision':data['revision'],'decision_revision':decisions['decisions']['revision'],
              'actions':[{'action':'map_categories','column':'city','mapping':{'RIYADH':'Riyadh'},'approved':True},
                         {'action':'fill_nulls','column':'score','strategy':'median'}]}
        r=self.client.post(f"/datasets/{data['dataset_id']}/effects",json=body);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['preview'][2]['score'],2)
        self.assertEqual(r.json()['actions'][0]['changed'],1)
        self.assertFalse(list(self.root.glob('cleaned_*')))
        r=self.client.post('/clean',json={'filename':data['filename'],'dataset_id':data['dataset_id'],'reading_revision':data['revision'],
                                        'decision_revision':body['decision_revision'],'actions':body['actions']})
        self.assertEqual(r.status_code,200,r.text); result=r.json()
        self.assertEqual(result['validation']['status'],'WARNING')
        self.assertEqual(result['validation']['custom_rules_after']['findings'][0]['violations'],2)
        self.assertEqual((self.root/data['filename']).read_bytes(),original)
        self.assertEqual(self.client.get(result['lineage_download_url']).status_code,200)
        self.assertIn('map_categories',self.client.get(result['lineage_download_url']).text)

    def test_saved_plan_recomputes_statistic_and_checks_schema_and_ownership(self):
        data,_=self.upload();decisions=self.decisions(data)
        request={'revision':data['revision'],'decision_revision':decisions['decisions']['revision'],'name':'Jobs',
                 'approved':True,'actions':[{'action':'fill_nulls','column':'score','strategy':'median'}]}
        r=self.client.post(f"/datasets/{data['dataset_id']}/plans",json=request);self.assertEqual(r.status_code,200,r.text)
        plan_id=r.json()['id']
        second,_=self.upload(b'customer_id,city,score\n004,Riyadh,10\n005,Riyadh,30\n006,Riyadh,\n')
        plans=self.client.get(f"/datasets/{second['dataset_id']}/plans").json()['plans']
        self.assertTrue(plans[0]['compatible']); self.assertTrue(plans[0]['requires_confirmation'])
        actions=plans[0]['payload']['actions']; self.assertNotIn('median',actions[0].get('mapping',{}))
        r=self.client.post(f"/datasets/{second['dataset_id']}/effects",json={'revision':second['revision'],'actions':actions})
        self.assertEqual(r.status_code,200,r.text); self.assertEqual(r.json()['preview'][2]['score'],20)
        other,_=self.upload(b'name,amount\nA,1\n')
        self.assertFalse(self.client.get(f"/datasets/{other['dataset_id']}/plans").json()['plans'][0]['compatible'])
        with TestClient(api.app) as stranger:
            self.assertEqual(stranger.delete(f'/plans/{plan_id}').status_code,404)
        self.assertEqual(self.client.delete(f'/plans/{plan_id}').status_code,200)
        self.assertEqual(self.client.get(f"/datasets/{data['dataset_id']}/plans").json()['plans'],[])

    def test_owned_plan_can_be_updated_and_not_duplicated(self):
        data,_=self.upload();decisions=self.decisions(data)
        body={'revision':data['revision'],'decision_revision':decisions['decisions']['revision'],
              'name':'first','approved':True,'actions':[]}
        saved=self.client.post(f"/datasets/{data['dataset_id']}/plans",json=body)
        self.assertEqual(saved.status_code,200,saved.text)
        updated=self.client.post(f"/datasets/{data['dataset_id']}/plans",json={**body,'name':'updated','plan_id':saved.json()['id']})
        self.assertEqual(updated.status_code,200,updated.text)
        plans=self.client.get(f"/datasets/{data['dataset_id']}/plans").json()['plans']
        self.assertEqual(len(plans),1);self.assertEqual(plans[0]['name'],'updated')

    def test_stale_decisions_and_invalid_mapping_blocked(self):
        data,_=self.upload();first=self.decisions(data);second=self.decisions(data)
        body={'revision':data['revision'],'decision_revision':first['decisions']['revision'],'actions':[]}
        self.assertEqual(self.client.post(f"/datasets/{data['dataset_id']}/effects",json=body).status_code,409)
        body['decision_revision']=second['decisions']['revision'];body['actions']=[{'action':'map_categories','column':'city','mapping':{'RIYADH':'Riyadh'}}]
        self.assertEqual(self.client.post(f"/datasets/{data['dataset_id']}/effects",json=body).status_code,422)
        self.assertEqual(self.client.post(f"/datasets/{data['dataset_id']}/plans",json={**body,'name':'x','approved':False}).status_code,422)
        self.assertEqual(self.client.post(f"/datasets/{data['dataset_id']}/decisions",json={'revision':data['revision'],'roles':{'score':'made-up'}}).status_code,422)

    def test_protected_fields_not_offered_for_group_merge(self):
        data,analysis=self.upload(b'customer_id,phone,Salary,Skills\n001,0500,10K,SQL\n002,0501,,Python\n003,0502,,\n')
        for step in analysis['cleaning_plan']:
            if step['column'] in {'Salary','Skills'} and step['action']=='fill_nulls':
                self.assertFalse(step['auto_eligible'])
        self.assertTrue(all(g['column'] not in {'phone','customer_id'} for g in analysis['category_groups']))


if __name__=='__main__':
    unittest.main()
