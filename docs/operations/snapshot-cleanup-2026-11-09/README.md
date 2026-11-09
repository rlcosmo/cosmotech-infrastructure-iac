# Limpeza de snapshots — 09/11/2026

Execução autorizada pelo proprietário para laboratório. **59 snapshots EBS encontrados; 33 excluídos e ausência confirmada; 26 preservados; nenhuma falha.** Inventário das 17 regiões habilitadas: somente us-east-1 tinha snapshots. us-east-2 e as outras 15 regiões não tinham snapshots próprios, AMIs próprias, vaults ou políticas DLM. Nenhuma restrição AWS foi contornada.

- [Inventário completo: ID, região, origem, volume, tamanho, data, AMI, elegibilidade e custo de referência](inventory.csv).
- [Evidências, pedidos de exclusão e verificação dos recursos preservados](evidence.json).

## Elegibilidade e execução

Havia 58 snapshots gerenciados pelo AWS Backup (19 associados a recovery points EC2/AMIs e 39 recovery points EBS) e um manual. Foram excluídos **32 recovery points exclusivamente EBS** pela API AWS Backup DeleteRecoveryPoint e o manual **snap-0a4278c92060be577**, do Zabbix V2-010, por EC2 DeleteSnapshot. Não se tentou excluir snapshots gerenciados por AWS Backup diretamente pelo EC2.

Preservados:

- **19 snapshots de 19 AMIs registradas**, incluindo as administradas por AWS Backup. Não se excluíram AMIs nem recovery points EC2.
- **Cinco backups EBS mais recentes dos volumes de dados ativos**, de Grafana, Prometheus, Tempo, Loki e Alertmanager.
- **Dois pontos adicionais de Tempo:** captura consistente recente de 09/11 às 04:40 UTC e baseline quiesced validado snap-0da8501e7373d8447, que originou o volume de recuperação preservado. O P0 de observabilidade ainda está aberto.

As capturas anteriores desses volumes e backups de raízes de workers antigos eram redundantes ou obsoletos. Um volume antigo de worker ainda existe e foi preservado; sua existência não torna obrigatório o snapshot de um worker substituível. Nenhuma AMI depende dos 33 snapshots removidos; compartilhamento foi consultado para todos os 59 snapshots.

Antes das exclusões: conta confirmada, AMIs incluindo deprecated/disabled consultadas, snapshots retidos conferidos completed, Snapshot Lock vazio, Recycle Bin sem regra de EBS, vault sem Vault Lock/política de bloqueio, nenhum legal hold e nenhum restore/copy job ativo. A elegibilidade foi registrada antes dos pedidos destrutivos. Somente recovery points ResourceType=EBS e snapshots próprios explicitamente classificados entraram na execução.

## Zabbix e recuperação

O upgrade V2-010 estava certificado, sem processo de upgrade ativo. Os seis arquivos de backup frio, manifest e checkpoint recente estavam presentes; restauração isolada anterior aprovada. Os recovery points EC2/AMIs do Zabbix foram preservados. Não se alterou sua instalação, arquivos de backup locais ou credenciais.

O ponto off-host pré-upgrade de 11:56 UTC foi removido. Uma perda integral do host dependerá dos backups EC2 retidos, cujo ponto mais recente do Zabbix é anterior ao upgrade, de 05:19 UTC; os backups locais não substituem uma cópia independente se o próprio volume for perdido. A janela de recuperação granular foi reduzida, conforme a autorização de limpeza do laboratório. Não houve perda de dados nos volumes ativos.

Os registros históricos V2-010 continuam descrevendo o snapshot na data do ensaio; não comprovam sua existência atual. O helper de upgrade versionado passou a consultar o snapshot real antes de qualquer cutover, rejeitando exclusão, indisponibilidade, permissão negada ou metadados divergentes. Esse guard foi testado contra o ID realmente excluído e com testes de falhas. **A mudança de fonte não foi implantada na instalação do Zabbix nem em documentos SSM nesta execução.** Um upgrade futuro deve partir da fonte atual e produzir nova evidência off-host válida.

## Custos: estimativa e limites

Preço atual obtido pelo AWS Price List API para EBS:SnapshotUsage em us-east-1: **US$0,05/GB-mês**. O CSV apresenta o custo máximo de cada cópia integral; não representa faturamento incremental individual.

