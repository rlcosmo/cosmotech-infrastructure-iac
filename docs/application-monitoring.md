# Infraestrutura de monitoramento de aplicações

`application_monitoring.tf` adota a inline policy existente do host ITSM, mantendo as permissões de billing/inventário e acrescentando execução do documento fixo de sondagem apenas nas três EC2 da plataforma. `ssm:GetCommandInvocation` usa Resource `*`, pois a API não oferece escopo por recurso. Nenhuma permissão de shell arbitrário é concedida ao coletor.

O novo papel de publicação de cotas confia no principal administrativo já existente somente para assumir sessões STS curtas e gravar um parâmetro sanitizado. Não tem acesso a dados das aplicações. O parâmetro é atualizado fora do Terraform para não armazenar valores de sessão no state. A leitura usa o instance profile ITSM.

As mudanças foram revisadas por plano salvo, fmt e validate. O primeiro plano importou a policy existente, criou o documento e a role de publicação e atualizou os instaladores. O plano final altera somente três documentos SSM; nenhuma destruição ou substituição de EC2. A EC2 Zabbix criada no trabalho usa subnet privada, IMDSv2 obrigatório, root criptografado retido, ALB existente e ingestão limitada ao SG EKS. Os custos e permissões já existentes de outras aplicações não foram alterados.

Os snapshots `ops/zabbix`, `ops/pipelines` e `ops/monitoring` são distribuídos por documentos SSM versionados. Suas origens oficiais são os repositórios observability e automation. O collector APM inclui o contorno do defeito upstream ZBX-28212; retenção de telemetria três dias e backup lógico PostgreSQL sete dias. Consulte `cosmotech-observability/docs/application-services-and-codex.md` para recuperação, limites e evidências.

O acesso legado `zabix.cosmotechsolutions.com.br` foi incluído somente nas duas regras específicas do Zabbix, junto com o hostname correto. A expressão é ancorada ao domínio completo e mantém a limitação por IP. O Ingress correspondente redireciona esse alias para o nome canônico; as regras de outras ferramentas e o bloqueio padrão permanecem iguais. Plano revisado: uma atualização de WAF, sem criação ou destruição de infraestrutura. Credenciais de acesso não são armazenadas neste repositório.
