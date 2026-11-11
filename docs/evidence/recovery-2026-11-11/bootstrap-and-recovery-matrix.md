> Evidência histórica de preparação/bootstrap. O role temporário foi revogado posteriormente; estado atual em ../../recovery-2026-11-11.md.

# Bootstrap e matriz de recuperação — 11/11/2026

## Bootstrap concluído

- Role: `arn:aws:iam::414053644145:role/cosmotech-lab-recovery-20261111`.
- Principal exclusivo na trust: `arn:aws:iam::414053644145:user/serverless-lab-admin`. O role de manutenção não é o principal da trust; o usuário autorizado assume o role diretamente.
- Root executou somente CreateRole e PutRolePolicy. Sem alteração de storage, instâncias ou Kubernetes.
- STS confirmou assumed-role/cosmotech-lab-recovery-20261111/recovery-authorized-20261111.
- Política inline: bounded-recovery-20261111. Sem IAM, AdministratorAccess, exclusões ou recursos Ohio.
- Trust e TODOS os Allows expiram em 12/11/2026 02:41:39 UTC (11/11 23:41:39 em São Paulo), prazo original de seis horas contado da preparação. Bootstrap às 21:56 UTC não estendeu o prazo.
- STS atual expira antes, às 22:56:29 UTC. A expiração das permissões não depende desse vencimento STS.
- Testes de API: inventários de volumes/snapshots/instâncias OK; CreateVolume dry-run aprovado com snapshot e tag revisados; dry-run sem tag negado. Nenhum recurso criado nesses testes.
- Esses testes não comprovam uso da chave KMS nem integridade de aplicação.

## Matriz completa

Todos os EBS originais abaixo permanecem ausentes. Todos os snapshots abaixo foram reconfirmados pelo role, completed, criptografados, com tamanho compatível. Destino 1b é uma PROPOSTA para novos volumes; não altera nem falsifica a AZ dos volumes antigos.

| PV | PVC | EBS original | Snapshot | Data UTC | GiB | AZ proposta | USD/mês |
|---|---|---|---|---|---:|---|---:|
| cosmotech-p0-grafana-1b | observability/grafana-p0-1b | vol-0426070ae45055fce | snap-0878acb54971e60c9 | 2026-11-09T18:42:06.443000+00:00 | 5 | us-east-1b | 0.40 |
| cosmotech-p0-prometheus-1b | observability/prometheus-p0-1b | vol-0d761779801b75422 | snap-0c7403c6a7dcbce14 | 2026-11-09T18:42:07.650000+00:00 | 10 | us-east-1b | 0.80 |
| cosmotech-p0-tempo-1b | observability/tempo-p0-1b | vol-0490f87ab26f9139a | snap-0c530f7f528d6869a | 2026-11-09T18:42:08.881000+00:00 | 10 | us-east-1b | 0.80 |
| cosmotech-recovered-alertmanager | cosmotech-recovery/recovered-alertmanager | vol-01207f7fc38232494 | snap-05d24cc4845f2f6f4 | 2026-11-09T18:39:11.602000+00:00 | 2 | us-east-1b | 0.16 |
| cosmotech-recovered-grafana | cosmotech-recovery/recovered-grafana | vol-0b4cf34ff357f3ac9 | snap-0739e76111a6e6078 | 2026-11-09T18:39:09.129000+00:00 | 5 | us-east-1b | 0.40 |
| cosmotech-recovered-loki | cosmotech-recovery/recovered-loki | vol-05f8c7f1042ef61b5 | snap-067e30a0fc3751d76 | 2026-11-09T18:39:06.704000+00:00 | 10 | us-east-1b | 0.80 |
| cosmotech-recovered-prometheus | cosmotech-recovery/recovered-prometheus | vol-028cab9754b684eab | snap-03648b89842f00e1e | 2026-11-09T18:39:07.919000+00:00 | 10 | us-east-1b | 0.80 |
| cosmotech-recovered-tempo | cosmotech-recovery/recovered-tempo | vol-0fe1c28a1558412a4 | snap-06fcb6d602af9fe45 | 2026-11-09T18:39:10.383000+00:00 | 10 | us-east-1b | 0.80 |
| cosmotech-tempo-consistent-restore | cosmotech-recovery/recovered-tempo-consistent | vol-048aa25041c575363 | snap-0cbc47bc3ace48320 | 2026-11-09T18:39:12.834000+00:00 | 10 | us-east-1b | 0.80 |
| pvc-04ab09a4-1904-49e0-9534-2a5096946455 | loki/storage-loki-0 | vol-0bd7dff7f7b802bc8 | snap-08631b45f37025308 | 2026-11-09T18:42:05.232000+00:00 | 10 | us-east-1b | 0.80 |
| pvc-18505c39-d15f-4102-a4a3-856cf5bd264a | observability/alertmanager-prometheus-kube-prometheus-alertmanager-db-alertmanager-prometheus-kube-prometheus-alertmanager-0 | vol-0a95a484ba0ce9518 | snap-004a2affa2fdefe27 | 2026-11-09T18:42:04.033000+00:00 | 2 | us-east-1b | 0.16 |
| pvc-418ecba8-550c-46cc-9180-d204ca98106e | observability/storage-tempo-0 | vol-091bf80370bf3410f | snap-0a28bbd5d748b8b35 | 2026-11-09T18:39:03.182000+00:00 | 10 | us-east-1b | 0.80 |
| pvc-4455891c-7f38-45bd-a63e-d81cad53af23 | observability/prometheus-prometheus-kube-prometheus-prometheus-db-prometheus-prometheus-kube-prometheus-prometheus-0 | vol-02c4cccd0b6088ca3 | snap-03ea753f855f09836 | 2026-11-09T18:39:04.343000+00:00 | 10 | us-east-1b | 0.80 |
| pvc-bd4cc904-d502-43e2-a264-8c782c162e25 | observability/grafana-data | vol-0d46fc5d30780ca02 | snap-0dc1eb6f11188110c | 2026-11-09T18:39:05.544000+00:00 | 5 | us-east-1b | 0.40 |

