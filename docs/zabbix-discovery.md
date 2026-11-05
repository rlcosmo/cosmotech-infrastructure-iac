# Descoberta automática e sinais APM

A implementação dos coletores, compatibilidade dos templates e dashboards está no repositório `cosmotech-observability`, em `ops/zabbix-monitoring/configure_discovery.py`. Este repositório guarda a cópia entregue pelo documento SSM.

`zabbix_discovery.tf` cria uma IAM Role assumível exclusivamente pelo perfil da EC2 Zabbix, com leitura de inventário e métricas em us-east-1, sem acesso a payloads, objetos S3 ou segredos. `DescribeBackupVault` fica limitado aos vaults desta conta/região. O perfil existente recebe somente `sts:AssumeRole` para essa role. O IMDSv2 permanece obrigatório; apenas o hop limit da instância Zabbix passa para 2, necessário ao servidor em Docker bridge. A revisão do plano confirmou atualização em metadata_options sem substituir a instância ou seus discos.

O documento `CosmoTech-Configure-Zabbix-Discovery` e sua associação State Manager de 30 minutos persistem configuração, correções da pré-release e dashboards. A descoberta nativa roda independentemente: EC2 a cada cinco minutos e demais famílias suportadas a cada quinze minutos. O template aplica itens/triggers automaticamente aos hosts descobertos. Famílias sem recursos não criam hosts. As identidades dos três agentes de sistema operacional permanecem no Secrets Manager.

O instalador APM também passa a entregar os pipelines logs e metrics no collector privado. A retenção ClickHouse continua em três dias; as métricas e logs são consultáveis pelas APIs APM nativas do Zabbix. As aplicações exportam contadores e histogramas de operação pelo CI/CD de seus repositórios individuais. Logs existentes são encaminhados também pelo Alloy, preservando Loki.

## Validação e operação

Revisar fmt/validate, plano e IAM antes da aplicação. Para esta manutenção, o plano revisado limitou as mudanças aos novos recursos de descoberta, documento APM e metadata_options. Atualizações seguintes de script alteram apenas o documento. Não aplicar o Terraform congelado no repositório agregador.

Confirmar associação SSM Success, ausência de erros nas regras de descoberta, seis EC2 distintas com templates e contadores CloudWatch atuais; conferir também ALB/NLB, Lambda, S3 e Backup conforme inventário real. APIs `apmmetric.get` e `apmlog.get` precisam retornar dados de ambas as aplicações, não apenas contagem direta em banco. Validar serviço dos agentes, docker/k3s, discos e problemas após atualização. Campos opcionais de Vault Lock/air gap não devem aparecer como itens não suportados em vaults comuns.

Custos adicionais: consultas CloudWatch/AWS e armazenamento de métricas/logs, sem nova EC2. Para rollback, reverter o commit e aplicar plano revisado; manter os volumes e backups existentes. Não remover hosts ou histórico para corrigir uma coleta. Mudanças de regiões devem atualizar IAM e filtros juntos. Reavaliar os patches limitados dos templates ao atualizar do Zabbix rc3.
