# Lab V2 — execução e limites

## V2-001 (parcial)

Portal local em `ops/iac-portal/index.html`, sem dependências, API de escrita ou credenciais. Abrir no navegador e carregar apenas `report.json`. O relatório projeta exclusivamente endereço/tipo/região, drift do refresh-only e candidatos do inventário. State, valores, outputs, tags e conteúdo das mudanças nunca são publicados. Dados de inventário e endereços de recursos são informação operacional: manter acesso restrito aos artefatos.

`iac-evidence.yml` agenda observação a cada seis horas, com OIDC do papel de plan existente, sem apply, sem escrita de state e sem bloqueio remoto. Histórico: artefatos por execução, retenção de 14 dias. Falhas de permissão interrompem a coleta; não geram relatório indicando ausência de drift. Um refresh-only concorrente com apply não é snapshot transacional: confirmar resultado depois de aplicações. Nenhuma aprovação GMUD/GLPI/Kestra ou pipeline existente foi alterada.

Inventário de Resource Groups Tagging API é **parcial**, incluindo apenas serviços suportados e recursos etiquetados ou anteriormente etiquetados. Candidatos podem pertencer a outros states. Recursos sem tags exigem coletores específicos por serviço e escopo autorizado; não há alegação de cobertura completa. `last_apply` fica desconhecido até integração com evidência assinada do executor oficial; não derivar apply bem-sucedido de plan ou de CI verde.

Pendências de aceite: executar workflow pelo CI após revisão, verificar permissões de leitura do role para refresh e `tag:GetResources`, integrar cobertura por serviço, publicação autenticada via pipeline canônico, evidência do último apply, dashboard histórico. Não hospedar relatório real em Site público ou anexar state/plan aos artefatos.

## V2-002 (aguardando V2-001)

A inspeção do Worker canônico encontrou instrumentação existente em `orders/runtime/worker_metrics.py`: histogramas de processamento e contadores de resultados, retries, erros, heartbeat e inflight. Reutilizar essa fonte; não criar métricas duplicadas. Dashboards/alertas e testes integrados ainda não executados nesta entrega.

## V2-003 (aguardando etapas anteriores e orçamento)

Não há teto monetário definido para uma segunda região. Nenhuma infraestrutura DR foi provisionada ou região declarada mais barata. Comparação deve incluir EKS, EC2 ferramentas, NAT, ALB, armazenamento, logs, tráfego inter-regional, replicação de imagens/secrets e DynamoDB antes da seleção. Créditos não são limite de gasto.

SQS não replica mensagens automaticamente entre regiões. Continuidade requer outbox/replay durável e idempotência; não usar duplicação de consumidores como replicação. Validar impacto de Global Tables no protocolo de lease e impedir escrita ativa simultânea antes do failover. States separados não substituem coordenação sobre recursos globais. Chaos deve iniciar isolado e com interrupção automática; nenhum experimento foi executado contra o V1.

## Validação desta entrega

`python3 -m unittest discover -s tests -v`: oito testes aprovados, incluindo drift recursivo, redação de valores sensíveis, inventário ausente, cobertura parcial e isolamento de região. Sem mudanças Terraform, IAM, dados ou runtime do V1. Sem recursos cloud novos. Custo de recursos novos: US$ 0; minutos/artefatos GitHub e chamadas AWS do workflow dependem da quota/uso existente, não estimados como gratuitos. Não houve validação de navegador ou integração AWS.

## Evidência remota e bloqueio de ativação

CI do commit inicial aprovado: https://github.com/rlcosmo/cosmotech-infrastructure-iac/actions/runs/42366279454 — testes, build dos artefatos Lambda, fmt, init sem backend e validate Terraform concluídos com sucesso.

PR de revisão: https://github.com/rlcosmo/cosmotech-infrastructure-iac/pull/2 (draft). A tentativa de acionar `iac-evidence.yml` na branch retornou HTTP 404: o novo workflow ainda não está registrado na branch padrão. Sua ativação depende da incorporação pelo fluxo de revisão existente; nenhum merge ou alteração de proteção foi realizado. A variável do papel OIDC de plan existe; isso não comprova permissão de refresh/inventário. A execução autenticada e a URL do portal permanecem pendentes. A ordem V2-001 → V2-002 → V2-003 foi preservada.

