# CosmoTech — Infraestrutura como código (Terraform)

Este é o **único repositório autorizado a gerenciar o backend e o estado Terraform existentes no S3**. O estado não foi copiado nem migrado. Os diretórios `ops/itsm`, fontes de automação e Lambda ainda incluem snapshots necessários às referências `file()` do Terraform. Migre essas referências para artefatos versionados antes de remover os snapshots.

Extraído de [aws-serverless-lab](https://github.com/rlcosmo/aws-serverless-lab) no commit `6f3e67f9e93d626393b23b6884b031767a63fea0`, preservando o histórico. A extração não recriou infraestrutura.

## Como operar com segurança

1. Use somente este repositório para alterações Terraform. **Nunca aplique** o snapshot congelado de `aws-serverless-lab`.
2. Revise código, variáveis, recursos afetados e achados de segurança.
3. Execute um plano atualizado com refresh habilitado no ambiente autorizado e revise-o antes de qualquer aplicação.
4. Não interprete o plano de CI com `-refresh=false` como confirmação do estado real: ele valida mudanças de código e não tem permissão para modificar recursos.
5. As duas atualizações legadas de artefatos Lambda foram excluídas intencionalmente e precisam de revisão separada.

O bucket/chave do backend, criptografia, versionamento e bloqueio de estado S3 permanecem inalterados.

## Argo CD — plano de gerenciamento

O Argo CD usa uma EC2 dedicada (`i-0b33fdd51332e04e5`) em sub-rede privada, sem IP público nem SSH, com disco gp3 criptografado de 40 GiB e IMDSv2 obrigatório.

Para reconstrução, revise e aplique o Terraform correspondente; depois utilize o documento SSM `CosmoTech-Install-Dedicated-ArgoCD` (`$LATEST`). Versões documentadas: K3s `v1.37.1+k3s1`, Helm `v4.3.1` e chart Argo `10.12.1`. Após registrar a chave pública de deploy com acesso somente leitura no GitHub, execute `ops/argocd/connect-gitops.sh` no host. A chave privada é recuperada automaticamente do Secrets Manager; não a copie para Git ou logs.

Secrets Manager: `cosmotech/argocd/admin` e `cosmotech/argocd/github-deploy-key`. Valores de segredos não entram no Terraform. `ops/argocd/values.yaml` controla a instalação do plano de gerenciamento até uma futura migração explícita para autogerenciamento. Manifests de aplicações e propostas de release pertencem ao [GitOps](https://github.com/rlcosmo/cosmotech-gitops); RBAC pertence ao [repositório RBAC](https://github.com/rlcosmo/cosmotech-kubernetes-rbac).

## Backup, restauração e reversão

Há backup SQLite online com verificação de integridade, token/configuração de criptografia K3s e hashes SHA256 diariamente às **04:20 UTC**, acessíveis somente a root. O plano de backup criptografado EC2 também protege o host; a limpeza dos arquivos lógicos ainda é manual.

Para recuperar: restaure um ponto EC2 em host privado isolado, recupere SQLite, configurações, token e arquivos de criptografia **antes de iniciar o K3s**, mantenha restrições de rede, reconecte a chave GitHub de leitura e valide acesso EKS e autenticação pública. **Nunca mantenha controladores Argo originais e restaurados atuando simultaneamente nos mesmos workloads.** A validação de recuperação completa por boot ainda está pendente. Para reverter configurações do plano de gerenciamento, utilize valores Helm anteriores revisados, preservando workloads e dados EKS.

## Custos estimados

Estimativa incremental de referência: EC2 `t3.large` US$ 0,0832/h × 730 ≈ **US$ 60,74/mês**; gp3 40 GiB ≈ US$ 3,20/mês; dois Secrets ≈ US$ 0,80/mês; duas regras WAF do Argo ≈ US$ 2/mês. Duas regras WAF adicionais do Grafana ≈ US$ 2/mês. Monitoramento detalhado, snapshots, tráfego, logs e tributos são adicionais. ALB, NAT e EKS existentes são reutilizados. Não há teto de gastos imposto.

Referência EC2: AWS Pricing API em 04/11/2026. Consulte [EBS](https://aws.amazon.com/ebs/pricing/), [Secrets Manager](https://aws.amazon.com/secrets-manager/pricing/) e [WAF](https://aws.amazon.com/waf/pricing/) para preços atualizados.
