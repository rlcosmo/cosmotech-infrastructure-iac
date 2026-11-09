# Recuperação operacional — 09/11/2026

## Capacidade e armazenamento

Grafana: PVC `observability/grafana-data`, PV `pvc-bd4cc904-d502-43e2-a264-8c782c162e25`, EBS `vol-0d46fc5d30780ca02`, 5 GiB criptografados, AZ us-east-1a, volume disponível sem attachment. Node group `aws-eks-istio-lab-dev-spot-named` tem somente dois nós Ready em us-east-1b. A criação do terceiro nó falha por limite de Fleet Requests; inventários Fleet/Spot Fleet retornaram vazios. Cotas EC2 Standard Spot e On-Demand em us-east-1: zero. Cota Standard On-Demand em us-east-2: zero. Não há mudança de configuração justificada que restabeleça capacidade dentro desses limites.

Nenhum nó/host alternativo foi criado, PVC/PV/EBS excluído, dado migrado ou configuração de Power Manager/rede alterada. Grafana/Tempo precisam de capacidade na AZ dos volumes; Prometheus também está Pending por memória. HTTP 503 do Grafana permanece sem aceite de autenticação/dashboards/datasources.

## Portal: autorização efetiva

Principal de execução: role `cosmotech-iac-observer`, assumida pelo instance profile ITSM. Sem permissions boundary; política inline `git-aws-read-only-comparison`. `SimulatePrincipalPolicy`, com RequestedRegion=us-east-1, permitiu GetFunction nos três recursos do state dev:

- `cosmotech-external-http-probe`;
- `aws-serverless-lab-dev-orders-producer`;
- `aws-serverless-lab-dev-orders-consumer`.

A sessão `serverless-lab` continua válida, com identidade root. Organizations respondeu AWSOrganizationsNotInUseException: não há SCP de organização aplicável nessa verificação. GetFunction/ListFunctions já falhavam nessa sessão; GetAccountSettings também retornou AccessDeniedException sem mensagem explicativa. CloudTrail não retornou eventos GetFunction na consulta. A evidência não identifica uma política IAM editável como causa e não permite atribuir o motivo exato da negação AWS. Nenhum grant foi ampliado; comparação indisponível não é inventário vazio ou ausência de drift.

## Alertas e GitOps

RBAC PR #6, merge `1d8740c`: leitura de ConfigMaps/PrometheusRules no namespace observability e patch/update somente de `cosmotech-v2-sqs-workers`; sem create/delete, workloads, armazenamento, Secrets ou concessão de RBAC. Bootstrap inicial criou apenas os dois objetos após CI e dry-run no API server.

GitOps PR #21, merge `8320ae7`: oito alertas e dashboard idênticos ao commit canônico de observabilidade `4b1512c`, com proveniência/hash. PrometheusRule instalada; o operador incluiu as oito regras em seu rulefiles ConfigMap e o selector `release=prometheus` corresponde ao recurso. Isso não comprova saúde de avaliação enquanto o Prometheus estiver Pending.

O novo projeto observability-v2 aceita exclusivamente ConfigMap/PrometheusRule nesse namespace, sem prune. A entrega de infraestrutura usa a assinatura pública dos merges GitHub, fingerprint completo `968479A1AFF927E37D1A566BB5690EEEBB952194`, após PR/CI. O bootstrap verifica esse fingerprint, adiciona somente a chave pública ao keyring e acrescenta observability ao campo de namespaces do cluster. Não lê token/config da credencial e preserva todo o AppProject orders-dev, inclusive sua autoridade exclusiva de promoção de aplicações. A aplicação só pode atualizar os dois nomes já instalados; recriação após remoção exige bootstrap deliberado.

Instalar no host de gerenciamento com o layout do repositório preservado: `scripts/configure_v2_observability_argocd.py` e `ops/argocd/observability-github-signing-key.asc`; executar o script com o administrador K3s existente. Depois aplicar os dois manifests observability-v2 do GitOps main revisado. Confirmar revisão/Synced/Healthy antes do aceite de reconciliação. A certificação operacional ainda exige Grafana autenticado, datasources e regras saudáveis na API Prometheus, além de métricas frescas.

## DR: plano habilitado versus provisionamento real

Backend remoto S3 separado: `aws-serverless-lab/dr/terraform.tfstate`, região do backend us-east-1; provider standby us-east-2; workspace default. Backend inicializado e acessível, objeto state DR ausente (404). Não há evidência de state perdido: nenhum recurso DR foi aplicado. Defaults continuam enable_standby=false, enable_standby_queues=false, orçamento zero e account_restricted=true. Não existem roles cosmotech-dr-observer/cosmotech-dr-operator.

O teste Terraform `standby_zero_nodes` gera **77 criações**, nenhuma atualização/exclusão, exclusivamente em module.standby e module.standby_sqs. Provider mock: é uma simulação, não plano cloud revisado ou aprovação financeira. Inclui VPC, três subnets, EKS com cinco add-ons, node group On-Demand com desired/min=0 e max=3, duas filas SQS, outbox DynamoDB com PITR, três ECR, S3, KMS, Backup e IAM. Passou no gate de escopo. Sem host opcional e sem consumidores ativos. Não inclui restore concluído, replicação pronta, failover/chaos ou RTO/RPO certificados.

A estimativa versionada de 08/11 aponta US$117,05/mês passivo e US$343,58/mês ativado, sem host opcional e com custos variáveis adicionais. São estimativas da arquitetura completa, não uma aprovação de gastos ou cobrança exata dos 77 recursos. O teto aprovado segue ausente. `provisioning.tfvars.json.example` descreve a configuração habilitada pretendida, mas mantém hold, release vazia, orçamento zero e operador/CIDRs vazios: deve falhar fechado até revisão explícita. Não preencher com evidência fictícia nem aplicar o exemplo.

O workflow cloud-plan agora usa `check_plan.py --require-provisioning`: rejeita plano vazio/no-op, grafo incompleto, leitura parcial, provisioning desabilitado, região/state incorretos e teto ausente/inválido. Depois de satisfeitos liberação, capacidade, custo, operador e permissões, gerar plano real com refresh, salvar em diretório privado e passar nesse gate. Nenhum workflow de apply DR foi habilitado e nenhum apply vazio foi executado como evidência.