## Continuação — revisão de 07/11/2026

Esta seção substitui o status inicial acima. PR #2 continua aberto, sem conflitos e com checks aprovados no head anterior, mas **sem reviews**. O pedido condiciona merge à aprovação; nenhum merge foi executado e nenhuma aprovação foi simulada.

Correção preparada para o 404: entrada `workflow_dispatch` do `iac.yml` já registrado passa a aceitar `evidence=true` e chamar o workflow reutilizável. Após aprovação/incorporação, acionar `gh workflow run iac.yml --ref main -f evidence=true`. O coletor permanece restrito à branch main, preservando a trust OIDC existente. A trust e as permissões do papel de plan foram inspecionadas: esse papel aceita somente main e não oferece leitura suficiente para refresh completo, inventário, faturamento ou Free Tier. Não alargar a trust para executar código de branches.

Novo requisito explícito: `IAC_EVIDENCE_ROLE_ARN`, um papel de observação revisado com permissões efetivas para os recursos do state e inventário. Não reutilizar silenciosamente o papel de plan. A configuração desse papel continua bloqueada pelo fluxo de IaC/revisão. A sessão local disponível usa credencial permanente; não foi usada para operações AWS, pois o projeto exige credenciais temporárias baseadas em role.

Inventário ampliado para ECR, Lambda, ELB, RDS, metadata de Secrets Manager, SQS, DynamoDB, EKS e instâncias EC2, incluindo recursos sem tags nesses escopos. Paginação automática da CLI; SQS usa `MaxResults=1000`, máximo de 200 consultas de metadata por execução. Cobertura e falhas são explícitas por serviço. Outros tipos e recursos de outros states continuam fora da conclusão de ownership: candidatos não são automaticamente importados.

Portal com servidor em loopback, autenticação Basic obrigatória atrás do proxy HTTPS existente e histórico de até 56 relatórios. `IAC_CREDENTIAL_SHA256` contém SHA256 de `usuario:senha` com senha aleatória forte, provisionado fora de Git/state; `IAC_REPORT_DIRECTORY` aponta somente para relatórios sanitizados. Testes HTTP verificam 401, leitura autenticada, rejeição de escrita/traversal, histórico e evidência corrompida. Ainda não implantado no proxy real.

Como este repositório é público, os artefatos operacionais passam a ser criptografados com o destinatário público `IAC_EVIDENCE_GPG_PUBLIC_KEY`; a chave privada fica exclusivamente no runtime autorizado do portal. Não publicar relatórios plaintext nos artefatos. O transporte/decriptação até o portal ainda depende do executor aprovado; nunca baixar/decriptar state ou plan para o diretório servido.

Último apply segue desconhecido: os workflows presentes não têm executor de apply nem recibo verificável. `S3 LastModified`, CI verde e drift observado não comprovam sucesso do último apply. Conectar recibos do executor oficial, associados a commit, ambiente, state e aprovação GMUD, antes de preencher esse campo. Nenhum histórico foi inventado.

## Evidências consolidadas da continuação

- Testes Python IaC ampliados (19 casos), incluindo inventário metadata/sem tags, limitação de consultas, isolamento, ausência de plan e autenticação/histórico via HTTP real local.
- CI Terraform dev/DR e três testes mock de orçamento aprovados em https://github.com/rlcosmo/cosmotech-infrastructure-iac/actions/runs/42367358896. Nenhuma chamada AWS dos testes mock.
- Dispatch real de `iac.yml` com `evidence=true` aceito na branch do PR, sem HTTP 404: https://github.com/rlcosmo/cosmotech-infrastructure-iac/actions/runs/42367424412. Coleta cloud é intencionalmente restrita a main; dispatch aceito não comprova inventário.
- Criptografia GPG do artefato validada por roundtrip com chave temporária local; nenhum segredo real foi usado/exposto.
- V2-002 preparado em https://github.com/rlcosmo/cosmotech-observability/pull/1: 26 testes locais e oito regras/fixtures promtool aprovados no CI. Testes existentes do Worker (12 casos, incluindo falha/retry/duplicata) aprovados localmente, sem alteração do Worker.
- Fonte de entrega do coletor SQS sincronizada no documento SSM existente via Terraform. Sem execução SSM fora do pipeline.
- DR: scaffold desabilitado, state isolado e runbook em lab-v2-dr.md; sem EKS secundário, replicação, failover ou Chaos real.
- Créditos: diagnóstico de sessão temporária ausente/expirada; Bedrock/RDS não executados por falta de elegibilidade verificável e teto/ciclo de vida para RDS. Detalhes em lab-v2-credits.md.

