# Orders: corrida da primeira startup probe Istio

Diagnóstico real06/11/2026: um Warning em cada pod Orders na primeira consulta a15021/healthz/ready, anterior à abertura do listener istio-proxy. Proxy nativo em initContainers, restartPolicy Always. Ambos os workloads ficaram prontos, sem reinícios. Não era falha permanente de API/Worker, nem evidência de Canary.

Istio instalado1.31.3 inicia essa probe com initialDelaySeconds0, período1s e failureThreshold600. O script set-orders-startup-delay.py muda apenas o atraso inicial para5s quando label app é orders-api ou orders-worker. Mantém startup/readiness, limite de falhas e demais workloads. Não usar a annotation de readiness para alterar startup: ela não controla a probe que falhou.

Aplicação: `python3 ops/istio/set-orders-startup-delay.py --apply --backup /caminho/privado/novo-backup.json`. Verifica versão/template, salva snapshot600, faz dry-run server e replace com resourceVersion. Idempotente; drift/template inesperado bloqueia. Backup real desta execução está em /tmp/cosmotech-istio-injector-before-startup-delay-20261106.json, fora do Git. Reversão exige conferir configuração atual e restaurar somente o trecho alterado, preservando alterações posteriores.

Não houve rollout, restart ou alteração das imagens/GMUDs das aplicações. Novos pods recebem o ajuste; pods existentes e eventos antigos não são modificados. Dois pods temporários de teste, sem tráfego de aplicação e sem consumidor SQS, foram realmente injetados em default, um por label Orders. Ambos receberam5s, ficaram2/2 Ready, com zero reinícios e zero Warning. Foram removidos depois de preservar startup-probe-evidence-20261106.json. Não foram testes de negócio ou Canary.

A configuração do injector recarrega sem restart do plano de controle. Upgrade/reinstalação Istio pode regenerar ConfigMap: revisar o template novo e manter o ajuste na configuração da instalação; o script bloqueia versões diferentes. Referência oficial: https://istio.io/latest/docs/setup/additional-setup/sidecar-injection/ .

Deploys anteriores aprovados: GMUD3/API RollingUpdate; GMUD4/Worker bootstrap rolling. Nenhuma candidata Canary ativa. GLPI em Review8/OBSERVING; não encerrar como sucesso antes das12h reais.
