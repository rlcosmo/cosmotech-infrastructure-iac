# Publicação Lab V2 — escopo, configuração e bootstrap

## Fonte autorizada

A publicação reutiliza a EC2 ITSM, o ALB, DNS, certificado, proxy HTTPS e Grafana existentes. Endpoint pretendido: `https://kestra.cosmotechsolutions.com.br/iac/`. Dashboard pretendido: `https://grafana.cosmotechsolutions.com.br/d/cosmotech-v2-sqs-workers`. São destinos declarados; não comprovam implantação enquanto os passos install/validate não concluírem.

`lab-v2-publication.yml` possui fases explícitas:
- `plan`: plano somente de publicação, sem refresh e sem apply, usando a role de plan existente.
- `bootstrap-plan`: separa IAM/SSM do restante, sem refresh/apply, usando a role de plan existente.
- `bootstrap-apply`: exige uma **role temporária já autorizada a criar os recursos IAM do bootstrap**, cria somente os recursos da lista permitida e instala/valida o publisher fixo.
- `deploy`: usa a nova role de publicação para atualizar somente `aws_ssm_document.lab_v2_publication`, executar esse documento na EC2 ITSM fixa e validar os serviços. Não pode editar IAM, inclusive sua própria policy.

Apply sempre usa o plano binário gerado no mesmo job, com refresh=true, lock e gate de endereços/ações. O gate rejeita WAF, Lambda, DR, exclusões, replacements, imports, mudanças adiadas e qualquer endereço fora do escopo. Não usa `-auto-approve` em plano aberto. Execução SSM exige a versão numérica e SHA256 do documento aplicado, sem usar `$LATEST`. Um plan sem refresh não autoriza deploy. Os logs/JSON/binário brutos ficam somente no diretório privado do runner e não são publicados.

As oito alterações anteriores permanecem fora dessa aplicação: três Lambdas, os documentos Argo bootstrap/Orders GitOps/console, Command Center e IP set WAF. A publicação usa um documento SSM separado; não aplica essas atualizações pendentes por associação.

## IAM mínimo e OIDC

`lab_v2_publication.tf` declara:
- `cosmotech-iac-evidence`: OIDC limitado ao repo imutável IaC, owner e main, leitura metadata dos serviços do inventário/refresh, state S3 exato e `GetItem` somente INVENTORY#LAB na tabela EKS. Não lê SecretString, payloads de filas nem itens de pedidos. PutObject fica limitado ao prefixo de evidências e exige SSE AES256 além da criptografia GPG do conteúdo.
- `cosmotech-lab-v2-deploy`: mesma trust OIDC; leitura metadata necessária ao plano, state/lock exatos, criação/atualização do documento SSM fixo e SendCommand somente nesse documento/host. Sem iam:PutRolePolicy, CreateRole, PassRole ou poderes para alterar sua própria trust/policy.
- Policy runtime ITSM: parâmetros SSM exatos, criptografia limitada ao contexto desses parâmetros, S3 somente no prefixo de relatórios, metadata SQS e CloudWatch regional, GetSecretValue somente no segredo Grafana já existente. Nenhum segredo novo pago é criado.
- Policy manutenção: GetQueueAttributes somente nas duas filas EKS. A leitura Kubernetes é corrigida separadamente pelo Role/RoleBinding canônico de observability; não se resolve RBAC com AdministratorAccess.

Policy de evidências completa em `ops/iac-portal/evidence-policy.json.tftpl`; operações/ações verificadas nas referências oficiais AWS constam em `iam-reference-operations.json`. Variantes de replicação DynamoDB/OAM foram excluídas por estarem fora do escopo explicitamente autorizado. Serviços de List/Describe sem suporte a ARN usam `*` com condição regional; state, filas, parâmetros e identidade são limitados. Não se anexou ReadOnlyAccess/SecurityAudit genérico.

## Configuração

Variáveis GitHub:
- `TERRAFORM_PLAN_ROLE_ARN`: existente, usada só nos planos sem refresh.
- `LAB_V2_BOOTSTRAP_ROLE_ARN`: role de bootstrap **já existente**, autorizada externamente para IAM/SSM/state neste escopo; não usar a role de plan/manutenção nem credencial administrativa permanente como fallback.
- `LAB_V2_DEPLOY_ROLE_ARN`: output `lab_v2_deploy_role_arn` após bootstrap.
- `LAB_V2_ITSM_INSTANCE_ID`: instância ITSM existente; o IAM ainda restringe o destino ao ARN declarado no Terraform.
- `IAC_EVIDENCE_ROLE_ARN`: output `iac_evidence_role_arn` após bootstrap.
- `IAC_EVIDENCE_GPG_PUBLIC_KEY`: público, opcional. Quando ausente, a coleta lê `/cosmotech/lab-v2/evidence-public-key` via sua role. Esse parâmetro só é produzido após instalação no host.

A chave GPG privada é gerada/preservada somente no host privado, root/0700, no EBS criptografado existente; nunca é transportada para Git, runner, outputs ou state. Senha aleatória de `iac-reader` e token Grafana são gerados no runtime e salvos em SecureString Standard `/cosmotech/lab-v2/portal-auth` e `/cosmotech/lab-v2/grafana-publisher`. Não há valor de senha/token em recurso Terraform. Recuperação/rotação desses valores requer identidade autorizada; não imprimir valores em evidências.

