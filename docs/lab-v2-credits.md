# Créditos AWS — Bedrock e RDS

Elegibilidade não confirmada; nenhuma chamada de inferência nem instância RDS foi criada.

A documentação oficial exige verificar as atividades disponíveis para a conta e suas datas. Bedrock envolve escolher um modelo e enviar prompt no playground; uma invocação API ou teste de integração não comprova o requisito promocional. RDS envolve criar uma base e pode gerar cobrança de instância, armazenamento, backups e secrets. Créditos não são teto de gasto e não tornam todo serviço gratuito.

Fontes verificadas em 07/11/2026:
- [Atividades e custos](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans-activities.html)
- [ListAccountActivities](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_freetier_ListAccountActivities.html)
- [GetAccountActivity, instruções e expiração](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_freetier_GetAccountActivity.html)
- [Plano e saldo](https://docs.aws.amazon.com/aws-cost-management/latest/APIReference/API_freetier_GetAccountPlanState.html)

Pré-requisitos de execução: sessão temporária baseada em role; leitura freetier:GetAccountPlanState, freetier:ListAccountActivities e freetier:GetAccountActivity; atividade efetivamente disponível/não concluída/não expirada; instruções específicas da conta; estimativa/ciclo de vida e mecanismo aprovado de encerramento. Não iniciar base RDS persistente sem teto e política de retenção/encerramento compatível com a proibição de apagar recursos/dados. A autorização geral não determina valor de teto, engine, duração ou elegibilidade.

Diagnóstico real: o perfil local temporário `serverless-lab` não autenticou (sessão ausente/expirada). A credencial estática disponível não foi usada para operações do laboratório. O papel OIDC de plan existente não tem ações Free Tier/Bedrock/RDS e aceita apenas a branch main. Não foram ampliadas permissões nem usado outro principal para contornar esse controle.

Próximo passo elegível quando o acesso existir: consultar os três endpoints somente leitura e registrar apenas ID, status, expiração e instruções oficiais; não inferir elegibilidade pelo título genérico da promoção. Se houver crédito, comprová-lo no estado oficial/Billing após processamento; não declarar que foi recebido apenas porque uma API de serviço respondeu com sucesso.
