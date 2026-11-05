# Readiness das aplicações e disponibilidade no ALB

## Diagnóstico

O evento reportado `Readiness probe failed: HTTP probe failed with statuscode: 503` corresponde ao worker `orders-worker-75cb9bbc7f-qlxzb`, em 2026-11-05 às 05:36:22 UTC. A aplicação iniciou às 05:36:10; a primeira readiness ocorreu antes de terminar o long polling SQS de 20 segundos. O pod ficou Ready às 05:36:32, sem reinícios. Na investigação posterior, ambos os pods estavam Ready e os dois hosts responderam HTTPS 200. Não foi observada indisponibilidade persistente ou erro de fila nesse período.

Readiness 503 durante falha real do SQS continua sendo intencional. A readiness não deve aprovar o worker antes de uma consulta de fila bem-sucedida. Remover a probe, usar sempre 200 ou avaliar somente liveness esconderia indisponibilidade real.

## Correção

A configuração oficial permanece em `deploy/dev` dos repositórios individuais API e worker. A startup probe do worker verifica `/health/ready`, com primeira tentativa após 25 segundos para acomodar o primeiro long polling normal e orçamento limitado para inicialização. A API usa startup probe em `/health/live`. Readiness e liveness normais começam depois da startup probe. As probes continuam com timeout e falhas limitadas; erros reais após startup continuam retirando o pod do serviço.

`ops/kubernetes/orders-ingress-readiness.json` adiciona exclusivamente a label `elbv2.k8s.aws/pod-readiness-gate-inject=enabled` aos namespaces existentes `orders-api` e `orders-worker`. Aplicar com server-side apply, field manager `cosmotech-ingress-readiness`, antes da criação dos novos pods. Não remover labels Istio, de squad ou ambiente. Os Services, Ingresses e TargetGroupBindings devem existir antes dos novos pods para a injeção da gate. A mudança de label sozinha não altera pods existentes.

As aplicações mantêm `maxUnavailable=0`, `maxSurge=1` e dez segundos de prontidão estável. A readiness gate do ALB somente permite terminar o pod anterior quando o alvo novo estiver saudável no target group, evitando a janela de targets Initial/Draining em aplicações com uma réplica. Não muda o HPA, réplicas mínimas, segurança de rede ou acesso IAM das aplicações.

Documentação primária:

- https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/deploy/pod_readiness_gate/
- https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

## Verificação e recuperação

Validar primeiro a configuração nos repositórios individuais e os testes existentes de saúde, processamento, retries e idempotência. Fazer push em main, aguardar teste/build/scan/publicação/promoção, Argo Synced/Healthy e health externo. O GitOps contém o espelho gerado e a proveniência do SHA testado; não editar esse espelho manualmente.

Nos novos pods, conferir a condition `target-health.elbv2.k8s.aws/*`, ContainersReady, Ready e ausência de repetição de readiness 503. Eventos antigos de pods/ReplicaSets anteriores podem continuar visíveis no Kubernetes até expirar; eles não são removidos para simular sucesso. Medir HTTPS durante a troca de pods para verificar disponibilidade no host.

Se a startup do worker não passar dentro do orçamento, verificar os eventos, `queue_poll_failed`, Pod Identity e acesso à fila antes de ampliar temporizações. Se containers estiverem prontos mas a gate permanecer False, verificar TargetGroupBinding, target health, healthcheck path/port e rede ALB→pod. Não desabilitar a gate para forçar um rollout a concluir. Recuperar revertendo a mudança pelo repositório da aplicação e acompanhar a reconciliação; preservar a réplica anterior saudável.

## Evidência após implantação

Em 2026-11-05, API Actions `41981833112` e worker Actions `41981834100` concluíram com sucesso. A entrega GitOps `41982067249` concluiu com validação, Argo e health externo aprovados. Os pods `orders-api-6b8d9f8c74-22dbp` e `orders-worker-7f84f6cd7d-d7mhq` receberam as respectivas gates `target-health.elbv2.k8s.aws/*`, atingiram Ready e ALBReady, sem reinícios. A observação mostrou a réplica antiga permanecendo ativa enquanto a gate nova estava False; ela só foi removida após o alvo novo ficar healthy.

Foram realizadas 99 consultas HTTPS de readiness em cada aplicação durante 245 segundos, cobrindo a troca dos pods: todas as 198 respostas foram HTTP 200. O evento original de readiness 503 permaneceu somente no histórico do pod anterior; os novos pods apresentaram apenas a tentativa inicial de startup do próprio sidecar Istio antes de sua porta abrir, sem novo readiness 503 das aplicações durante a validação. O controller instalado é AWS Load Balancer Controller `v3.6.2`; a label de injeção está documentada na referência dessa versão.
