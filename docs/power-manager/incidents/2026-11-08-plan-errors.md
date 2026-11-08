# Preparação de plano e indisponibilidade Lambda — causa comprovada

## Causas independentes

1. O frontend só lia o campo JSON `error`. O API Gateway devolve `message` em
   falhas da integração. A mensagem resultante era vazia, originando
   “Falha ao preparar plano:” sem detalhe.
2. O serviço Lambda da conta passou a negar chamadas de controle e invocação.
   O 403 ocorre no endpoint padrão e FIPS de us-east-1, na sessão administrativa,
   via role OIDC e na console. A integração do API Gateway permanece vinculada
   à função esperada; o erro ocorre antes de executar o handler.
3. A causa desse bloqueio do provedor foi comprovada em AWS User Notifications:
   evento `Account Verification Required`, criado em 2026-11-08 às 18:30:55 UTC.
   A notificação informa pendência de verificação da conta. Seus links e detalhes
   privados não são copiados para código, PRs ou logs. A verificação deve ser
   concluída pelo titular no canal seguro da AWS. IAM não remove essa restrição.

O ciclo real aprovado terminou ON às 10:29 UTC, antes da notificação. A infraestrutura
recuperada permaneceu saudável na última leitura: 6 EC2 running, 3 nós Ready, 24
workloads saudáveis, GitOps Synced, 17/17 volumes originais e rede protegida idêntica.

## Correções técnicas

- Proxy normaliza erros JSON, texto/HTML inválidos, falhas de rede e timeout.
- Toda falha apresenta HTTP/código/correlação; inclui o request ID do API Gateway
  quando fornecido pelo serviço. Headers, tokens, secrets e corpos não são logados.
- O frontend aceita `error` e `message`, conserva uma correlação local em falhas
  de transporte e não habilita confirmação após resposta inválida ou expiração.
- A confirmação exige plano UUID válido, direção/frase coerentes, escopo não vazio,
  ausência de bloqueios, prazo válido e consentimento. O backend mantém autenticação,
  vínculo por ator, lock e idempotência e revalida a versão/certificação de DOWN.
- LIGAR mantém o caminho de recuperação pelo snapshot original, sem exigir a
  certificação de um novo DOWN.
- O handler backend registra correlação e request IDs, com mensagens seguras para
  erros esperados e inesperados. Isso não contorna o bloqueio da AWS.

## Validação e liberação

61 testes Python e testes DOM/proxy cobrem os botões, 401/403/404/500/502, timeout,
JSON inválido, contrato de plano, expiração, consentimento, correlação e redação
de informações sensíveis. Nenhum novo desligamento é executado durante o bloqueio.

Após a AWS liberar a conta: repetir a publicação oficial, certificar a versão,
preparar e consultar um plano DOWN com sessão pessoal, validar bloqueios/TTL e
confirmar estado operacional. A disponibilidade dos botões deve ser informada pelo
resultado ao vivo, não apenas por CI ou pela ausência de erro visual.
