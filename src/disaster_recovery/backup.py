"""Destination-only backup inventory, guarded restore and versioned artifact recovery."""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import tempfile

REGION='us-east-2'
VAULT='cosmotech-dr'
BUCKET='cosmotech-dr-414053644145-us-east-2'


def backup_readiness(client, expected_arns, *, now=None, max_age_seconds=3600):
    if client.meta.region_name != REGION or not expected_arns or not 1<=max_age_seconds<=86400:
        raise ValueError('Explicit resources, Ohio client and bounded freshness required')
    now=now or datetime.now(timezone.utc)
    found={}
    paginator=client.get_paginator('list_recovery_points_by_backup_vault')
    for page in paginator.paginate(BackupVaultName=VAULT):
        for point in page.get('RecoveryPoints',[]):
            resource=point['ResourceArn']
            if resource not in expected_arns or point.get('Status')!='COMPLETED':continue
            stamp=point.get('CreationDate')
            if not stamp or stamp.tzinfo is None or not 0<=(now-stamp).total_seconds()<=max_age_seconds:continue
            arn=point['RecoveryPointArn']
            from .restore import valid_recovery_point_arn
            if (not valid_recovery_point_arn(arn, point.get('ResourceType', ''))
                    or point.get('BackupVaultArn') != f'arn:aws:backup:{REGION}:414053644145:backup-vault:{VAULT}'
                    or point.get('IsEncrypted') is not True):continue
            if resource not in found or stamp>found[resource]['CreationDate']:found[resource]=point
    missing=sorted(set(expected_arns)-set(found))
    return {'ready':not missing,'missing_or_stale':missing,'points':found,
            'scope':'completed-encrypted-destination-copies-only','application_consistency_verified':False}


def execute_restore_plan(backup, plan, *, release_gate, role_arn, checkpoint=None):
    """Only already compiled allowlisted intents; no source restore/overwrite/delete."""
    if backup.meta.region_name!=REGION or not release_gate():
        raise ValueError('Account release/scope not verified; no restore started')
    if not re.fullmatch(r'arn:aws:iam::414053644145:role/cosmotech-dr-[\w+=,.@-]+',role_arn):
        raise ValueError('Dedicated scoped restore role required')
    from .control import canonical_hash
    if plan.get('mode')!='plan-only' or not plan.get('intents'):
        raise ValueError('Verified compiled restore intents required')
    if len(plan['intents'])>25:
        raise ValueError('Restore batch exceeds reviewed lab scope')
    # Validate the ENTIRE batch before starting any resource creation.
    from .restore import validate_intent
    for intent in plan['intents']:
        validate_intent(intent)
        observed=backup.describe_recovery_point(BackupVaultName=VAULT,RecoveryPointArn=intent['arn'])
        if (observed.get('Status')!='COMPLETED' or observed.get('IsEncrypted') is not True
                or observed.get('BackupVaultArn') != f'arn:aws:backup:{REGION}:414053644145:backup-vault:{VAULT}'):
            raise ValueError('Recovery point not completed/encrypted')
    jobs=[]
    for intent in plan['intents']:
        # Revalidate every intent through the same destination-only compiler.
        from .restore import validate_intent
        validate_intent(intent)
        if not release_gate():raise ValueError('Release evidence expired; stop additional restores')
        token=canonical_hash({'intent':intent,'role':role_arn})[:50]
        response=backup.start_restore_job(RecoveryPointArn=intent['arn'],Metadata=intent['metadata'],
            IamRoleArn=role_arn,ResourceType=intent['type'],IdempotencyToken=token)
        jobs.append({'job_id':response['RestoreJobId'],'intent_hash':canonical_hash(intent)})
        if checkpoint:checkpoint(jobs)
    return jobs


def restore_artifact(s3, *, key, version_id, sha256, destination, max_bytes=1024**3):
    if (s3.meta.region_name!=REGION or not key.startswith(('itsm/','dr-evidence/')) or '..' in key.split('/')
            or not version_id or version_id=='null' or not re.fullmatch('[0-9a-f]{64}',sha256)):
        raise ValueError('Only immutable destination recovery artifacts allowed')
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.dr-restore-',dir=destination.parent)
    digest=hashlib.sha256();size=0
    try:
        response=s3.get_object(Bucket=BUCKET,Key=key,VersionId=version_id)
        if response.get('ServerSideEncryption') not in {'AES256','aws:kms'}:
            response['Body'].close();raise ValueError('Unencrypted recovery artifact')
        with os.fdopen(fd,'wb') as output, response['Body'] as body:
            fd=None
            while chunk:=body.read(1024*1024):
                size+=len(chunk)
                if size>max_bytes:raise ValueError('Recovery artifact exceeds approved size')
                digest.update(chunk);output.write(chunk)
            output.flush();os.fsync(output.fileno())
        if digest.hexdigest()!=sha256:raise ValueError('Recovery artifact checksum mismatch')
        os.link(temp,destination)  # fail if destination exists; never overwrite data
        return {'verified':True,'bytes':size,'sha256':sha256,'archive_extracted':False}
    finally:
        if fd is not None:os.close(fd)
        Path(temp).unlink(missing_ok=True)
