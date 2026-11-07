from scripts.check_power_plan import check
import pytest
from power_manager.costs import estimate
from power_manager.core import actions

@pytest.mark.parametrize('kind', ['aws_lb', 'aws_nat_gateway', 'aws_eip', 'aws_instance', 'aws_eks_node_group', 'aws_route53_record'])
def test_publish_plan_cannot_touch_existing_lab(kind):
    with pytest.raises(ValueError):
        check({'resource_changes': [{'type': kind, 'name': 'control', 'address': kind+'.control', 'change': {'actions': ['update']}}]})


def test_publish_replacements_forbidden():
    with pytest.raises(ValueError):
        check({'resource_changes': [{'type': 'aws_lambda_function', 'name': 'api', 'address': 'aws_lambda_function.api', 'change': {'actions': ['delete','create']}}]})


def test_residual_is_not_zero():
    items = [{'type': t} for t in ['EKS','ALB','NLB','NAT','EIP','EIP','EIP']]
    assert estimate(items)['minimum_residual_usd_month'] == 149.65
    assert not estimate(items)['complete']


def test_generic_asg_capacity_restored_exactly():
    s={'key': 'snapshot','targets': [{'type':'ASG','id':'group','capacity':{'MinSize':2,'MaxSize':5,'DesiredCapacity':3}}]}
    assert actions(s,'DOWN')[1]['capacity'] == {'MinSize':0,'MaxSize':5,'DesiredCapacity':0}
    assert actions(s,'UP')[0]['capacity'] == s['targets'][0]['capacity']
