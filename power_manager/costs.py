"""Partial residual floor; published AWS us-east-1 rate card verified 2026-11-07."""
from decimal import Decimal
RATES = {'EKS': Decimal('0.10'), 'ALB': Decimal('0.0225'), 'NLB': Decimal('0.0225'), 'NAT': Decimal('0.045'), 'EIP': Decimal('0.005')}
SOURCES = ['https://aws.amazon.com/eks/pricing/', 'https://aws.amazon.com/elasticloadbalancing/pricing/',
           'https://aws.amazon.com/vpc/pricing/', 'https://aws.amazon.com/ebs/pricing/']

def estimate(resources):
    floor = Decimal(0)
    for r in resources:
        if r['type'] in RATES:
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
            'hours_per_month': 730, 'rate_card_date': '2026-11-07', 'sources': SOURCES,
            'message': 'Piso parcial: EKS em suporte padrão, ALB/NLB, NAT, EIPs alocados e EBS gp3. Exclui LCU/NLCU, processamento NAT, IPv4 adicionais dos balanceadores, snapshots, S3, logs, tráfego, consumo e Power Manager. Valores em USD antes de impostos/créditos; não é previsão da fatura.'}
