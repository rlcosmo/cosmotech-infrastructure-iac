"""Pure destination restore intent validation, shared by compiler and SDK executor."""
import re


def valid_recovery_point_arn(arn: str, resource_type: str) -> bool:
    """AWS Backup uses accountless EC2 snapshot ARNs; vault ownership is separate."""
    if resource_type == 'EBS':
        return bool(re.fullmatch(r'arn:aws:ec2:us-east-2::snapshot/snap-[a-f0-9]+', arn))
    if resource_type == 'DynamoDB':
        return bool(re.fullmatch(r'arn:aws:backup:us-east-2:414053644145:recovery-point:[A-Za-z0-9-]+', arn))
    return False


def validate_intent(point):
    if not valid_recovery_point_arn(point.get('arn', ''), point.get('type', '')):
        raise ValueError('Only copied Ohio recovery points')
    metadata=point.get('metadata')
    if not isinstance(metadata,dict) or any(not isinstance(v,str) or 'us-east-1' in v for v in metadata.values()):
        raise ValueError('Destination-only string restore metadata required')
    if point['type']=='DynamoDB':
        if not re.fullmatch(r'cosmotech-dr-(orders|outbox)-restored(?:-[a-f0-9]{12})?',metadata.get('targetTableName','')):
            raise ValueError('Only a NEW isolated restoration table')
    elif point['type']=='EBS':
        if metadata.get('availabilityZone') not in {'us-east-2a','us-east-2b','us-east-2c'} or metadata.get('volumeType')!='gp3' or metadata.get('encrypted','').lower()!='true':
            raise ValueError('Encrypted gp3 volume in destination AZ required')
    else:raise ValueError('Unsupported restore component')
