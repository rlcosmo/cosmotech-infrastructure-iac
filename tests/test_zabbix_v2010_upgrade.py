"""Storage, artifact and bootstrap guards for the Zabbix upgrade."""
import base64
import gzip
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import yaml

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('v2010_bootstrap',ROOT/'ops/zabbix/v2010/bootstrap.py');bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)

class UpgradeTests(unittest.TestCase):
    def test_deleted_snapshot_and_aws_denial_cannot_pass_historical_backup_gate(self):
        spec=importlib.util.spec_from_file_location('upgrade',ROOT/'ops/zabbix/upgrade_v2010.py')
        upgrade=importlib.util.module_from_spec(spec);spec.loader.exec_module(upgrade)
        snapshot={'SnapshotId':'snap-0a4278c92060be577','VolumeId':'vol-0cf8b3eaf7cc6ea10','State':'completed','Encrypted':True}
        with patch.object(upgrade,'run',side_effect=RuntimeError('AWS lookup failed')):
            with self.assertRaises(RuntimeError):upgrade.verify_live_snapshot(snapshot)
        for live in [[],[{**snapshot,'State':'pending'}],[{**snapshot,'VolumeId':'vol-other'}],[{**snapshot,'Encrypted':False}]]:
            with self.subTest(live=live),patch.object(upgrade,'run',return_value=json.dumps({'Snapshots':live}).encode()):
                with self.assertRaises(ValueError):upgrade.verify_live_snapshot(snapshot)
        with patch.object(upgrade,'run',return_value=json.dumps({'Snapshots':[snapshot]}).encode()):
            upgrade.verify_live_snapshot(snapshot)

    def test_original_named_data_volumes_and_private_ingestion_are_preserved(self):
        compose=yaml.safe_load((ROOT/'ops/zabbix/docker-compose.yml').read_text())
        self.assertEqual({k:v['name'] for k,v in compose['volumes'].items()},
            {'postgres':'cosmotech-zabbix-postgres','clickhouse':'cosmotech-zabbix-clickhouse','proxy':'cosmotech-zabbix-proxy'})
        self.assertNotIn('ports',compose['services']['notifications'])
        self.assertEqual(compose['services']['lab-agent']['profiles'],['v2010-lab'])
        self.assertNotIn('ports',compose['services']['lab-agent'])
        self.assertTrue(compose['services']['notifications']['read_only'])
        self.assertEqual(compose['services']['notifications']['cap_drop'],['ALL'])
        self.assertEqual(compose['services']['proxy']['ports'],['127.0.0.1:14317:4317'])
    def test_stable_components_and_official_frontend_base_are_pinned(self):
        images=json.loads((ROOT/'ops/zabbix/images.json').read_text())
        for key in ['server','web','proxy','agent2']:
            self.assertEqual(images[key]['version'],'8.0.0')
            self.assertIn('@sha256:',images[key]['image'])
            self.assertNotIn('trunk',images[key]['source_tag'])
        self.assertIn(images['web']['image'],(ROOT/'ops/zabbix/v2010/Dockerfile.web').read_text())
        self.assertEqual(images['clickhouse']['version'],'26.8.36.3')
    def test_compressed_code_bundle_stays_under_ssm_limit(self):
        files=[p for p in (ROOT/'ops/zabbix').glob('*') if p.is_file()]+[p for p in (ROOT/'ops/zabbix/v2010').glob('*') if p.is_file()]
        size=sum(len(base64.b64encode(gzip.compress(p.read_bytes(),mtime=0)))+len(str(p))*2+160 for p in files)
        self.assertLess(size+4000,60000)
    def test_upsert_reuses_owned_ids_and_rejects_ambiguity(self):
        api=bootstrap.API.__new__(bootstrap.API)
        with patch.object(api,'call',side_effect=[[{'itemid':'12'}],{'itemids':['12']}]) as call:
            self.assertEqual(api.upsert('item','itemid',{'key_':'v2010.owned'},{'hostid':'5','name':'owned'}),'12')
            self.assertEqual([c.args[0] for c in call.call_args_list],['item.get','item.update'])
            self.assertNotIn('hostid',call.call_args_list[1].args[1])
        with patch.object(api,'call',return_value=[{'itemid':'1'},{'itemid':'2'}]):
            with self.assertRaises(ValueError):api.upsert('item','itemid',{},{} )

    def test_periodic_monitoring_preserves_other_owned_groups_and_tags(self):
        spec=importlib.util.spec_from_file_location('monitoring',ROOT/'ops/zabbix-monitoring/configure_monitoring.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        obj=module.Zabbix.__new__(module.Zabbix)
        existing={'hostid':'1','hostgroups':[{'groupid':'original'}],'tags':[{'tag':'custom','value':'keep'}]}
        with patch.object(obj,'api',side_effect=[[existing],{}]) as api:
            self.assertEqual(obj.host('owned','canonical'),'1')
            payload=api.call_args_list[1].args[1]
            self.assertEqual({g['groupid'] for g in payload['groups']},{'original','canonical'})
            self.assertIn({'tag':'custom','value':'keep'},payload['tags'])