O V2-001 ainda não atende aceite operacional: falta aprovação do PR, role de observação, configuração do destinatário GPG, publicação autenticada/ingestão de relatórios, recibo do apply oficial e coleta AWS real. V2-002 também exige implantação pelo proprietário GitOps/Helm e validação de queries/alertas reais. Nenhum recurso cloud novo, apply, merge ou custo recorrente de infraestrutura foi criado nesta execução.

## Estado atual após autorização de merges (substitui os bloqueios antigos de revisão)

O proprietário autorizou merges automáticos do Lab V2. IaC #2 foi mesclado em `6177376`; Observabilidade #1 em `c877930`. Ambos tinham CI aprovado, ausência de conflitos e verificações de diff/segredos sem achados nos padrões examinados. Não havia proteção/review obrigatório configurado nas branches, e nenhum mecanismo foi desabilitado/burlado. Autorização persistida em lab-v2-merge-policy.md.

CI main pós-merge aprovado em ambos os repositórios. Workflow IaC de evidências acionado em main sem 404, porém falhou corretamente no preflight por ausência de IAC_EVIDENCE_ROLE_ARN e IAC_EVIDENCE_GPG_PUBLIC_KEY. O executor CI existente tem permissão de plan/leitura restrita, não de criação/atualização de IAM ou implantação. Não foi substituído por credencial administrativa permanente.

Foi identificado e utilizado o caminho já existente de STS AssumeRole para a role de manutenção: sessão de 900s, credenciais apenas em memória. Portanto, a falta do login local temporário não impede toda observação: CloudWatch e pods foram consultados legitimamente. Essa role recebeu AccessDenied ao ler atributos SQS e Forbidden para ConfigMap/PodMonitor/PrometheusRule. O endpoint privado Prometheus não foi alcançável deste executor. Grafana /api/health respondeu 200; a API do dashboard V2 respondeu 401. Isso não confirma existência/ausência do dashboard, apenas a necessidade de validação autenticada ainda não disponível.

A observação real encontrou gauges recentes da fila principal (backlog/in-flight/idade zero), mas DLQ sem datapoints. O coletor foi corrigido no PR Observabilidade #2 (`bbfd24c`) para buscar a contagem real da DLQ por GetQueueAttributes com o grant ITSM já declarado, mantendo estado desconhecido quando negar/falhar. Fontes SSM sincronizadas nesta alteração; sem execução SSM manual. Testes canônicos de observabilidade: 27 aprovados.

Plan source-only pós-merge aprovado: 0 adições, 8 atualizações, 0 remoções, com refresh=false. Inclui alterações pendentes de Lambda, WAF e documentos de gerenciamento fora da publicação V2. Não aplicar esse plano como se fosse uma mudança isolada/aprovada; não houve apply. Portal/publicação, role de evidências, chave destinatária e deploy autenticado dos dashboards continuam dependentes do executor Terraform/GitOps autorizado, com escopo de plano revisado.

Evidências sanitizadas e URLs: docs/evidence/lab-v2-post-merge-2026-11-07.json. Nenhum recurso cloud novo, mensagem consumida, mudança de RBAC/IAM, publicação manual no Grafana ou custo recorrente novo de infraestrutura. O portal ainda não tem URL publicada; V2-001/V2-002 não foram declarados operacionalmente concluídos.
