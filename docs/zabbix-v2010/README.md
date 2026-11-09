# V2-010 — Zabbix 8.0 LTS: upgrade preservando os dados

## Estado e evidências

Origem auditada em 09/11/2026: EC2 dedicada, Docker Compose, server/web/proxy e agentes 8.0.0rc3. A API rc3 já retorna `8.0.0`; por isso o aceite exige também binários, digests e estado dos containers. PostgreSQL 17.11, schema mandatory/optional 8000000, aproximadamente 303 MB de banco. Volumes existentes: cosmotech-zabbix-postgres, cosmotech-zabbix-clickhouse e cosmotech-zabbix-proxy, além dos volumes anônimos de export/snmptraps. Nenhum é removido ou recriado pelo upgrade.

Inventário API anterior: 30 hosts visíveis, 390 templates, 11.698 itens, 4.241 triggers, sete dashboards, 40 media types, cinco actions, uma regra de descoberta de rede e seis regras CEP. Contagens SQL incluem templates/objetos descobertos e não são diretamente comparáveis às contagens API. As integrações existentes usam TLS-PSK para agentes, AWS discovery via instance profile/AssumeRole, scripts de monitoramento, dashboards, Command Center e o ramo OTLP das aplicações.

**Não declarar migração concluída sem evidência operacional final.** O snapshot/cópia de arquivos, API info.version e testes locais, isoladamente, não certificam uma migração.

## Aceite operacional em 09/11/2026

**Upgrade certificado ao vivo às 15:12 UTC.** Server, frontend, proxy e três Agent 2 passaram de 8.0.0rc3 para 8.0.0 GA. ClickHouse passou de 25.8.33.6 para 26.8.36.3; PostgreSQL 17.11 e schema 8000000/8000000 foram preservados. O aceite combina binários oficiais, frontend com sessão autenticada, API pública autenticada, dashboard real, amostras recentes dos três sinais para API/Worker, estados suportados dos itens nativos, agentes ativos e eventos/entregas reais. Isso certifica o escopo do Zabbix V2-010, sem certificar Grafana ou DR.

Inventário depois do bootstrap: 31 hosts, 390 templates, 11.710 itens, 4.247 triggers, oito dashboards, 41 media types, seis actions e oito CEP rules. As diferenças correspondem aos objetos do laboratório: um host, 12 itens, seis triggers, um dashboard, um media type, uma action e duas regras CEP. Os 390 templates existentes permanecem. O bootstrap passou duas vezes com os mesmos IDs; a LLD gerou quatro itens e quatro triggers concretos.

Frontend público HTTP 200, login com cookie de sessão e dashboard 443 HTTP 200; API pública autenticada. A página de notificações retorna 401 sem credencial e 200 para o leitor, e POST com token inválido retorna 401. As credenciais permanecem no catálogo SecureString existente, nunca nesta documentação.

O ensaio final `all --duration 12 --live` passou nos quatro cenários: HTTP 503, latência observada de 801 ms, serviço loopback indisponível e parada temporária do container Agent 2 isolado. Todos restauraram baseline automaticamente. Eventos de problema 153, 155, 157 e 159 tiveram recuperação 154, 156, 158 e 160; oito alertas constam enviados, sem erro. A página mostra os quatro incidentes recuperados. CEP adicionou managed_by a todos e relacionou três sintomas à causa 153. O container lab-agent foi removido; os sete serviços permanentes estavam running e os quatro estados voltaram a zero.

Os seis alertas falhos anteriores à habilitação explícita do media type foram mantidos como evidência, não apagados. O primeiro ensaio de agente não reconheceu o resultado string `[s|1]`; o código agora aceita string ou unsigned igual a um. A rejeição inicial do trapper levou à exceção de gateway somente no host isolado. Esses achados estão corrigidos nos PRs #55 e #56.

Os itens nativos tipo 24/JSON e dependentes estão supported, sem erro, com coleta recente. As 16 triggers APM auditadas (taxa de erro, P95, ausência de sinais, consulta e logs de erro) estavam habilitadas e normais. Traces, logs e métricas sum/histogram recentes foram observados para os dois serviços; gauge e exponential histogram vazios refletem ausência desses sinais, não foram preenchidos artificialmente. A retenção efetiva de todas as seis tabelas é 259.200 segundos (três dias). Nenhuma falha foi injetada nas aplicações primárias.

