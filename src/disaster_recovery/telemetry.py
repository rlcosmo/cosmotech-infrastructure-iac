"""Bounded-label operational gauges; unknown recovery/loss never becomes zero."""
from .reconciliation import immutable, integer


def render_outbox(records, *, now_ms, report=None):
    pending=[];sent=0;attempts=0
    for row in records:
        immutable(row)
        if row['state']=='PENDING':pending.append(row)
        elif row['state']=='SENT':sent+=1
        else:raise ValueError('Unknown journal state')
        attempts+=integer(row.get('attempts',0))
    oldest=max([max(0,now_ms-integer(v['accepted_at']))/1000 for v in pending],default=0)
    values={'outbox_pending':len(pending),'outbox_published_records':sent,
            'outbox_publish_attempts_observed':attempts,'outbox_oldest_pending_age_seconds':oldest,
            'outbox_observed_timestamp_seconds':now_ms/1000}
    # Offline receipts/reconciliation are not automatically AWS-certified telemetry.
    if report is not None:
        values.update(reconciliation_missing_journal=len(report['missing_journal_ids']),
                      reconciliation_pending=len(report['pending_ids']),
                      reconciliation_unrecovered_acknowledgements=report['unrecovered_ack_count'],
                      reconciliation_receipt_coverage_verified=int(report['receipt_coverage_verified']))
        if report.get('message_loss_count') is not None:
            values['journal_loss_count_verified']=report['message_loss_count']
    return ''.join(f'cosmotech_dr_{key}{{region="us-east-2"}} {value}\n' for key,value in values.items())


def journal_fingerprint(records):
    import hashlib
    from .outbox import canonical
    rows=sorted([immutable(r) for r in records],key=lambda r:r['order_id'])
    return hashlib.sha256(canonical(rows).encode()).hexdigest()


def verified_report(envelope, records, now_ms):
    """Accept only fresh online-verified reports matching this exact journal view.

    Envelope provenance relies on the authorized read-only mount/verified artifact;
    a hash is not a signature. This never emits or certifies RTO/RPO.
    """
    if (envelope.get('evidence_source')!='aws-verified-reconciliation'
            or type(envelope.get('observed_at_ms')) is not int
            or not 0<=now_ms-envelope['observed_at_ms']<=900000
            or envelope.get('region')!='us-east-2'
            or envelope.get('journal_fingerprint')!=journal_fingerprint(records)):
        return None
    return envelope['report']
