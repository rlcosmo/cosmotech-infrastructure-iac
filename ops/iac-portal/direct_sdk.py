"""Reusable, paginated SDK transport for the fixed metadata inventory operations."""
from threading import local, Lock

READ_OPERATIONS = {
 ('ec2','describe-regions'),('ec2','describe-instances'),('ec2','describe-volumes'),
 ('ec2','describe-snapshots'),('ec2','describe-vpcs'),('ec2','describe-subnets'),
 ('ec2','describe-security-groups'),('ec2','describe-nat-gateways'),
 ('efs','describe-file-systems'),('fsx','describe-file-systems'),
 ('autoscaling','describe-auto-scaling-groups'),('rds','describe-db-instances'),
 ('elbv2','describe-load-balancers'),('ecr','describe-repositories'),('lambda','list-functions'),
 ('dynamodb','list-tables'),('eks','list-clusters'),('eks','list-nodegroups'),('eks','describe-nodegroup'),
 ('sqs','list-queues'),('configservice','describe-configuration-recorder-status'),
 ('configservice','select-resource-config'),('resource-explorer-2','search'),
 ('s3api','list-buckets'),('s3api','get-bucket-location'),
 ('iam','list-roles'),('iam','list-instance-profiles'),('iam','list-policies'),
}


def parameters(arguments, members):
    result={}; index=0
    while index<len(arguments):
        flag=arguments[index];index+=1
        if not flag.startswith('--'):
            raise ValueError('Metadata parameter flag required')
        matching=[name for name in members if name.lower()==flag[2:].replace('-','').lower()]
        if len(matching)!=1:
            raise ValueError('Unsupported metadata parameter')
        name=matching[0];shape=members[name]
        values=[]
        while index<len(arguments) and not arguments[index].startswith('--'):
            values.append(arguments[index]);index+=1
        if shape.type_name=='list':result[name]=values
        elif len(values)!=1:raise ValueError('Single metadata parameter value required')
        elif shape.type_name in ('integer','long'):result[name]=int(values[0])
        else:result[name]=values[0]
    return result


class InventorySdk:
    def __init__(self, credentials, session_factory=None):
        if not credentials.get('AccessKeyId','').startswith('ASIA') or not credentials.get('SessionToken'):
            raise ValueError('Temporary observer role credentials required')
        if session_factory is None:
            import boto3
            from botocore.config import Config
            from botocore.exceptions import ClientError, BotoCoreError
            self._factory=boto3.Session
            self._config=Config(retries={'total_max_attempts':2,'mode':'standard'},
                                connect_timeout=5,read_timeout=15,max_pool_connections=8)
            self._errors=(ClientError,BotoCoreError)
        else:
            self._factory=session_factory;self._config=None;self._errors=(RuntimeError,)
        self._credentials={'aws_access_key_id':credentials['AccessKeyId'],
                           'aws_secret_access_key':credentials['SecretAccessKey'],
                           'aws_session_token':credentials['SessionToken']}
        self._local=local();self._clients=[];self._lock=Lock()

    def client(self, region, service):
        if not hasattr(self._local,'session'):
            self._local.session=self._factory(**self._credentials)
            self._local.clients={}
        key=(region,service)
        if key not in self._local.clients:
            client=self._local.session.client(service,region_name=region,config=self._config)
            self._local.clients[key]=client
            with self._lock:self._clients.append(client)
        return self._local.clients[key]

    def __call__(self, region, service, operation, *arguments):
        if (service,operation) not in READ_OPERATIONS:
            raise ValueError('Operation outside read-only inventory')
        service={'s3api':'s3','configservice':'config'}.get(service,service)
        method=operation.replace('-','_')
        try:
            client=self.client(region,service)
            api_name=client.meta.method_to_api_mapping[method]
            model=client.meta.service_model.operation_model(api_name)
            kwargs=parameters(arguments,model.input_shape.members if model.input_shape else {})
            if method=='list_queues':kwargs.setdefault('MaxResults',1000)
            pages=client.get_paginator(method).paginate(**kwargs) if client.can_paginate(method) else [getattr(client,method)(**kwargs)]
            result={}
            for page in pages:
                for key,value in page.items():
                    if key in ['ResponseMetadata','NextToken','NextMarker','Marker','IsTruncated','NextContinuationToken']:
                        continue
                    if isinstance(value,list):result.setdefault(key,[]).extend(value)
                    else:result[key]=value
            return result
        except self._errors:
            # Boundary fallback: caller records unavailable coverage, never an empty result.
            raise RuntimeError('AWS metadata operation unavailable') from None

    def close(self):
        for client in self._clients:client.close()
