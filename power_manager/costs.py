"""Partial residual floor; published AWS us-east-1 rate card verified 2026-11-07."""
from decimal import Decimal
import json
from pathlib import Path
QUOTES=json.loads(Path(__file__).with_name("compute-rates.json").read_text())
RATES = {'EKS': Decimal('0.10'), 'ALB': Decimal('0.0225'), 'NLB': Decimal('0.0225'), 'NAT': Decimal('0.045'), 'EIP': Decimal('0.005')}
SOURCES = ['https://aws.amazon.com/eks/pricing/', 'https://aws.amazon.com/elasticloadbalancing/pricing/',
           'https://aws.amazon.com/vpc/pricing/', 'https://aws.amazon.com/ebs/pricing/']

def estimate(resources):
    floor = Decimal(0)
    for r in resources:
        if r['type']=='EC2':
            rate=QUOTES['spot'].get(r.get('instance_type','')+'@'+r.get('availability_zone','')) if r.get('capacity_type')=='spot' else QUOTES['ondemand'].get(r.get('instance_type'))
            if rate is not None:
                r['cost_on_usd_hour']=rate
                r['cost_off_usd_hour']='0' if r.get('action') in ('stop','managed') else rate
                r['quote_observed_at']=QUOTES['observed_at']
                floor += Decimal(r['cost_off_usd_hour'])*730
        elif r['type'] in RATES:
            amount = RATES[r['type']]
            r['cost_on_usd_hour'] = str(amount)
            r['cost_off_usd_hour'] = str(amount)
            floor += amount * 730
        elif r['type'] == 'EBS' and r.get('volume_type') == 'gp3':
            month = Decimal(str(r['size_gib'])) * Decimal('0.08')
            r['cost_on_usd_hour'] = str(month / 730)
            r['cost_off_usd_hour'] = str(month / 730)
            floor += month
    return {'currency': 'USD', 'complete': False, 'minimum_residual_usd_month': float(floor.quantize(Decimal('0.01'))),
            'known_on_usd_month':float(sum(Decimal(str(r['cost_on_usd_hour']))*730 for r in resources if r.get('cost_on_usd_hour') is not None).quantize(Decimal('0.01'))), 'compute_quote_observed_at':QUOTES['observed_at'], 'hours_per_month': 730, 'rate_card_date': '2026-11-07', 'sources': SOURCES,
            'message': 'Piso parcial: EKS em suporte padrão, ALB/NLB, NAT, EIPs alocados e EBS gp3. Exclui LCU/NLCU, processamento NAT, IPv4 adicionais dos balanceadores, snapshots, S3, logs, tráfego, consumo e Power Manager. Valores em USD antes de impostos/créditos; não é previsão da fatura.'}