O Grafana publisher é Viewer com edição explícita apenas na pasta CosmoTech Lab V2. Usa a credencial administrativa do vault existente somente para criar/reconciliar essa conta/pasta/token, sem alterar a conta administrativa. Tokens duram sete dias e são renovados quando restar menos de um dia; não exclui tokens/contas/dashboards. Não sobrescreve um dashboard com o mesmo UID pertencente a outra pasta. O conteúdo publicado é o snapshot versionado do proprietário Observabilidade, entregue pelo SSM gerenciado por Terraform.

## Validação e dados desconhecidos

Portal: serviço sem privilégios www-data em loopback, Basic obrigatório, HTTPS no proxy existente, rate limit, sem API de escrita. Testes HTTP reais locais já verificam autenticação, traversal e histórico. Não faz bypass de TLS.

Evidências GPG vão para prefixo privado no bucket existente; timer root lê os dois meses recentes e expõe somente os 56 relatórios sanitizados mais recentes ao serviço. Nenhum raw state/plan é servido e nenhum objeto é apagado por esse timer. O diretório GPG não é legível por www-data. Arquivos públicos de código/assets não carregam valores de credenciais.

O modo validate exige 401 sem autenticação, 200 autenticado no portal, API Grafana autenticada e séries reais finitas/frescas por datasource. Aceita zero **medido**, rejeita ausência/staleness/NaN. DLQ sem amostra continua desconhecida se GetQueueAttributes não confirmar contagem; não é convertida em zero. Histograma sem amostras não comprova latência de processamento.

Role/RoleBinding de manutenção: https://github.com/rlcosmo/cosmotech-kubernetes-rbac/pull/2 (merge 178d6d0). Instalação exige executor RBAC com poderes no namespace; a manutenção read-only não se concede permissões. Secrets, escrita e cluster-admin continuam proibidos.

## Custos e backlog

Nenhum EKS/EC2/ALB/DNS/WAF/Lambda/replicação/segredo pago novo nesse escopo. Após ativação haverá chamadas de API, S3 de evidências, KMS/SSM e uso de Actions sujeitos a tarifas/quotas existentes; não equivalem a custo garantido zero. Sem mudança de retenção de dados de pedidos ou filas. Relatórios S3 são preservados; retenção/arquivamento definitivo requer política própria revisada.

V2-003 segue desabilitado e sem provisionamento. V2-004, V2-005 e V2-006 permanecem no backlog, sem execução nesta mudança.

## Limite técnico de bootstrap

Na última verificação, apenas TERRAFORM_PLAN_ROLE_ARN estava configurada. A role de manutenção existente assume corretamente STS, porém não tem IAM write nem permissão para instalar o RoleBinding em observability. A role de plan aceita main e não tem apply/IAM write. O pipeline recusa bootstrap-apply sem LAB_V2_BOOTSTRAP_ROLE_ARN; autorização conversacional não cria essas permissões técnicas. Código, plano separado e CI tornam a mudança revisável, mas não autorizam contornar um AccessDenied ou usar credencial permanente para elevar privilégios.

## Resultado verificado desta execução

Esta seção substitui o retrato de configuração inicial acima. Código IaC #4 integrado em ed9a825; RBAC #2/#3/#4 integrados em 178d6d0, 29a25f4 e 55b27f7. CI IaC passou com 27 testes Python, fmt/validate dev/DR e testes Terraform mock; CI RBAC passou com validação de escopo e contexto de workflow. O erro real HTTP 422 do runner.temp em jobs.env foi corrigido e o dispatch foi aceito.

Configuradas IAC_EVIDENCE_ROLE_ARN e LAB_V2_DEPLOY_ROLE_ARN para as identidades declaradas, sem afirmar que já foram provisionadas. LAB_V2_ITSM_INSTANCE_ID foi confirmado por EC2 DescribeInstances usando STS de 900s antes de configurar. Nenhuma credencial foi persistida/exposta. O GPG público pode vir do parâmetro runtime; chave privada não foi gerada/exportada neste executor.

`bootstrap-plan` executou com sucesso na role existente de plan, passou pelo gate de escopo e manteve as oito mudanças anteriores fora da publicação. Não é plano com refresh para apply. `bootstrap-apply` foi acionado e recusado no preflight por ausência de LAB_V2_BOOTSTRAP_ROLE_ARN, antes de qualquer mutação AWS. A coleta de evidências já encontra a variável configurada, mas AWS negou sts:AssumeRoleWithWebIdentity para a identidade que ainda não foi provisionada nesta execução.

A entrega RBAC também foi acionada e bloqueada no preflight por ausência de LAB_V2_RBAC_BOOTSTRAP_ROLE_ARN, antes de qualquer mutação Kubernetes. Não se reutilizou a manutenção read-only para conceder sua própria autorização; nenhuma política obrigatória foi contornada. Os pipelines agora estão registrados, mas publicação e auth can-i real seguem dependentes dessas identidades técnicas.

CloudWatch real em 07/11/2026, amostra de 12:16 UTC: origem com visible/in-flight/idade zero **medidos** e DLQ agora com uma amostra explícita de visible=0. Isso difere da observação anterior sem datapoints. Ausência continua desconhecida nos coletores/testes, não é convertida em zero. Não houve envio, recebimento, exclusão, redrive ou alteração das filas.

Portal e dashboard não foram declarados publicados/validados autenticadamente. Nenhum apply ou recurso AWS novo. V2-003 segue sem provisionamento; V2-004/005/006 continuam backlog. URLs, bloqueios e medições sanitizadas em `docs/evidence/lab-v2-publication-2026-11-07.json`.