## Estimativa e sequência proposta

- Preço público AWS consultado: gp3 us-east-1 USD 0.08/GB-mês, publicação 2026-11-10T17:38:52Z.
- Restaurar os 14 candidatos: 109 GiB, USD 8.72/mês; USD 0.2907/dia em mês de 30 dias.
- Primeiro lote recomendado: Grafana P0, Prometheus P0, Tempo P0, Loki e Alertmanager: 37 GiB, USD 2.96/mês. Os outros nove são históricos/alternativos; não criar 14 volumes duplicados por padrão.
- Roots Argo/Zabbix: 40 + 120 GiB em us-east-1a, USD 12,80/mês adicionais. Primeiro lote + roots: 197 GiB, USD 15,76/mês de EBS.
- Estimativa incremental de armazenamento, com gp3 baseline 3000 IOPS/125 MB/s; não inclui EC2, KMS, snapshots/Backup existentes, impostos, inicialização paga/FSR ou outros serviços. Nenhum desconto/crédito presumido.
- Fonte pública exata: https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ebs.json
- Página de preços: https://aws.amazon.com/ebs/pricing/

## Restauração ainda não executada

- Sem CreateVolume real, attach, StartInstances, patch/deletion de PV/PVC ou provisionamento Ohio.
- Validar filesystem e aplicação após criar novos volumes, antes de conectar consumidores. Completed/encryption não provam consistência.
- Preservar snapshots e PVCs históricos; volumeHandle de PV e volumeName de PVC bound não serão forçados. Preparar novos bindings Retain e revisar consumidor/Helm/operator.
- Corrigir seletores de nomes de nós antigos usando a topologia real dos novos volumes; não forçar PV de uma zona para outra.

## Revogação ao final

- Recuperação ainda não concluída: remoção física da política e teste final de negação NÃO foram executados.
- O role NÃO tem IAM; por isso não pode remover sua própria política. A autorização atual permite root apenas no bootstrap; não executar remoção posterior com root sem nova autorização.
- Para cumprir a remoção física ao encerrar, usar um role administrativo autorizado com iam:DeleteRolePolicy limitado a este role, ou aprovar previamente esse mecanismo. Não ampliar a política atual silenciosamente.
- Mesmo sem remoção física, as condições DateLessThan cortam todos os Allows às 02:41:39 UTC, inclusive de sessões emitidas antes. Após o encerramento, validar CreateVolume --dry-run negado e registrar auditoria; não usar GetCallerIdentity como prova de revogação.
