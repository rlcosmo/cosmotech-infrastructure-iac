# P0 — observabilidade EKS indisponível em 09/11/2026

**Incidente não recuperado. Dependência de liberação da AWS demonstrada.** Coleta ao vivo aproximadamente 15:15–15:22 UTC. Esta execução fez somente leituras, testes de conectividade e documentação. Não alterou instalação do Zabbix, triggers, eventos, workloads, capacidade, IAM, volumes ou Power Manager. Nenhum recurso AWS foi criado. A atualização paralela do Zabbix está fora deste incidente.

[Evidências selecionadas, sem credenciais](2026-11-09-observability-p0.json).

## Estado observado

| Serviço/recurso | Antes informado | Depois verificado | Causa observada | Correção/condição de recuperação |
| --- | --- | --- | --- | --- |
| Nós EKS | Sem nós na AZ dos volumes | us-east-1a: 0 Ready/0 NotReady; us-east-1b: 2 Ready/0 NotReady | Os únicos dois workers estão em 1b | Repor capacidade elegível em 1a após liberação AWS |
| Managed node group/ASG | Degradado | DEGRADED; desired 3, min 1, max 3; dois InService/Healthy | AsgInstanceLaunchFailures / Fleet Requests | O ASG já tenta o terceiro nó; não se ampliou o desejado |
| Grafana | HTTP 503 | Pod Pending, HTTPS 503; zero endpoints | FailedScheduling: PV node affinity | Nó Ready em 1a, montagem/readiness, depois login e dashboards |
| Prometheus | HTTP 503 | StatefulSet Pending, API/rules HTTPS 503 | PVC em 1a e evento adicional Insufficient memory nos dois nós atuais | Capacidade na AZ correta e memória; depois PromQL e avaliação das regras |
| Tempo | HTTP 503 | Pod Pending, /ready HTTPS 503; zero endpoints | FailedScheduling: PV node affinity | Capacidade em 1a, montagem/readiness e confirmação de ingestão |
| OpenTelemetry Collector | Estado a verificar | Pod Ready e health HTTP 200; EKS conecta a 4317/4318 | Receiver funciona; exporter Tempo recebe connection refused | Restaurar Tempo; não redirecionar ou desativar o exporter |
| Endpoint de telemetria EKS (evento 121) | Missing | Item HTTP agent recebe 503 da API do Prometheus | O item cosmotech.eks.metrics consulta Prometheus, não a existência do Collector | Restaurar Prometheus e esperar coleta/avaliação normal |
| V2-002 | Pendente | Oito regras instaladas e GitOps Synced/Healthy; sem avaliação ao vivo validada | Prometheus/Grafana indisponíveis | Não certificado |
| V2-007 | Pendente | Portal 401 sem autenticação, 200 autenticado; observer unavailable | Lambda:GetFunction retorna AccessDeniedException | Inventário/classificação não certificados; não ampliar IAM |

Não há CrashLoopBackOff nos pods listados. Os três pods das ferramentas estão Pending e nunca chegaram a montar seus volumes nessa execução; não existe log atual de aplicação desses containers que demonstre recuperação. Avisos Kyverno sobre limites ausentes são evidência adicional, não foram tratados como a causa comprovada desses agendamentos.

## Bloqueio de capacidade e preservação

Node group `aws-eks-istio-lab-dev-spot-named`: SPOT, somente c7i-flex.large, duas subnets/AZs. ASG sem processos suspensos, distribuição 100% Spot e price-capacity-optimized. Atividades de 13:02, 13:34, 14:06, 14:38 e 15:10 UTC falharam com a mesma mensagem:

> You've reached your quota for maximum Fleet Requests for this account. Launching EC2 instance failed.

Cotas retornadas por Service Quotas: Standard Spot L-34B43A08 = 0; On-Demand Standard L-1216C47A = 0. São leituras reais, não pressupostos de configuração. Cotas de rate limiting CreateFleet/DescribeFleets não equivalem ao limite de quantidade mencionado no erro. A causa específica desse limite interno da conta não foi informada pela API. Não há evidência de uma correção local de IAM ou de configuração que libere o lançamento.

Não há worker EKS parado para retomar. As outras três EC2 em 1a são ITSM, Argo CD e Zabbix; não foram convertidas em workers. Não se terminou um nó saudável para tentar liberar quota, não se criou outro grupo/família para contornar a restrição e não se alterou a afinidade dos PVs.

Volumes ativos das ferramentas: Grafana vol-0d46fc5d30780ca02 (5 GiB), Prometheus vol-02c4cccd0b6088ca3 (10 GiB), Tempo vol-091bf80370bf3410f (10 GiB). Todos criptografados, available, sem attachments, em us-east-1a; PVCs/PVs Bound, reclaim Retain. Os volumes de recuperação também foram preservados. EBS exige instância na mesma AZ: remover a restrição no Kubernetes não muda essa limitação física.

