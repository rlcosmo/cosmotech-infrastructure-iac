# Operador permanente do laboratório

Role cosmotech-lab-operator, assumido exclusivamente pelo usuário de manutenção serverless-lab-admin. Não cria usuário/chave, não confia em root e não usa AdministratorAccess. Sessões STS de até uma hora; perfil local cosmotech-lab-operator usa o perfil de origem existente serverless-lab-static. O perfil default não foi trocado.

As permissões AWS são incrementais: restauração dos snapshots explicitamente listados, volumes gp3 até 120 GiB nas AZs 1a/1b com tag desta operação, start/attach somente nos três hosts conhecidos, SSM somente nesses hosts, KMS da chave existente, leitura de diagnóstico e backup nos recursos do laboratório. PassRole serve exclusivamente ao role de backup existente. A autorização temporária DeleteRolePolicy foi removida após revogar o role do incidente. Não há permissões IAM genéricas, alterações de cobrança, segurança da conta ou recursos Ohio.

O acesso EKS usa uma Access Entry e AmazonEKSClusterAdminPolicy limitados ao cluster aws-eks-istio-lab-dev. Administração de PVs e recuperação demandaram escopo de cluster; isso inclui acesso a Secrets do próprio laboratório e deve ser revisado junto com este role. Não é AdministratorAccess AWS. O usuário de origem existente continua sendo a credencial de bootstrap; migrar sua origem para federação humana/OIDC em uma mudança específica, sem criar chaves permanentes.

## State e aplicação

O backend S3 canônico respondeu NotSignedUp mesmo com identidade autorizada. Esta raiz nova usa backend local privado durante a recuperação. State e plano nunca são versionados. O state existe no workspace operacional original e uma cópia mode-0600 foi mantida no diretório privado de evidências. NÃO aplicar esta raiz com state vazio: importar e reconciliar role, policy, Access Entry e associação existentes se o state local não estiver disponível.

Validar fmt, validate, IAM Access Analyzer e plano antes do apply. O bootstrap foi executado pelo usuário autorizado, sem root nesta fase. Operações posteriores usam o role. Quando S3 estiver normalizado, migrar este state ao prefixo isolado lab-v2/operator-bootstrap/terraform.tfstate no bucket canônico, após revisar permissões e migração. Não criar backend alternativo para contornar a restrição.

As configurações locais de perfil contêm somente role_arn, source_profile, região e duração; nenhuma nova credencial está no repositório. Sem criação automática de DR, autoscaling ou acesso público.
