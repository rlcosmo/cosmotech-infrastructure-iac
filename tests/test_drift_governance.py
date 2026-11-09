"""Ownership and safety regressions; no AWS, Kubernetes or file mutations outside tmp."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from test_direct_iac_comparison import comparison, module, plan, resource, SOURCE


def asset(kind,id,region='us-east-1',**kw):return dict(kind=kind,id=id,region=region,name=id,**kw)
def compare(rows,live,**kw):return comparison.compare(plan(rows),dict(resources=live,**kw),SOURCE)


class GovernanceTests(unittest.TestCase):
    def test_same_name_cross_region_never_inherits_owner(self):
        parent=resource('aws_autoscaling_group.main','aws_autoscaling_group',{'id':'asg','name':'same'})
        live=[asset('AWS::EC2::Instance','i-other','us-east-2',platform=True,parents=['asg','same'])]
        result=compare([parent],live)
        self.assertEqual(result['summary']['extra'],1)
        self.assertEqual(result['regional_summary']['us-east-2'],{'extra':1})

    def test_automatic_snapshots_visible_but_not_false_drift(self):
        for key in ['aws:dlm:lifecycle-policy-id','aws:backup:source-resource']:
            r=compare([], [asset('AWS::EC2::Snapshot','snap-a',metadata={'tags':{key:'owner'}})])
            self.assertEqual((r['drift_count'],len(r['resources'])),(0,1))
            self.assertEqual(r['resources'][0]['management'],'aws_managed')

    def test_manual_snapshot_and_name_only_automation_still_extra(self):
        r=compare([], [asset('AWS::EC2::Snapshot','snap-a',name_tag='automatic-dlm',metadata={'source_volume_id':'deleted'})])
        self.assertEqual(r['drift_count'],1)
        self.assertFalse(r['resources'][0]['orphan_candidate'])

    def test_terraform_snapshot_overrides_system_tag(self):
        owner=resource('aws_ebs_snapshot.s','aws_ebs_snapshot',{'id':'snap-a'})
        r=compare([owner],[asset('AWS::EC2::Snapshot','snap-a',metadata={'tags':{'aws:dlm:lifecycle-policy-id':'p'}})])
        self.assertEqual(r['management_summary'],{'terraform':1})

    def test_detached_pvc_is_review_not_orphan_or_false_extra(self):
        r=compare([], [asset('AWS::EC2::Volume','vol-a',metadata={'tags':{'CSIVolumeName':'pvc-a'},'state':'available','attachments':[]})])
        self.assertEqual(r['resources'][0]['status'],'needs_review')
        self.assertEqual(r['orphan_candidate_count'],0)

    def test_detached_unmanaged_volume_only_candidate_with_observed_coverage(self):
        live=[asset('AWS::EC2::Volume','vol-a',metadata={'state':'available','attachments':[]})]
        for status,expected in [('unavailable',0),('partial',0),('observed',1)]:
            r=compare([],live,coverage=[{'region':'us-east-1','kind':'AWS::EC2::Volume','status':status}])
            self.assertEqual(r['orphan_candidate_count'],expected)

    def test_gitops_requires_unique_exact_region_physical_binding(self):
        live=[asset('AWS::EC2::Volume','vol-a')]
        b=dict(volume_id='vol-a',region='us-east-1',verified=True,repository='https://github.com/rlcosmo/cosmotech-gitops',commit='a'*40,pv='pv',pvc='claim',namespace='ns')
        self.assertEqual(compare([],live,gitops_bindings=[b])['resources'][0]['management'],'gitops')
        for bindings in [[{**b,'region':'us-east-2'}],[{**b,'verified':False}],[{**b,'repository':'untrusted'}],[b,b]]:
            self.assertEqual(compare([],live,gitops_bindings=bindings)['resources'][0]['management'],'unmanaged')

    def test_native_inventory_links_real_asg_membership_and_keeps_only_governance_metadata(self):
        def call(region,service,operation,*args):
            if operation=='describe-regions':return {'Regions':[{'RegionName':'us-east-1'}]}
            if operation=='describe-auto-scaling-groups':return {'AutoScalingGroups':[{'AutoScalingGroupName':'asg','Instances':[{'InstanceId':'i-a'}]}]}
            if operation=='describe-instances':return {'Reservations':[{'Instances':[{'InstanceId':'i-a','Tags':[{'Key':'secret','Value':'DO-NOT-EXPORT'}]}]}]}
            if operation=='describe-snapshots':return {'Snapshots':[{'SnapshotId':'snap-a','Tags':[{'Key':'aws:dlm:lifecycle-policy-id','Value':'policy'}]}]}
            raise RuntimeError('Unavailable')
        inv=module('direct_inventory').inventory(call,'123456789012')
        self.assertNotIn('DO-NOT-EXPORT',json.dumps(inv))
        instance=next(a for a in inv['resources'] if a['id']=='i-a')
        self.assertEqual(instance['parents'],['asg'])
        self.assertEqual(instance['parent_evidence'],'autoscaling-membership')
        owner=resource('aws_autoscaling_group.main','aws_autoscaling_group',{'id':'asg'})
        r=comparison.compare(plan([owner]),inv,SOURCE)
        self.assertEqual(r['drift_count'],0)


class GitOpsBindingsTests(unittest.TestCase):
    def fixture(self):
        return {'endpoint':'https://eks.example.test','pvs':[{'metadata':{'name':'pv'},'status':{'phase':'Bound'},'spec':{'claimRef':{'namespace':'ns','name':'claim','uid':'uid'},'csi':{'driver':'ebs.csi.aws.com','volumeHandle':'vol-abcd'}}}],
                'pvcs':[{'metadata':{'namespace':'ns','name':'claim','uid':'uid'},'status':{'phase':'Bound'},'spec':{'volumeName':'pv'}}]}

    def test_binding_checks_both_directions_uid_and_canonical_git(self):
        binding=module('gitops_bindings');snap=self.fixture()
        args=dict(region='us-east-1',commit='a'*40,cluster='cluster')
        self.assertEqual(len(binding.bindings(snap,{('https://eks.example.test','ns','claim')},**args)),1)
        wrong=self.fixture();wrong['endpoint']='https://other-cluster.test'
        self.assertEqual(binding.bindings(wrong,{('https://eks.example.test','ns','claim')},**args),[])
        snap['pvcs'][0]['metadata']['uid']='recreated'
        self.assertEqual(binding.bindings(snap,{('https://eks.example.test','ns','claim')},**args),[])
        self.assertEqual(binding.bindings(self.fixture(),set(),**args),[])

    def test_git_source_reads_plain_claims_and_ignores_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);apps=root/'gitops/apps/a';apps.mkdir(parents=True)
            bootstrap=root/'gitops/bootstrap';bootstrap.mkdir()
            (bootstrap/'app.json').write_text(json.dumps({'kind':'Application','spec':{'source':{'repoURL':'https://github.com/rlcosmo/cosmotech-gitops','targetRevision':'main','path':'gitops/apps/a'},'destination':{'server':'https://eks.example.test','namespace':'ns'}}}))
            doc={'kind':'PersistentVolumeClaim','metadata':{'name':'claim','namespace':'ns'}}
            (apps/'claim.json').write_text(json.dumps(doc))
            self.assertEqual(module('gitops_bindings').declared_claims(root),{('https://eks.example.test','ns','claim')})
            (apps/'claim.json').unlink();(apps/'claim.json').symlink_to('/etc/passwd')
            self.assertEqual(module('gitops_bindings').declared_claims(root),set())

    def test_region_and_management_controls_packaged_for_portal(self):
        html=(Path(__file__).parents[1]/'ops/iac-portal/index.html').read_text()
        self.assertIn('id="region"',html);self.assertIn('id="management"',html)
        self.assertIn('textContent',html);self.assertNotIn('innerHTML',html)