Revisão financeira: preço Spot observado em 1a US$0,0314/h, equivalente a US$22,92 por 730h para um terceiro c7i-flex.large, além do disco gp3 de 20 GiB do launch template e rede. É amostra variável, não cotação garantida ou orçamento aprovado para expansão. Não houve aumento de capacidade/custos por esta execução. O desejado existente de três nós já está tentando se recompor automaticamente.

## Rede, telemetria e regras

DNS dos hosts corretos resolve; TLS verifica hostname/cadeia e certificado válido até 19/05/2027. Os VirtualServices apontam para os Services corretos. As respostas 503 são `no healthy upstream`, consistentes com EndpointSlices vazios. O host Prometheus correto é `prometheus-aws-eks-istio-lab-dev.cosmotechsolutions.com.br`; não usar o alias curto como prova de disponibilidade.

O Collector é alcançável a partir do container da Orders API nas portas OTLP 4317/4318. A comparação dos contadores mostrou +56 pontos de métricas aceitos via OTLP e +56 enviados ao destino Zabbix, sem novos erros nesse ramo na janela medida. Não houve aumento de traces/logs nesse intervalo; contadores cumulativos não foram usados para alegar ingestão recente desses sinais. O ramo Tempo mostrou conexão recusada e descarte explícito de quatro spans na amostra de logs. A disponibilidade do receiver não certifica persistência no Tempo.

As oito regras `cosmotech-v2-sqs-workers` estão instaladas, com seleção release=prometheus: EvidenceUnavailable, Backlog, OldMessages, DLQ, WorkerFailures, WorkerRetries, WorkerSlow e WorkerScrapeUnavailable. O Argo CD informa Synced/Healthy para a aplicação dessas regras e para API/Worker, revisão 8fd239b73354e9c186c0a4a6c713590cd0abcc91. Isso não substitui a avaliação pelo Prometheus ausente.

A coleta SQS retornou sucesso, idade de evidência 146s, backlog/inflight/idade de mensagens/DLQ zero. O endpoint Worker expõe processing_seconds_count/sum, ambos zero na amostra. Não se produziram nem consumiram mensagens para este diagnóstico. Dashboard autenticado, PromQL, regras carregadas/avaliadas e DLQ ponta a ponta continuam sem certificação operacional.

## Zabbix e portal

Eventos 118, 119, 120 e 121 foram consultados duas vezes, separados por 275 segundos. Todos permaneceram sem recuperação (`r_eventid=0`), com triggers em problema. Nenhum evento foi reconhecido/fechado manualmente e nenhum threshold/trigger foi alterado. Não existe janela de estabilidade pós-recuperação, pois ela ainda não ocorreu.

O portal autentica corretamente, mas api/comparison informa unavailable, zero recursos/cobertura e erro Lambda:GetFunction / AccessDeniedException. Uma leitura GetFunction direta, como root, confirmou o mesmo erro com mensagem `None`. Organizations retornou AWSOrganizationsNotInUseException: a conta não pertence a uma Organization. Não foi comprovada uma causa SCP; tampouco se atribuiu o erro a uma política insuficiente do observer. Políticas já comprovadas suficientes não foram ampliadas. O validate-portal não pode ser certificado com esse inventário indisponível.

## Retomada após liberação AWS

1. Resolver com a AWS o bloqueio Fleet e as cotas efetivas; não basta alterar desired/min/max. Nenhum chamado foi consultado ou aberto nesta execução.
2. Acompanhar o retry já existente do ASG e confirmar nó Ready em us-east-1a, preservando os dois nós atuais e os volumes. Confirmar requests/allocatable e ausência de falhas de montagem.
3. Confirmar Grafana, Prometheus e Tempo Running/Ready; validar login/datasources/dashboards, PromQL e as oito regras carregadas e saudáveis, /ready e traces reais no Tempo.
4. Confirmar métricas recentes e entrega EKS→Collector→Tempo; não apagar filas/buffers/histórico para obter readiness.
5. Verificar recuperação automática dos quatro eventos originais e observar pelo menos 15 minutos sem recorrência. Só então certificar V2-002.
6. Após liberação independente de Lambda:GetFunction, repetir inventário/classificação/filtros e validate-portal para V2-007.

Referências oficiais: [EBS e AZ](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html), [diagnóstico EKS](https://docs.aws.amazon.com/eks/latest/userguide/troubleshooting.html). CI/documentação aprovados não significam recuperação de serviço.
