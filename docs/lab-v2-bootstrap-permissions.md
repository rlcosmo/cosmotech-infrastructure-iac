# Lab V2 — diagnóstico de identidade e permissões de bootstrap

Verificação somente leitura de 07/11/2026; sem apply, mutação IAM/EKS ou reexecução de pipelines já bloqueados. Os commits, gate de escopo e validações anteriores permanecem preservados. V2-003/004/005/006 não foram alterados.

## Causa verificada

O inventário IAM ao vivo não contém `cosmotech-iac-evidence` nem `cosmotech-lab-v2-deploy`. As variáveis correspondentes já configuradas apontam para recursos declarados em Terraform, ainda não provisionados nesta execução. A role de evidências ausente explica por que configurar seu ARN não remove o AccessDenied OIDC; não foi identificada divergência de formato de subject no GitHub que justificasse alargar trust de outra role.

GitHub API confirmou ausência (404) de `LAB_V2_BOOTSTRAP_ROLE_ARN` no IaC e `LAB_V2_RBAC_BOOTSTRAP_ROLE_ARN` no RBAC. Os dois repositórios usam subjects imutáveis; as definições aprovadas exigem repo/owner corretos, main e aud `sts.amazonaws.com`. Uma role de outro repositório, de EC2, Backup ou Pod Identity não substitui essas identidades.

A sessão STS existente de manutenção foi assumida por 900s, sem gravar credenciais. Uma única leitura `iam:GetRole` da role de plan recebeu **AccessDenied**. Não foram repetidas requisições IAM negadas. Portanto, os documentos de trust e policies AWS atuais **não foram lidos nem declarados verificados**; a comparação de trusts é com a fonte Terraform canônica. O inventário conectado confirma nomes e nomes de policies, não permite concluir o conteúdo de toda inline policy.

EKS DescribeCluster respondeu ACTIVE, autenticação API_AND_CONFIG_MAP. SelfSubjectAccessReviews reais no namespace observability retornaram **no** para create Role, create RoleBinding e get Secrets. Nenhum objeto foi criado/alterado; a negação de Secrets foi preservada.

## Reutilização avaliada

| Identidade existente | Evidência | Limite para esta tarefa |
| --- | --- | --- |
| cosmotech-lab-maintenance | Sessão STS válida; autorizações verificadas | Não lê GetRole nem cria Role/RoleBinding |
| cosmotech-terraform-github-plan | Nome/policy listados ao vivo; trust/policy comparados com Terraform | Fonte permite plan/leitura/lock, sem IAM write e sem PutObject no state |
| cosmotech-gitops-github-sync | Nome/policies listados; fonte exige repo GitOps/main | Reconciliação SSM fixa; não é a identidade IaC/RBAC |
| cosmotech-orders-worker-github-ecr | Nome/policies listados; fonte exige repo Worker/main | Publicação de imagem/promoção, não IAM bootstrap |
| cosmotech-itsm-dev-instance-role | Nome/policies listados; fonte confia em EC2 | Não confia no GitHub; sem IAM write declarado |

Nenhuma role com autoridade de bootstrap **verificada e utilizável** foi identificada. Não se reutilizou uma identidade de aplicação nem se mudou sua trust para fazer o job passar.

## Ações necessárias — sem AdministratorAccess

Estas são necessidades do bootstrap aprovado, **não uma lista de chamadas de escrita tentadas/negadas**. O único IAM AccessDenied novo observado foi `iam:GetRole`.

| Operação | Ações/permissões necessárias | Limite de recurso |
| --- | --- | --- |
| Verificar trusts e inline policies reais | iam:GetRole, iam:GetRolePolicy, iam:ListRolePolicies, iam:ListAttachedRolePolicies | Roles candidatas do laboratório; não exige segredo ou access key |
| Criar as duas roles aprovadas | iam:CreateRole, iam:TagRole e leitura iam:GetRole | Somente cosmotech-iac-evidence e cosmotech-lab-v2-deploy |
| Gravar as quatro inline policies | iam:PutRolePolicy e leitura iam:GetRolePolicy | Duas novas roles, cosmotech-itsm-dev-instance-role e cosmotech-lab-maintenance; nomes/JSON já versionados em lab_v2_publication.tf |
| Ler dependências do plano atualizado | iam:GetInstanceProfile; ec2:DescribeInstances/DescribeVpcs/DescribeSubnets/DescribeSecurityGroups/DescribeSecurityGroupRules/DescribeVolumes/DescribeTags/DescribeVpcAttribute; eks:DescribeCluster; secretsmanager:DescribeSecret; ssm:GetParameter | Apenas dependências existentes do plano isolado; parâmetro AMI público e metadata do segredo Grafana; não SecretString |
| Persistir state e lock | s3:GetObject/PutObject no state; s3:GetObject/PutObject/DeleteObject no lock transitório; s3:ListBucket/GetBucketLocation | Bucket existente, chave aws-serverless-lab/dev/terraform.tfstate e respectivo .tflock; sem DeleteObject no state ou dados de negócio |
| Publicar documento fixo | ssm:CreateDocument/AddTagsToResource/GetDocument/DescribeDocument/ListTagsForResource | CosmoTech-Publish-Lab-V2; UpdateDocument/UpdateDocumentDefaultVersion só para atualização desse mesmo documento |
| Executar publicação e validar | ssm:SendCommand, ssm:GetCommandInvocation | Documento fixo com versão/SHA256 aplicados e instância ITSM existente; sem shell/documento arbitrário |
| Entregar RBAC revisado | eks:DescribeCluster e autenticação EKS válida; get/create/patch/update de roles e rolebindings | Somente namespace observability e os dois manifests canônicos |
| Passar o controle anti-escalada Kubernetes | Possuir as permissões de leitura que a Role concede, e autoridade para vinculá-las ao grupo de manutenção | ConfigMap/PodMonitor/ServiceMonitor/PrometheusRule read-only em observability; não requer cluster-admin, Secrets ou bind/escalate irrestritos |

