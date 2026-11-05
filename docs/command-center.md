# Infraestrutura do Command Center

O catálogo adiciona `cosmotech/grafana/command-center`. Terraform cria apenas a metadata do segredo; valores são gravados pelo provisionador no host Zabbix, fora de Git/state/logs. O perfil Zabbix recebe Get/Put somente nesse segredo; o perfil ITSM recebe Get somente nele.

`CosmoTech-Provision-Command-Center` cria/atualiza a conta de serviço Zabbix de leitura, seu grupo, role e token com validade de 180 dias. Métodos permitidos: problem.get e trigger.get. O endpoint é HTTPS na rota pública já existente. Reexecução preserva a credencial vigente; a menos de 30 dias do vencimento renova o token, preservando o usuário. Uma criação parcial sem segredo publicado recupera a senha antes da publicação.

`CosmoTech-Install-Command-Center` instala a cópia versionada do coletor ITSM, mantendo timer, endpoint privado /lab-metrics e outras coletas. Não cria VM, porta SSH, plugin Grafana ou rota nova. Não altera o método de acesso às EC2. A configuração da conta de serviço é mantida em `cosmotech-observability/ops/zabbix-monitoring/configure_command_center.py`; o coletor é `cosmotech-observability/src/lab_monitoring.py`. Manter as cópias de entrega sincronizadas.

Plano inicial revisado: cinco criações (segredo, duas inline policies e dois documentos SSM), sem alteração ou remoção de servidores. Atualização seguinte revisada alterou apenas o documento de provisionamento para a API da versão instalada. Fmt/validate e testes locais executados. Confirmar invocações SSM Success, account de serviço sem acesso a método fora da lista, coleta Prometheus e dashboard Grafana antes de encerrar manutenção. Valores de credenciais nunca aparecem nas evidências.

Guia operacional e PDF da arquitetura ficam em `cosmotech-observability/docs/command-center-and-ec2-access.md`. Snapshot contém as seis EC2, os dois clusters, ferramentas instaladas e roadmap proposto; não habilita SSH por senha/chave.
