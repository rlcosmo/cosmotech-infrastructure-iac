# Lab V2 — execução e limites

## V2-001 (parcial)

Portal local em `ops/iac-portal/index.html`, sem dependências, API de escrita ou credenciais. Abrir no navegador e carregar apenas `report.json`. O relatório projeta exclusivamente endereço/tipo/região, drift do refresh-only e candidatos do inventário. State, valores, outputs, tags e conteúdo das mudanças nunca são publicados. Dados de inventário e endereços de recursos são informação operacional: manter acesso restrito aos artefatos.

`iac-evidence.yml` agenda observação a cada seis horas, com OIDC do papel de plan existente, sem apply, sem escrita de state e sem bloqueio remoto. Histórico: artefatos por execução, retenção de 14 dias. Falhas de permissão interrompem a coleta; não geram relatório indicando ausência de drift. Um refresh-only concorrente com apply não é snapshot transacional: confirmar resultado depois de aplicações. Nenhuma aprovação GMUD/GLPI/Kestra ou pipeline existente foi alterada.

Inventário de Resource Groups Tagging API é **parcial**, incluindo apenas serviços suportados e recursos etiquetados ou anteriormente etiquetados. Candidatos podem pertencer a outros states. Recursos sem tags exigem coletores específicos por serviço e escopo autorizado; não há alegação de cobertura completa. `last_apply` fica desconhecido até integração com evidência assinada do executor oficial; não derivar apply bem-sucedido de plan ou de CI verde.

Pendências de aceite: executar workflow pelo CI após revisão, verificar permissões de leitura do role para refresh e `tag:GetResources`, integrar cobertura por serviço, publicação autenticada via pipeline canônico, evidência do último apply, dashboard histórico. Não hospedar relatório real em Site público ou anexar state/plan aos artefatos.

## V2-002 (aguardando V2-001)

A inspeção do Worker canônico encontrou instrumentação existente em `orders/runtime/worker_metrics.py`: histogramas de processamento e contadores de resultados, retries, erros, heartbeat e inflight. Reutilizar essa fonte; não criar métricas duplicadas. Dashboards/alertas e testes integrados ainda não executados nesta entrega.

## V2-003 (aguardando etapas anteriores e orçamento)

Não há teto monetário definido para uma segunda região. Nenhuma infraestrutura DR foi provisionada ou região declarada mais barata. Comparação deve incluir EKS, EC2 ferramentas, NAT, ALB, armazenamento, logs, tráfego inter-regional, replicação de imagens/secrets e DynamoDB antes da seleção. Créditos não são limite de gasto.

SQS não replica mensagens automaticamente entre regiões. Continuidade requer outbox/replay durável e idempotência; não usar duplicação de consumidores como replicação. Validar impacto de Global Tables no protocolo de lease e impedir escrita ativa simultânea antes do failover. States separados não substituem coordenação sobre recursos globais. Chaos deve iniciar isolado e com interrupção automática; nenhum experimento foi executado contra o V1.

## Validação desta entrega

`python3 -m unittest discover -s tests -v`: oito testes aprovados, incluindo drift recursivo, redação de valores sensíveis, inventário ausente, cobertura parcial e isolamento de região. Sem mudanças Terraform, IAM, dados ou runtime do V1. Sem recursos cloud novos. Custo de recursos novos: US$ 0; minutos/artefatos GitHub e chamadas AWS do workflow dependem da quota/uso existente, não estimados como gratuitos. Não houve validação de navegador ou integração AWS.

## Evidência remota e bloqueio de ativação

CI do commit inicial aprovado: https://github.com/rlcosmo/cosmotech-infrastructure-iac/actions/runs/42366279454 — testes, build dos artefatos Lambda, fmt, init sem backend e validate Terraform concluídos com sucesso.

PR de revisão: https://github.com/rlcosmo/cosmotech-infrastructure-iac/pull/2 (draft). A tentativa de acionar `iac-evidence.yml` na branch retornou HTTP 404: o novo workflow ainda não está registrado na branch padrão. Sua ativação depende da incorporação pelo fluxo de revisão existente; nenhum merge ou alteração de proteção foi realizado. A variável do papel OIDC de plan existe; isso não comprova permissão de refresh/inventário. A execução autenticada e a URL do portal permanecem pendentes. A ordem V2-001 → V2-002 → V2-003 foi preservada.
