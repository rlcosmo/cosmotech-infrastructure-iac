# V2-003 — Disaster Recovery multi-região e Chaos Engineering

A issue #47 substitui o scaffold inicial por infraestrutura e GitOps isolados,
backup/restore, planejamento de recuperação e validação offline. **Não há DR
provisionado nem RTO/RPO real certificado enquanto a conta estiver restrita.**

- [Arquitetura, custos, ownership e limites](dr/architecture.md)
- [Runbooks de preparação, failover/failback e caos](dr/runbooks.md)
- [Cotações e disponibilidade regional consultadas](dr/regional-evidence-2026-11-08.json)
- [Comparação de custos com premissas](dr/cost-comparison.json)
- [Estado verificável da entrega](dr/status.json)

Região selecionada: us-east-2. Recursos ficam desabilitados por padrão, teto
financeiro zero, workers passivos e nenhuma alteração no primário/Power Manager.
A fila SQS não possui replicação nativa e o produtor atual não possui outbox:
perda da fila sem journal tem RPO não limitado; promoção real deve permanecer
bloqueada. Testes simulados não substituem restore e ensaio real na região secundária.