Os agentes dos hosts Zabbix, ITSM e ArgoCD informam versão 8.0.0 e ping ativo no server; backups locais de configuração/binário foram preservados em `/var/backups/cosmotech-v2010-agent`, e checksums confirmaram a configuração intacta. Os documentos SSM de instalação/monitoramento foram atualizados por planos restritos: primeiro três documentos, depois apenas o documento de bootstrap nas duas correções. Zero recursos AWS criados/destruídos nesses applies; o snapshot é a única criação AWS específica desta operação.

Evidências sem segredos: [operational-evidence.json](operational-evidence.json). No host, os artefatos protegidos ficam em `/opt/cosmotech/zabbix/backups/v2010-20261109T114434Z`: manifest/archives, snapshot-verified.json, rehearsal-ok.json, checkpoint recente, applied.json (aceite do core), operational-certification.json e certified.json (aceite final). A cópia de restauração foi desligada sem remover volumes/dados. O rollback real de produção não foi necessário nem exercitado.

PRs de implementação: [#54](https://github.com/rlcosmo/cosmotech-infrastructure-iac/pull/54), [#55](https://github.com/rlcosmo/cosmotech-infrastructure-iac/pull/55), [#56](https://github.com/rlcosmo/cosmotech-infrastructure-iac/pull/56), todos integrados após CI aprovado. Suíte local: 120 testes, dois skips de SDK; oito testes focados de upgrade/notificações, builds dos dois containers e quatro cenários offline no CI. A documentação/evidência final segue PR próprio.

## Artefatos e compatibilidade

Server, frontend, proxy e Agent 2 são pinados em artefatos oficiais 8.0.0. O frontend recebe somente uma camada de configuração Nginx para `/lab-notifications/`; seu binário e entrypoint permanecem oficiais. Python do receptor também é pinado por digest. Não se usa trunk/latest para resolver Zabbix.

O schema oficial rc3 e GA difere apenas na marca inicial de dbversion; o patch GA 8000000 não altera estrutura. A base existente já usa 8000000. Isso foi verificado contra o código oficial; a restauração isolada continua obrigatória antes da operação. Não se aplica SQL manual de migração nem downgrade do banco alterado.

ClickHouse 25.8 está fora do requisito de APM da versão final; o alvo é 26.8.36.3, dentro da faixa documentada e acima do mínimo 26.8.17.4 para telemetria. Os três scripts oficiais de schema OTLP rc3/GA são idênticos. O coletor e as tabelas existentes permanecem; PostgreSQL não muda de versão.

## Backup e rollback

**Atualização de inventário em 09/11/2026:** o proprietário autorizou a limpeza e o snapshot pré-upgrade `snap-0a4278c92060be577` foi excluído, após certificação operacional e verificação das alternativas de recuperação. Os registros abaixo são históricos. [Resultado, backups preservados, impacto no RPO e custos](../operations/snapshot-cleanup-2026-11-09/README.md). Um novo cutover exige snapshot novo confirmado ao vivo; o helper versionado rejeita metadados antigos sem um snapshot existente. A instalação Zabbix não foi alterada pela limpeza.

`ops/zabbix/upgrade_v2010.py backup` faz dump PostgreSQL, cópias de configuração/identidades e backup frio de todos os seis volumes montados. Interrompe somente o Compose Zabbix e retoma os containers originais em finally, sem down/prune/renew-anon-volumes. Os arquivos são root-only, verificados com leitura tar e SHA256. No ambiente observado, ClickHouse ocupa 21,45 GB, embora as tabelas APM ocupem poucos MB; o restante inclui dados internos do ClickHouse. A primeira tentativa atingiu timeout e foi descartada como incompleta; os serviços foram retomados. A cópia corrigida gerou aproximadamente 10,85 GB comprimidos. A manutenção é uma interrupção de coleta: retries OTLP são limitados; não se promete ausência de lacunas durante a janela.

Snapshot EBS criptografado pré-upgrade: snap-0a4278c92060be577, volume original vol-0cf8b3eaf7cc6ea10, 120 GiB. A operação apply exige `snapshot-verified.json` com o resultado real completed/encrypted e `rehearsal-ok.json`; não transformar pending em sucesso. O ensaio extrai para diretórios novos e usa rede Docker internal, sem credenciais AWS, rotas para o primário ou notificações externas; actions existentes são desabilitadas somente na cópia.

Plano de rollback: parar somente os componentes Zabbix, preservar/quarentenar os volumes pós-upgrade, verificar hashes e extrair as seis cópias para diretórios NOVOS. Gerar override Compose com as imagens anteriores do manifest e bind mounts desses diretórios nos mesmos targets; manter portas/projeto/segredos anteriores. Subir PostgreSQL/ClickHouse antigos, validar schema e inventário, depois server/web/proxy/coletor. Nunca iniciar binário antigo sobre o banco alterado, nunca apagar o volume atual e nunca executar down -v. O ensaio testa a restauração das cópias; rollback preserva também os dados pós-cutover para reconciliação, sem prometer RPO zero. O snapshot é uma segunda cópia fora do host, não um restore completo de boot já certificado.

Antes do corte, apply também gera checkpoint recente do PostgreSQL e exporta as seis tabelas APM em Native, verificando contagens com o binário ClickHouse anterior em container sem rede. O backup frio/snapshot permanece disponível. O comando apply reserva 25 GiB para cópias de rollback, preserva o digest PostgreSQL, a configuração OTLP ativa e os nomes dos três volumes. Faz build/validação antes da interrupção e registra applied.json somente após core GA e mounts verificados. Falha exige reconciliação pelo operador; não apaga dados automaticamente.

Para recuperar o ponto do corte, restaurar também o dump PostgreSQL recente em um banco NOVO e criar/importar as seis tabelas pelos DDLs/arquivos Native do checkpoint em um diretório ClickHouse NOVO, com o binário anterior validado. Não importar o export completo sobre tabelas já preenchidas: isso duplicaria histórico. Validar contagens/hashes antes de publicar. O backup frio continua sendo a referência integral; esse replay recente não foi executado contra produção e não estabelece RPO zero para a janela de manutenção.

## ClickHouse e APM

**Manter ClickHouse para APM; não migrar histórico de itens PostgreSQL.** 8.0 suporta ClickHouse como histórico de valores e como datasource APM, mas o banco de configuração/trends continua relacional. Não há benefício demonstrado para mover os aproximadamente 303 MB atuais de PostgreSQL. Nenhum export/import de histórico é executado.

Traces, métricas e logs já fluem de API/Worker via OpenTelemetry/Alloy para ClickHouse, com schema oficial e TTL APM de três dias. O ramo Grafana/Loki/Tempo/Prometheus permanece intacto. Não se altera o pipeline do primário nem se reinstrumentam aplicações que já emitem os três sinais. A ingestão ativa segue pelo OTel Collector oficialmente suportado; o listener OTLP/gRPC do proxy permanece limitado ao host, sem trocar o roteamento de produção sem teste próprio.

Consultas nativas tipo 24 e valores JSON tipo 6 são adicionados para logs de erro por serviço, com itens dependentes e triggers. Monitores existentes de taxa de erro, P95, ausência de spans, falha da consulta e disponibilidade permanecem. Ausência de amostras não certifica latência; erro de consulta não vira zero. A API APM é validada com time_from/time_till e contagens, sem exportar corpos de logs/traces.

## CEP, descoberta e eventos

O bootstrap reconcilia apenas objetos CosmoTech V2-010. Uma regra LLD trapper e protótipos geram os quatro estados do laboratório. CEP filtra a tag cosmotech_scope=v2-010: adiciona managed_by e agrupa por service em janela de 90s, usando primeira ocorrência como causa e subsequentes como sintomas. Isso é correlação declarativa, não inferência causal ou correlação global automática. Não descarta, fecha ou suprime eventos existentes. As seis regras anteriores permanecem. O receptor deduplica event_id/state e não reabre um incidente quando a recuperação chega antes do problema.

`lab.sh all --duration 1` testa offline erros HTTP reais em loopback, latência, conexão indisponível e endpoint de agente indisponível. `--live` só aceita o host dedicado e publica métricas no host trapper cosmotech-v2010-isolated-lab. No cenário agent live, somente o container lab-agent é interrompido e removido; o agente Linux principal nunca é parado pelo laboratório. Duração 1–180s por cenário; cleanup em finally e SIGTERM restaura baseline e recursos próprios. Não injeta falhas em API, Worker, EC2 principal, filas, dados ou Power Manager.

## Webhook, página e bootstrap

`https://zabbix.cosmotechsolutions.com.br/lab-notifications/` usa Basic Auth separado para leitura; o receiver exige Bearer token e aceita apenas event_id, state, service, severity e horário de envio. Nenhum texto de evento, log, ordem, credential ou payload arbitrário é persistido. Banco SQLite separado, projeção por sete dias e até 200 incidentes por leitura; histórico integral continua no Zabbix. Recuperação e abertura são mostradas com severidade/serviço/horário, por DOM textContent.

Credenciais são geradas uma vez, fora do Git; leitura fica no registro SecureString existente `/cosmotech/zabbix/credentials`, seção notifications_reader. O token de ingestão fica no arquivo root-only e em macros secretas somente dos hosts gerenciados; não é exibido na página. Mediatype/action enviam apenas os eventos etiquetados V2-010 para o serviço Docker interno, sem abrir porta AWS. O usuário de notificação possui somente leitura do grupo dedicado e GUI desabilitada.

`v2010/bootstrap.py` preserva IDs em reruns, recusa recursos ambíguos e não implementa delete. Configura grupo, host, LLD/protótipos, itens/triggers nativos, dashboard, macros secretas, mediatype/action e regras CEP. Os grupos anteriores dos dois hosts APM são mantidos. O instalador inicial recusa volumes PostgreSQL existentes: upgrades devem usar o fluxo com backup/ensaio.

O Zabbix 8.0 usa por padrão a macro `{$TRAPPER.ALLOWED_HOSTS}`. O tráfego enviado ao loopback publicado pelo Docker chega ao server como o gateway da bridge. O bootstrap deriva os IPs privados reais do container e grava a exceção somente na macro do host isolado, sem ampliar a macro global nem regras AWS. O media type próprio é explicitamente habilitado; os demais permanecem sob suas configurações existentes.

## Custos e limites

Não cria EC2, EKS, RDS, rede, NAT, ALB, volume ou chave KMS. Reutiliza a EC2 m7i.xlarge e seu EBS 120 GiB. O novo receptor tem limite de 96 MiB/0,25 CPU; o agente de falhas só existe durante testes. O custo adicional recorrente vem do snapshot e de eventuais retenções/cópias, além de CI/tráfego. O snapshot é incremental e cobrado por blocos armazenados; não confundir 120 GiB de volume com tamanho faturado. A tag de revisão em 16/11 não apaga backups automaticamente. Logs internos do ClickHouse e espaço de ensaio devem ser acompanhados; não purgar histórico para acomodar o upgrade.

O snapshot reportou FullSnapshotSizeInBytes 72.202.846.208 (aproximadamente 67,24 GiB); esse campo não é uma medição de faturamento incremental. Usando apenas como hipótese US$0,05/GiB-mês, 67,24 GiB custariam aproximadamente US$3,36/mês, ou US$6/mês para os 120 GiB lógicos completos, além de tráfego e CI. Confirmar cobrança real e retenção: a revisão de 16/11/2026 não é exclusão automática. Não foi criado banco/cluster ClickHouse adicional permanente.

Pendências de operação: acompanhar estabilidade e espaço livre; decidir retenção dos backups/snapshot na revisão; registrar faturamento real. O restore isolado passou, mas não equivale a um teste de boot do snapshot EC2 nem a um rollback de produção. Não há bloqueio restante para o aceite do Zabbix V2-010 observado nesta janela.

## Fontes oficiais

- https://www.zabbix.com/release_notes
- https://www.zabbix.com/documentation/8.0/en/manual/installation/upgrade/containers
- https://www.zabbix.com/documentation/8.0/en/manual/installation/requirements
- https://www.zabbix.com/documentation/8.0/en/manual/appendix/install/clickhouse_setup
- https://www.zabbix.com/documentation/8.0/en/manual/config/items/itemtypes/telemetry_query
- https://www.zabbix.com/documentation/8.0/en/manual/events/complex_event_processing
- https://github.com/zabbix/zabbix/tree/8.0.0
- https://clickhouse.com/docs/guides/oss/update
- https://aws.amazon.com/ebs/pricing/
