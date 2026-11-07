# V2-003 — preparação ativo-passivo (sem provisionamento)

## Terraform e custos

`terraform/environments/dr` usa estado separado `aws-serverless-lab/dr/terraform.tfstate`, sem migrar/copiar o state primário. Provider alias standby; candidato us-east-2 não é seleção de menor custo. Reutiliza o módulo SQS existente, desabilitado por padrão, com visibilidade 180s, cinco recebimentos, retenção 4d e DLQ 14d. Nenhum consumidor standby é iniciado. Testes Terraform com provider mock verificam zero recursos por padrão, ausência de teto e estimativa acima do teto. Não existe workflow de apply DR.

O teto padrão é zero. Habilitação exige teto explícito positivo e estimativa total positiva dentro dele; isso é uma trava de configuração, não limite de faturamento AWS. Plano atualizado, revisão de segurança/IAM, GMUD e executor oficial continuam obrigatórios. Não aplicar os exemplos nem o snapshot histórico. Custos de filas, dados e tráfego não são garantidos como zero só por serem on-demand.

Comparar us-east-2/us-west-2/sa-east-1 contra us-east-1 com cotações atuais e mesma carga. `src/disaster_recovery/evidence.py` calcula totais Decimal e rejeita componentes ausentes, preços negativos/NaN, dados sem fonte ou com mais de sete dias e regiões incompatíveis. Componentes: EKS, compute, rede fixa (NAT/ALB/health checks), armazenamento/backups, replicação DynamoDB, ECR, Secrets Manager, tráfego inter-regional e observabilidade. Os testes usam **preços fictícios**, não comprovam a região mais barata. Faltam cotações e volume de replicação/tráfego reais.

## Ownership e continuidade

A arquitetura ativa usa EKS; o root dev também contém recursos Lambda legados. Não replicar indiscriminadamente esse root. A tabela ativa EKS usa `order_id`; o módulo DynamoDB legado usa `pk/sk`. Alterar/recriar a tabela via esse módulo destruiria o contrato de dados. Primeiro identificar o state proprietário da tabela ativa, configuração PITR/streams e protocolo de lease. Adicionar réplica no mesmo proprietário primário; não gerenciar a mesma tabela global nos dois states.

DynamoDB: escolher modo de consistência/regiões compatíveis após analisar conditional writes, transações e leases atuais. Em replicação eventual, uma escrita condicional local não fornece exclusão global; exigir fencing antes de ativar o passivo. Global Tables não substitui um journal de aceite/replay e não comprova RPO zero. Preservar dados e testar restauração isolada antes da mudança.

SQS: filas são regionais. Implementar outbox durável de aceite com ID imutável, status de publicação e checkpoint; replay no destino só após fencing e validação do checkpoint. Aceite HTTP deve significar persistência durável; não prometer RPO zero se o único registro estiver na fila/região perdida. Reentregas/replay devem preservar idempotência dos efeitos em estoque/pagamento/entrega. Nunca consumir a fila V1 para um ensaio DR.

ECR: replicação deve ser configurada no proprietário do registry de origem, filtrada aos repositórios Orders; não criar uma segunda configuração global no state DR. Imagens existentes exigem estratégia explícita de cópia/republicação; verificar digest imutável e políticas de scanning no destino. Não trocar tag mutable durante failover.

Secrets Manager: replicar somente os segredos de runtime necessários; não exportar valores para código/state/logs. Verificar chave KMS de destino e dependências regionais dos valores. Promoção de réplica altera seu ciclo de vida e exige revisão; credenciais de terceiros podem não aceitar a segunda região. Não replicar chaves de administração por conveniência.

Roteamento: registrar endpoint passivo saudável somente após restauração e teste ponta a ponta. Health check deve provar aceite durável + processamento + leitura do resultado; readiness de pod não é recuperação. DNS não faz fencing e TTL não força clientes a expirar cache. Atualizações de Route 53, health checks, certificates e WAF pertencem ao fluxo IaC aprovado.

## Failover e failback

1. Abrir/manter GMUD pelo GLPI/Kestra existente e capturar baseline, último aceite durável e checkpoint replicado.
2. Confirmar indisponibilidade de ponta a ponta e cessar novas escritas/consumo no primário por mecanismo de fencing verificável. Se não for possível provar fencing, bloquear promoção automática.
3. Verificar dados/chaves, digests, secrets, dependências e capacidade no destino. Reproduzir outbox com IDs originais e validar nenhum efeito duplicado.
4. Ativar o único consumidor/escritor autorizado no passivo pelo GitHub → CI → Terraform/GitOps. Alterar roteamento somente depois de aprovado e saudável.
5. Encerrar RTO após teste ponta a ponta bem-sucedido, não após resposta DNS ou pod Ready. Registrar RPO contra o último pedido confirmado ao cliente.
6. Failback é outra mudança: estabilizar primário, sincronizar alterações reversas, drenar destino com fencing, verificar dados/checkpoints e só então retornar tráfego. Não manter dois Argo CD gerenciando o mesmo ambiente.

## Chaos e evidências

Primeiro ensaio isolado: recursos e filas com tag Environment=dr-test; duração máxima 120s; somente pedidos sintéticos; interrupção ao perder telemetria, violar limite de custo ou afetar namespace/VPC primário. Cenários: atraso de réplica, indisponibilidade do endpoint primário isolado, worker indisponível, checkpoint antigo e entrega duplicada. Fault Injection Service ou alteração de rede só após revisão de targets/stop conditions e GMUD; nenhum experimento real foi executado.

Contrato de telemetria proposto: probe_success{job="orders-dr-e2e",region}, cosmotech_dr_rto_seconds, cosmotech_dr_rpo_seconds e cosmotech_dr_outage_started_timestamp_seconds. Emitir somente evidências verificadas, com região/ambiente e timestamp; jamais preencher ausências com zero. `recovery_metrics` testa RTO, RPO e indisponibilidade contínua em timestamps simulados. O dashboard preparado não comprova exportador, replicação ou failover real.

## Bloqueios restantes

Aprovação PR #2; credenciais temporárias/role de observação; ownership da tabela e de EKS; limites de gastos e cotações completas; implementação/validação da outbox e fencing; provisionamento de EKS/rede/rotas/replicação pelo pipeline. Esta entrega é scaffold e runbook, não DR operacional. Nenhum dado, recurso ou região foi apagado/provisionado.
