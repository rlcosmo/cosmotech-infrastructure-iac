import importlib.util
from pathlib import Path
import unittest

ROOT=Path(__file__).parents[1]
def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'ops/iac-portal'/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
comparison=module('direct_comparison')
SOURCE={'repository':'canonical','branch':'main','commit':'a'*40}

def resource(address,kind,values):return {'address':address,'mode':'managed','type':kind,'values':values}
def plan(rows,changes=(),before=None):
    return {'planned_values':{'root_module':{'resources':rows}},
            'prior_state':{'values':{'root_module':{'resources':rows if before is None else before}}},
            'resource_changes':list(changes)}

class ComparisonTests(unittest.TestCase):
    def test_git_teste_and_extra_lab_nodegroup(self):
        expected=resource('aws_eks_node_group.teste','aws_eks_node_group',{'id':'cluster:teste','arn':'arn:aws:eks:us-east-1:123456789012:nodegroup/cluster/teste/123','node_group_name':'teste'})
        live=[{'kind':'AWS::EKS::Nodegroup','id':'cluster:teste','name':'teste','region':'us-east-1','arn':expected['values']['arn']},
              {'kind':'AWS::EKS::Nodegroup','id':'cluster:lab','name':'lab','region':'us-east-1','arn':'arn:aws:eks:us-east-1:123456789012:nodegroup/cluster/lab/456'}]
        result=comparison.compare(plan([expected]),{'resources':live},SOURCE)
        self.assertEqual(result['drift_count'],1)
        self.assertEqual([(r['name'],r['status']) for r in result['resources']], [('teste','in_sync'),('lab','extra')])

    def test_removed_from_git_is_extra_even_if_in_state(self):
        old=resource('aws_instance.lab','aws_instance',{'id':'i-lab','tags':{'Name':'lab'}})
        change={'address':old['address'],'type':old['type'],'change':{'actions':['delete'],'before':old['values'],'after':None}}
        result=comparison.compare(plan([], [change], [old]),{'resources':[]},SOURCE)
        self.assertEqual(result['resources'][0]['status'],'extra')

    def test_configuration_changes_and_missing_resources_without_refresh_only_drift(self):
        r=resource('aws_instance.test','aws_instance',{'id':'i-test','instance_type':'t3.small'})
        c={'address':r['address'],'type':r['type'],'change':{'actions':['update'],'before':{'id':'i-test','instance_type':'t3.large','password':'must-not-leak'},'after':r['values']}}
        missing={'address':'aws_s3_bucket.new','type':'aws_s3_bucket','change':{'actions':['create'],'before':None,'after':{'bucket':'new'}}}
        result=comparison.compare(plan([r],[c,missing]),{'resources':[]},SOURCE)
        self.assertEqual(result['summary'],{'different':1,'missing':1})
        self.assertNotIn('must-not-leak',str(result))
        self.assertIn('instance_type',result['resources'][0]['changed_fields'])

    def test_eks_generated_instances_are_linked_only_to_owned_parent(self):
        group=resource('aws_eks_node_group.main','aws_eks_node_group',{'id':'cluster:teste','arn':'arn:aws:eks:us-east-1:123456789012:nodegroup/cluster/teste/id','node_group_name':'teste'})
        live=[{'kind':'AWS::AutoScaling::AutoScalingGroup','id':'eks-asg','name':'eks-asg','region':'us-east-1','parents':[group['values']['arn']],'platform':True},
              {'kind':'AWS::EC2::Instance','id':'i-node','name':'node','region':'us-east-1','parents':['eks-asg'],'platform':True},
              {'kind':'AWS::EC2::Instance','id':'i-lab','name':'lab','region':'us-east-1','parents':['unowned-asg'],'platform':True}]
        result=comparison.compare(plan([group]),{'resources':live},SOURCE)
        self.assertEqual(result['summary']['service_managed'],2)
        self.assertEqual(result['summary']['extra'],1)

    def test_failed_evaluation_does_not_claim_all_clear(self):
        for flag in [{'errored':True},{'deferred_changes':[{}]},{'complete':False}]:
            with self.assertRaises(ValueError):comparison.compare(flag,{'resources':[]},SOURCE)
        result=comparison.compare(plan([]),{'resources':[],'coverage':[{'status':'unavailable'}]},SOURCE)
        self.assertFalse(result['inventory_complete'])

    def test_root_disk_is_not_falsely_extra(self):
        r=resource('aws_instance.test','aws_instance',{'id':'i-test','root_block_device':[{'volume_id':'vol-root'}]})
        live=[{'kind':'AWS::EC2::Volume','id':'vol-root','region':'us-east-1'}, {'kind':'AWS::EC2::Volume','id':'vol-manual','region':'us-east-1'}]
        result=comparison.compare(plan([r]),{'resources':live},SOURCE)
        self.assertEqual([r['id'] for r in result['resources'] if r['status']=='extra'],['vol-manual'])

    def test_arn_match_is_not_a_name_or_tag_heuristic(self):
        expected=resource('aws_instance.test','aws_instance',{'id':'i-expected','tags':{'Name':'lab'}})
        live=[{'kind':'AWS::EC2::Instance','id':'i-other','name':'lab','region':'us-east-1'}]
        result=comparison.compare(plan([expected]),{'resources':live},SOURCE)
        self.assertEqual(result['summary']['extra'],1)

class DirectInventoryTests(unittest.TestCase):
    def test_untagged_storage_ec2_and_nodegroups_are_discovered_and_denial_is_explicit(self):
        inventory=module('direct_inventory')
        calls=[]
        def call(region,service,operation,*args):
            calls.append((region,service,operation))
            if operation=='describe-regions':return {'Regions':[{'RegionName':'us-east-1'}]}
            if operation=='describe-volumes':return {'Volumes':[{'VolumeId':'vol-lab'}]}
            if operation=='describe-instances':return {'Reservations':[{'Instances':[{'InstanceId':'i-lab','State':{'Name':'running'}}]}]}
            if operation=='list-clusters':return {'clusters':['teste']}
            if operation=='list-nodegroups':return {'nodegroups':['lab']}
            if operation=='describe-nodegroup':return {'nodegroup':{'nodegroupArn':'arn:aws:eks:us-east-1:123456789012:nodegroup/teste/lab/id'}}
            if operation=='list-buckets':return {'Buckets':[{'Name':'lab-storage'}]}
            if operation=='get-bucket-location':return {'LocationConstraint':None}
            raise RuntimeError('AccessDenied')
        result=inventory.inventory(call,'123456789012')
        self.assertTrue({'vol-lab','i-lab','teste:lab','lab-storage'} <= {r['id'] for r in result['resources']})
        self.assertFalse(result['complete'])
        self.assertTrue(any(c['status']=='unavailable' and c['source']=='fsx' for c in result['coverage']))
        self.assertTrue(all(op not in ['receive-message','get-secret-value','get-object','create-index'] for _,_,op in calls))
