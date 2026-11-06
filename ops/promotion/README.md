# Controles de promoção aplicados no laboratório

Documentos SSM fixos de coleta Argo, stages e solicitação CI; políticas IAM revisadas com Access Analyzer sem findings; entry EKS existente ampliada aditivamente preservando backup/ViewPolicy. Sem nova entry duplicada, compute ou ampliação de nós.

Argo project exige autoridade GPG; chave pública em ops/argocd/promotion-enforcement.yaml, chave privada somente no host. Sync executa validador/script confiáveis instalados da IaC, não bootstrap mutável do GitOps. CI perdeu segredo App de escrita. Assinatura alternativa realmente recusada, sem sync do app diagnóstico. Admins permanecem fronteira privilegiada.

Runtime RBAC limita alterações às escalas/ConfigMaps Worker nomeados e leitura de proxy Prometheus. apply-worker-control-ignore.py preserva ignoreDifferences preexistentes e acrescenta somente campos de lease/mode Worker; foi aplicado no lab. Nenhuma estratégia API modificada.

ServiceMonitor eks-control-plane-metrics usa endpoints oficiais EKS com CA/serverName, quatro targets up e alertas resolvidos sem supressão. Referência: https://docs.aws.amazon.com/eks/latest/userguide/view-raw-metrics.html .

Planos direcionados foram revisados antes dos applies. Nenhuma aplicação promovida: GMUD3/API e4/Worker aguardam humano. Canary, rollback EKS e12h reais pendentes. Bedrock opcional/desabilitado; permissões anteriores preservadas sem invocação. Sem NLB/CloudTrail. Documentação de operação/retorno em cosmotech-automation/docs/gmud-operations.md e showcase10/14 do laboratório.
