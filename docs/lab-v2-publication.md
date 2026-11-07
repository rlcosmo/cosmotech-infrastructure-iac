# Portal IaC — Git desejado × AWS real

A fonte de verdade é `rlcosmo/cosmotech-infrastructure-iac`, branch `main`, roots ativos
`terraform/environments/dev`, `terraform/bootstrap/lab_v2_admin` e
`terraform/bootstrap/state_backend`. O portal não exige upload de arquivos, relatório CI
ou execução manual de GitHub Actions para atualizar seu conteúdo.

O observador no host ITSM existente roda automaticamente a cada cinco minutos após
terminar a consulta anterior. O botão **Comparar agora** solicita uma nova coleta.
A interface consulta apenas a API autenticada do portal e mostra a revisão Git,
horário, recursos e cobertura da comparação. Não executa comandos fornecidos pelo
navegador. O histórico antigo é preservado, mas não define mais o estado atual.

## Comparação

1. Obter `main` diretamente do repositório canônico e registrar o SHA imutável.
2. Avaliar módulos, variáveis, count e for_each com Terraform. Executar apenas
   `init`, `plan -refresh=true -lock=false` e `show -json`, nunca apply ou destroy.
3. Usar o state existente para correlacionar endereços Terraform com IDs/ARNs AWS.
   Estar no state não basta: um recurso removido do Git continua sendo extra.
4. Consultar metadata das regiões habilitadas e recursos globais. Descobrir recursos
   sem tags por APIs nativas; complementar com AWS Config e Resource Explorer
   quando disponíveis. Esses serviços não são provisionados/ativados pelo portal.
5. Classificar **conforme**, **extra na AWS**, **ausente na AWS** e **configuração
   diferente**. Expor somente nomes de campos divergentes, sem valores sensíveis.

Exemplo: node group `teste` está no Git e na AWS; outro node group `lab` aparece só
na AWS. `lab` é exibido como extra e conta no drift. Instâncias EC2 e ASGs geradas
por um node group declarado são relacionadas ao pai e não viram falsos extras.
Volumes raiz conhecidos do recurso EC2 também são correlacionados.

O Git pode estar à frente da implantação. Nesse caso a diferença Git × AWS deve
aparecer mesmo que não haja `resource_drift` no formato antigo refresh-only.
O portal observa essas diferenças e não importa, exclui nem corrige recursos AWS.

## Cobertura e limites

O Terraform avalia todos os recursos gerenciados do root canônico. A descoberta
fora do código inclui EC2, EBS, snapshots próprios, VPC/subnet/security group/NAT,
EKS/node groups, ASG, S3, EFS, FSx, RDS, ELB, ECR, Lambda, DynamoDB, SQS e metadata
IAM. Config amplia para seus tipos gravados; Resource Explorer amplia para seus
tipos indexados. APIs paginadas são consultadas pelo SDK com clientes reutilizados e timeouts limitados.
O SDK fica em ambiente virtual isolado do sistema, com versões fixadas.

Não existe uma única API AWS que garanta descoberta instantânea de todos os tipos.
Config depende do recorder; Resource Explorer pode ter cobertura parcial e atraso
de indexação. Falhas, tipos não cobertos e permissões negadas permanecem explícitos.
**Nenhum resultado declara inventário global completo ou ausência de recursos com
base em uma consulta negada.** Serviços que não têm adaptador nem aparecem nas fontes
gerais não são declarados cobertos. Relações ambíguas não justificam importação automática.

A identidade é a conta do profile do host, não outras contas da organização.
A tela mostra os roots e a revisão usados. O scaffold DR desabilitado não é tratado
como infraestrutura a provisionar. No bootstrap do bucket, sem state remoto, uma
presença por nome determinístico é exibida como declarada no Git, mas com configuração
não verificada; não é confundida com conformidade nem com recurso extra. Nenhuma infraestrutura DR é provisionada.

## Identidade e operação

`cosmotech-iac-observer` é uma role de leitura assumida somente pelo instance profile
ITSM existente, por sessões de uma hora. O daemon usa um usuário local sem login,
sem privilégios root. Não recebe escrita AWS, IAM write, GetSecretValue, acesso a
mensagens SQS, decrypt ou acesso geral a objetos S3. A leitura do state é limitada
às chaves canônicas dev e admin-bootstrap existentes. GetItem continua limitado ao registro INVENTORY#LAB.

Código/configuração do observador são públicos no Git. Credenciais STS ficam em
memória. Checkout e plano/state brutos ficam em diretório privado e são removidos
após cada tentativa. A API serve somente a projeção sanitizada e requer Basic sobre
HTTPS, usando a autenticação do portal já existente. O processo web não pode ler o
workspace privado nem alterar o resultado produzido pelo observador.

O serviço é `cosmotech-iac-observer.service`; saída contém apenas estágio, SHA,
contagens e identificadores de erro permitidos. Falha torna a comparação desconhecida.
Uma observação com mais de 15 minutos é marcada desatualizada.

A entrega inicial de código/IAM continua pelo pipeline oficial protegido
`lab-v2-publication.yml`: `bootstrap-apply` cria a role e atualiza o grant exato de
AssumeRole no ITSM, passando pelo gate; `deploy` só entrega o publisher fixo.
Esse pipeline instala o software; a coleta operacional posterior não depende dele.
Nenhuma mudança de acesso EKS, WAF/Lambda ou recurso de computação é incluída.
As chamadas de API e downloads de metadata usam quotas/custos existentes.