| Medida | Antes | Depois / redução |
| --- | ---: | ---: |
| Snapshots | 59 | 26 |
| Soma das capacidades lógicas referenciadas | 1.692 GiB | 1.297 GiB; redução de 395 GiB |
| Soma dos tamanhos integrais FullSnapshotSizeInBytes | 532,82 GiB | 416,50 GiB; redução máxima de 116,31 GiB |
| Economia mensal de armazenamento | — | **até US$5,82/mês**, menor quando há blocos compartilhados |
| Cost Explorer, dia 08/11 | US$0,2179145978/dia | equivalente a US$6,54 por 30 dias; referência anterior, estimada |
| Custo efetivo após exclusão | — | pendente de atualização do faturamento |

395 GiB são capacidades lógicas, não espaço físico liberado. A soma integral também duplica blocos compartilhados. Excluir snapshots preserva os blocos ainda necessários às capturas retidas; por isso não é possível afirmar que 116,31 GiB foram fisicamente liberados ou que US$5,82 serão efetivamente economizados. O dia de referência do Cost Explorer não inclui toda a atividade de 09/11 e não deve ser subtraído diretamente do teto de economia. Conferir faturamento posterior antes de declarar economia realizada. A consulta ao Cost Explorer pode acrescentar a tarifa de API aplicável.

## Políticas que podem recriar custos

- AWS Backup `cosmotech-itsm-dev-daily`, plano 26e6a57c-13e7-485d-94d7-9ccd19c0af58: **05:00 UTC diário, retenção 14 dias**. Seleções incluem ITSM, Argo CD, Zabbix e os volumes de observabilidade. Fonte canônica: terraform/modules/itsm_host/main.tf; seleções adicionais existentes também foram inventariadas.
- Timer ativo no ITSM `cosmotech-tempo-backup.timer`: **04:40 UTC diário**, próximo disparo observado em 10/11; captura consistente Tempo com **14 dias** de retenção. Fonte: cosmotech-observability/ops/itsm/systemd/cosmotech-tempo-backup.timer e automação instalada. Isso duplica a captura diária Tempo em relação à seleção genérica; a consistência exige avaliação antes de remover qualquer ramo.
- DLM: nenhuma política nas 17 regiões. Nenhum recurso aws_ebs_snapshot gerenciado no state Terraform dev; o snapshot manual do Zabbix não possui recriação agendada identificada.
- DR: fonte dr-backup-source prevê captura horária/cópia para Ohio com retenção de sete dias quando habilitada; não há vault, recovery point ou plano provisionado em us-east-2. Não se habilitou nem desativou DR.
- RDS: uma instância MySQL disponível, retenção automática zero; nenhum snapshot RDS, automated backup RDS ou backup nativo DynamoDB nas regiões auditadas. Nenhum backup desses serviços foi excluído. As leituras nativas de ElastiCache, FSx e Redshift também retornaram zero snapshots/backups nas 17 regiões, sem erros.

**Proposta, não aplicada:** depois da recuperação do P0, reter três diários por recurso, preservar uma referência semanal validada e manter a captura consistente de Tempo como referência específica. Rever a seleção genérica de Tempo para evitar duplicação, sem perder a captura consistente. Manter requisitos/cópias DR separados. Durante o P0, conservar os sete pontos EBS selecionados. Reduzir retenção de EC2/AMI exige revisão explícita, pois esta execução não autoriza apagar AMIs. As políticas atuais continuam criando snapshots; parte da economia de limpeza será temporária enquanto a retenção de 14 dias permanecer.

## Validações

Após exclusão: **19 volumes EBS sem alteração de estado/tamanho/criptografia/attachments, 19 AMIs preservadas, estados EC2 e RDS inalterados, 11 PVs e 11 PVCs com UID/spec/phase intactos, EKS ACTIVE, inventários S3/DynamoDB preservados e objeto remoto Terraform State com mesmo ETag/VersionId/tamanho**. Não houve comando de exclusão de volumes, bancos, buckets, tabelas ou recursos de rede; Power Manager não foi alterado.

Testes locais: 121 casos, 119 aprovados e dois skips; nove testes focados do upgrade/notificações. Guard de snapshot excluído validado com leitura real AWS. CI e PR registram alterações de fonte/documentação, sem apply Terraform ou implantação de ferramentas.

Fontes oficiais: [exclusão EBS e blocos compartilhados](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-deleting-snapshot.html), [exclusão via AWS Backup](https://docs.aws.amazon.com/aws-backup/latest/devguide/deleting-backups.html), [preços EBS](https://aws.amazon.com/ebs/pricing/).
