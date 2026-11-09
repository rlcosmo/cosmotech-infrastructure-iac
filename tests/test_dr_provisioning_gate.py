"""Disabled, incomplete and no-op plans cannot be claimed as DR provisioning."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('plan_gate',Path(__file__).resolve().parents[1]/'scripts/dr/check_plan.py')
gate=importlib.util.module_from_spec(spec);spec.loader.exec_module(gate)

class ProvisioningGateTests(unittest.TestCase):
    def fixture(self):
        return {'resource_changes':[{'address':f'module.standby[0].{kind}.resource_{i}',
            'type':kind,'change':{'actions':['create'],'after':{'region':'us-east-2'}}}
            for kind,n in gate.REQUIRED_COUNTS.items() for i in range(n)],
            'output_changes':{'readiness':{'after':{'provisioning_enabled':True,'mode':'passive',
            'selected_region':'us-east-2','state_key':'aws-serverless-lab/dr/terraform.tfstate',
            'financial_ceiling_usd':200}}}}

    def test_requires_graph_and_actual_changes(self):
        self.assertTrue(gate.check_plan(self.fixture(),require_provisioning=True))
        empty=self.fixture();empty['resource_changes']=[]
        noop=self.fixture()
        for r in noop['resource_changes']:r['change']['actions']=['no-op']
        partial=self.fixture();partial['resource_changes']=[r for r in partial['resource_changes'] if r['type']!='aws_eks_cluster']
        for bad in [empty,noop,partial,{**self.fixture(),'complete':False}]:
            with self.assertRaises(ValueError):gate.check_plan(bad,require_provisioning=True)

    def test_requires_enabled_isolated_configuration_and_budget(self):
        for field,value in [('provisioning_enabled',False),('mode','active'),
                            ('selected_region','us-east-1'),('state_key','primary'),
                            ('financial_ceiling_usd',0),('financial_ceiling_usd',True),
                            ('financial_ceiling_usd',float('nan'))]:
            plan=copy.deepcopy(self.fixture());plan['output_changes']['readiness']['after'][field]=value
            with self.assertRaises(ValueError):gate.check_plan(plan,require_provisioning=True)
