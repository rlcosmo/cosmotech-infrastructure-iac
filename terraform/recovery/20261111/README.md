# Volumes restaurados em 11/11/2026

Esta raiz é proprietária apenas dos sete EBS novos restaurados e de uma seleção suplementar de backup. Os volumes foram criados com client tokens estáveis e importados nesta raiz; um plano após import retornou no changes. Não contém os volumes antigos ausentes e não tenta recriar IDs EBS. Todos os sete discos e a seleção têm prevent_destroy.

Cinco discos de dados estão em us-east-1b e dois roots de Argo/Zabbix em us-east-1a. Os roots estão ligados às instâncias existentes com DeleteOnTermination=false. Attachments e lifecycle dos hosts precisam ser reconciliados com o owner dev quando seu backend voltar; não importar a mesma instância ou root block device nesta raiz. Kubernetes tem novos PV/PVC Retain, versionados no repositório observability. PVCs históricos e snapshots permanecem preservados.

Backend local transitório porque S3 retorna NotSignedUp; manter state mode-0600 e cópia privada. Não commitar state/plano nem aplicar com state vazio. Os IDs e snapshots estão na evidência sanitizada para permitir import/reconciliação. Após liberação do S3, revisar migração para prefixo de recuperação isolado no backend canônico. Não aplicar o root dev antigo como mudança isolada de recuperação.

Seleção supplemental: cinco volumes novos, plano diário existente 26e6a57c-13e7-485d-94d7-9ccd19c0af58, role de backup existente. Mantém seleções anteriores; não desabilita auditoria/retencão. Cinco jobs de backup on demand completaram. A captura genérica de volumes em execução é crash-consistent; a estratégia de consistência de aplicação e retenção do Tempo exige revisão específica antes de certificação DR.

Ações de destruição continuam bloqueadas por lifecycle. Uma eventual limpeza deve primeiro comprovar recovery points e validação de restore, além de confirmar que nenhum consumidor usa os volumes. Dados ativos recuperados não são recursos descartáveis do futuro ensaio Ohio.
