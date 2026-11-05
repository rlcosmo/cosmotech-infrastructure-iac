# Credenciais das ferramentas no AWS Secrets Manager

Região `us-east-1`, conta `414053644145`. Metadados dos novos segredos são mantidos em `tool_secrets.tf`; valores são publicados fora do Terraform e não entram no Git, planos ou state.

| Segredo | Conteúdo |
|---|---|
| `cosmotech/grafana/credentials` | Administrador e `laboratorio` informados pelo proprietário; registro administrativo do Secret Kubernetes em campo separado |
| `cosmotech/glpi/credentials` | Administrador, `laboratorio`, conta `svc-cosmotech-automation`, credenciais OAuth e referência ao registro anterior no SSM |
| `cosmotech/kestra/credentials` | Administrador público, `laboratorio` informado, conta privada `svc-cosmotech-automation@cosmotechsolutions.com.br` |
| `cosmotech/zabbix/credentials` | Administrador, `laboratorio`, registro anterior do administrador, senhas de PostgreSQL e ClickHouse |
| `cosmotech/argocd/laboratorio` | Conta `laboratorio`; referência ao segredo administrativo existente |
| `cosmotech/argocd/admin` | Segredo existente do administrador, preservado |
| `cosmotech/argocd/github-deploy-key` | Chave de deploy existente, preservada |
| `cosmotech/github/app` | Proprietário `rlcosmo`, App ID `5615173` e referências da chave privada nas Actions |
| `cosmotech/itsm/runtime` | Bancos GLPI/MariaDB e Kestra/PostgreSQL, materiais de bootstrap e chave de criptografia do Kestra público |
| `cosmotech/kestra/automation-runtime` | Conta técnica, banco e chave de criptografia do engine privado |

## Origem e validade

`accounts` contém exatamente as credenciais informadas pelo proprietário, com origem `owner_provided`. `runtime_credential_record` preserva os valores encontrados anteriormente no Parameter Store ou Kubernetes. Nos registros de GLPI e Zabbix esses valores diferem dos informados; a divergência é registrada por `matches_owner_provided=false`. Material de bootstrap pode estar desatualizado em relação a uma senha posteriormente alterada na interface. Este procedimento armazena credenciais e não muda senhas, cria contas ou reinicia ferramentas.

O inventário anterior confirmou `laboratorio` no Grafana, GLPI e Argo CD. A conta adicional do Kestra não foi validada: a instalação OSS anterior usava uma credencial nativa única. O valor informado foi guardado com esse status explícito, sem afirmar que a conta foi criada. A conta `svc` do Kestra pertence ao engine privado, não ao login público do proprietário.

As contas `svc` foram importadas dos SecureStrings existentes, com os campos de OAuth do GLPI preservados. Os parâmetros SSM anteriores permanecem disponíveis para os consumidores em operação. A atualização do catálogo não altera o mecanismo de autenticação das esteiras nem suas permissões de aprovação.

A chave privada da GitHub App não foi fornecida nesta solicitação e não foi recuperada das Actions. O segredo registra `private_key_stored_in_aws=false` e a referência `GITOPS_APP_PRIVATE_KEY` nos dois repositórios de aplicação. App ID não é senha ou token; não foi inventado um Client ID, token de instalação ou chave privada. Para colocar essa chave também na AWS, importar o arquivo original em uma sessão autorizada e atualizar o registro; não gerar outra chave ou substituir o segredo em uso apenas para completar o catálogo.

## Operação

Abrir o Secrets Manager na região indicada, selecionar o nome e usar **Retrieve secret value** com a identidade IAM autorizada. Não copiar valores para tickets, comandos com argumentos literais, PRs ou documentação.

`scripts/publish_tool_secrets.py` recebe objetos JSON por stdin e aceita somente nomes de `ops/credential-catalog/catalog.json`. Usa AWS CLI com credenciais de role e encaminha valores por stdin; a saída contém somente nome, versão e estado. Um retry idêntico não cria outra versão. `ops/credential-catalog/export_host_runtime.py` exporta arquivos root já existentes para destinos fixos, sem imprimir conteúdo.

Na rotação de uma senha, confirmar o login na ferramenta e atualizar o registro correspondente no Secrets Manager e o SecureString ainda consumido por scripts existentes. Não sobrescrever o arquivo de runtime ou o parâmetro de bootstrap com uma senha nova sem coordenar a alteração no serviço. Restauração de banco deve preservar as respectivas chaves de criptografia; não confundir senha de bootstrap com senha interativa atual.

O Secrets Manager usa criptografia padrão AWS; nenhum valor é gerenciado por Terraform. Os segredos possuem janela de recuperação de 30 dias e prevenção de destruição no recurso. A criação adiciona cobrança de armazenamento e chamadas do Secrets Manager. O trabalho usa permissões temporárias de leitura/publicação, limitadas aos destinos nomeados, removidas após verificação. Não habilita rotação automática nem amplia o acesso permanente de outras contas.
