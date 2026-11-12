from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

import boto3
from moto import mock_aws

from src.disaster_recovery.backup import backup_readiness, restore_artifact, execute_restore_plan, BUCKET


class BackupControlTests(unittest.TestCase):
    def test_stale_failed_missing_or_unencrypted_copies_never_pass(self):
        now=datetime(2026,11,9,tzinfo=timezone.utc)
        client=Mock();client.meta.region_name='us-east-2'
        base=dict(ResourceArn='orders',ResourceType='DynamoDB',BackupVaultArn='arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr',RecoveryPointArn='arn:aws:backup:us-east-2:414053644145:recovery-point:one',
                  Status='COMPLETED',IsEncrypted=True,CreationDate=now-timedelta(minutes=10))
        for changed in [dict(Status='FAILED'),dict(IsEncrypted=False),dict(CreationDate=now-timedelta(hours=3))]:
            client.get_paginator.return_value.paginate.return_value=[{'RecoveryPoints':[{**base,**changed}]}]
            self.assertFalse(backup_readiness(client,['orders'],now=now)['ready'])
        client.get_paginator.return_value.paginate.return_value=[{'RecoveryPoints':[base]}]
        report=backup_readiness(client,['orders'],now=now)
        self.assertTrue(report['ready']);self.assertFalse(report['application_consistency_verified'])
        self.assertFalse(backup_readiness(client,['orders','missing-volume'],now=now)['ready'])

    def test_restore_release_wrong_region_and_bad_batch_fail_before_any_start(self):
        client=Mock();client.meta.region_name='us-east-2'
        role='arn:aws:iam::414053644145:role/cosmotech-dr-restore'
        intent=dict(arn='arn:aws:backup:us-east-2:414053644145:recovery-point:one',type='DynamoDB',metadata={'targetTableName':'cosmotech-dr-outbox-restored'})
        plan=dict(mode='plan-only',intents=[intent])
        with self.assertRaises(ValueError):execute_restore_plan(client,plan,release_gate=lambda:False,role_arn=role)
        client.start_restore_job.assert_not_called()
        client.describe_recovery_point.return_value=dict(Status='COMPLETED',IsEncrypted=True,BackupVaultArn='arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr')
        bad={**intent,'metadata':{'targetTableName':'primary-table'}}
        with self.assertRaises(ValueError):execute_restore_plan(client,{**plan,'intents':[intent,bad]},release_gate=lambda:True,role_arn=role)
        client.start_restore_job.assert_not_called()

    def test_ebs_and_dynamo_jobs_use_stable_idempotency_tokens(self):
        client=Mock();client.meta.region_name='us-east-2'
        client.describe_recovery_point.return_value=dict(Status='COMPLETED',IsEncrypted=True,BackupVaultArn='arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr')
        client.start_restore_job.return_value={'RestoreJobId':'fixture-job'}
        plan=dict(mode='plan-only',intents=[dict(arn='arn:aws:backup:us-east-2:414053644145:recovery-point:one',type='DynamoDB',metadata={'targetTableName':'cosmotech-dr-outbox-restored'}),
            dict(arn='arn:aws:ec2:us-east-2::snapshot/snap-abcd',type='EBS',metadata={'availabilityZone':'us-east-2b','volumeType':'gp3','encrypted':'true'})])
        kw=dict(release_gate=lambda:True,role_arn='arn:aws:iam::414053644145:role/cosmotech-dr-restore')
        execute_restore_plan(client,plan,**kw);first=[c.kwargs['IdempotencyToken'] for c in client.start_restore_job.call_args_list]
        client.start_restore_job.reset_mock();execute_restore_plan(client,plan,**kw)
        self.assertEqual(first,[c.kwargs['IdempotencyToken'] for c in client.start_restore_job.call_args_list])
        self.assertEqual(len(set(first)),2)


    def test_accountless_snapshot_from_owned_vault_is_ready(self):
        now=datetime(2026,11,9,tzinfo=timezone.utc)
        client=Mock();client.meta.region_name='us-east-2'
        point=dict(ResourceArn='volume',ResourceType='EBS',
            RecoveryPointArn='arn:aws:ec2:us-east-2::snapshot/snap-abcd',
            BackupVaultArn='arn:aws:backup:us-east-2:414053644145:backup-vault:cosmotech-dr',
            Status='COMPLETED',IsEncrypted=True,CreationDate=now-timedelta(minutes=10))
        client.get_paginator.return_value.paginate.return_value=[{'RecoveryPoints':[point]}]
        self.assertTrue(backup_readiness(client,['volume'],now=now)['ready'])
        point['BackupVaultArn']='arn:aws:backup:us-east-2:999999999999:backup-vault:cosmotech-dr'
        self.assertFalse(backup_readiness(client,['volume'],now=now)['ready'])

    def test_foreign_vault_blocks_restore_before_any_start(self):
        client=Mock();client.meta.region_name='us-east-2'
        client.describe_recovery_point.return_value=dict(Status='COMPLETED',IsEncrypted=True,
            BackupVaultArn='arn:aws:backup:us-east-2:999999999999:backup-vault:cosmotech-dr')
        point=dict(arn='arn:aws:ec2:us-east-2::snapshot/snap-abcd',type='EBS',
            metadata={'availabilityZone':'us-east-2b','volumeType':'gp3','encrypted':'true'})
        with self.assertRaises(ValueError):
            execute_restore_plan(client,dict(mode='plan-only',intents=[point]),
                release_gate=lambda:True,role_arn='arn:aws:iam::414053644145:role/cosmotech-dr-restore')
        client.start_restore_job.assert_not_called()


@mock_aws
class ArtifactRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.s3=boto3.client('s3',region_name='us-east-2')
        self.s3.create_bucket(Bucket=BUCKET,CreateBucketConfiguration={'LocationConstraint':'us-east-2'})
        self.s3.put_bucket_versioning(Bucket=BUCKET,VersioningConfiguration={'Status':'Enabled'})
        self.body=b'opaque encrypted recovery bundle; never execute or extract it'
        self.version=self.s3.put_object(Bucket=BUCKET,Key='itsm/bundle.tar',Body=self.body,ServerSideEncryption='AES256')['VersionId']

    def test_version_pinned_checksum_verified_private_restore_without_overwrite(self):
        self.s3.put_object(Bucket=BUCKET,Key='itsm/bundle.tar',Body=b'newer contents',ServerSideEncryption='AES256')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'recovery.tar'
            kw=dict(key='itsm/bundle.tar',version_id=self.version,sha256=hashlib.sha256(self.body).hexdigest(),destination=p)
            result=restore_artifact(self.s3,**kw)
            self.assertTrue(result['verified']);self.assertEqual(p.read_bytes(),self.body)
            self.assertEqual(os.stat(p).st_mode&0o777,0o600)
            with self.assertRaises(FileExistsError):restore_artifact(self.s3,**kw)
            self.assertEqual(p.read_bytes(),self.body)

    def test_corrupt_or_oversize_artifact_is_not_published(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'never-created'
            kw=dict(key='itsm/bundle.tar',version_id=self.version,sha256='0'*64,destination=p)
            with self.assertRaises(ValueError):restore_artifact(self.s3,**kw)
            self.assertFalse(p.exists())
            kw.update(sha256=hashlib.sha256(self.body).hexdigest(),max_bytes=1)
            with self.assertRaises(ValueError):restore_artifact(self.s3,**kw)
            self.assertEqual(list(Path(d).iterdir()),[])