A identidade de bootstrap precisa ter trust OIDC apropriada ao repositório/branch que a utilizará. As ações para alterar trust existente (`iam:UpdateAssumeRolePolicy`) não são necessárias se forem criadas exatamente as duas novas roles já aprovadas; não foram solicitadas mudanças de trust em aplicações existentes. O deploy posterior não recebe IAM write nem pode alterar sua própria policy.

Sem uma identidade temporária com essas permissões efetivas e as autorizações EKS correspondentes, não há caminho de apply autorizado a executar. Alterar apenas variáveis GitHub, repetir STS ou apontar para uma role read-only não corrige essa ausência. Não se usou chave administrativa permanente como executor alternativo.

## Resultado

Nenhum recurso publicado/alterado nesta verificação; portal, inventário automatizado e validação autenticada Grafana permanecem pendentes. Nenhuma alteração WAF/Lambda. Sem novos recursos pagos; uso de API/CI sujeito às quotas/tarifas existentes. Evidências sanitizadas em `docs/evidence/lab-v2-bootstrap-permissions-2026-11-07.json`. Nenhum token, senha, private key, conta AWS ou endereço privado foi incluído nessa evidência.

## Procedimento administrativo único (versionado)

Implementação: `scripts/bootstrap_lab_v2_admin.py`; stack independente em
`terraform/bootstrap/lab_v2_admin`, no bucket existente, chave
`lab-v2/admin-bootstrap/terraform.tfstate`. Não inicializa nem aplica o root dev
completo. O gate admite cinco recursos IAM/EKS, sem delete, import ou replacement.
As duas roles OIDC exigem audience STS e subjects imutáveis repo/main; trust e
permissões de escrita expiram em duas horas (limite de quatro horas no gate).
Nenhuma policy AdministratorAccess, bind/escalate ou permissão Secrets é concedida.

Pré-requisito humano quando não houver sessão administrativa temporária: autenticar
no console AWS com uma identidade autorizada para a matriz acima, criação/leitura
das duas roles de bootstrap e suas inline policies, criação/leitura do EKS access
entry e leitura/escrita do state/lock separado. Essa identidade também deve possuir
RBAC para reconciliar os quatro objetos revisados de observability, incluindo os
próprios privilégios concedidos (controle anti-escalada). Não tentar novamente com
a role de manutenção já negada. Não criar access keys para esse procedimento.

Usar AWS CLI com `aws login`, uma sessão temporária de role, ou perfil temporário
existente. O procedimento rejeita AKIA, token ausente e sessão expirada. Credenciais
são encaminhadas apenas pelo pipe privado de credential_process ao SDK, nunca
no plano, state, arquivo de configuração ou saída humana. O kubeconfig contém
apenas metadata e comando exec para token efêmero.

```bash
aws login --profile serverless-lab
# Ambos os checkouts devem estar limpos no main revisado atual.
python3 scripts/bootstrap_lab_v2_admin.py \
  --profile serverless-lab \
  --rbac-checkout /tmp/cosmotech-v2-rbac
# Omitir --apply acima valida sessão, RBAC e plano sem aplicar.
python3 scripts/bootstrap_lab_v2_admin.py \
  --profile serverless-lab \
  --rbac-checkout /tmp/cosmotech-v2-rbac --apply
```

O apply utiliza exatamente o plano salvo aprovado pelo gate; reconcilia os manifests
RBAC do repositório canônico e configura variables a partir dos outputs reais.
Em seguida executa RBAC, publicação bootstrap-apply e evidências pelos workflows
oficiais main, correlacionados por request_id. Uma negação interrompe sem repetição.
A chave pública GPG é copiada do parâmetro SSM existente/criado pelo publisher;
nenhuma chave privada ou credencial de terceiro é substituída pelo bootstrap.
A saída de sucesso dos jobs ainda exige inspeção dos resultados de validação e
evidências reais para declarar a publicação concluída.

Reexecuções preservam o state separado e reutilizam o prazo enquanto houver ao
menos uma hora restante; renovação requer a mesma sessão administrativa autorizada.
Nenhum recurso computacional novo ou custo fixo relevante é previsto neste stack.
As chamadas API, S3/SSM e Actions usam cobrança/quotas existentes.
