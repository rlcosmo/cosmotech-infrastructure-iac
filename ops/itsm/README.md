# GLPI and Kestra on the private ITSM host

Terraform provisions one Ubuntu 24.04 LTS `t3.large` in the existing EKS VPC's private subnet. The instance has an encrypted 50 GiB gp3 root disk, no public IP, no inbound security-group rules, an EC2 instance profile, and SSM Session Manager access. Its role can inspect the lab, read the DEV Terraform state, write the two generated bootstrap passwords to their fixed SSM parameter paths, and view the EKS cluster. It cannot apply Terraform changes.

GLPI and its MariaDB database are separate from Kestra and its PostgreSQL database. Docker volumes persist application files and each database on the encrypted EBS root volume. A short-lived, network-isolated BusyBox init container sets the Kestra volume owner to UID 1000, matching the non-root Kestra process. Compose publishes only loopback ports `8080` and `8081`; SSM port forwarding is the access path. No web listener is exposed to the internet.

## Access

Get the instance ID from the initialized Terraform environment:

```bash
terraform -chdir=terraform/environments/dev output -raw itsm_instance_id
```

Start one port-forwarding session for each application:

```bash
aws ssm start-session --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'
```

```bash
aws ssm start-session --target INSTANCE_ID \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8081"],"localPortNumber":["8081"]}'
```

Then open `http://localhost:8080` (GLPI) or `http://localhost:8081` (Kestra). The tunnel is carried by Systems Manager; these host ports are not reachable directly from the network.

Bootstrap rotates GLPI's default `glpi` password, disables the unused default `tech`, `normal`, and `post-only` accounts, and generates a Kestra instance-owner password. The two administrator passwords are stored as SSM `SecureString` parameters:

- `/cosmotech/itsm/glpi/admin-password`
- `/cosmotech/itsm/kestra/admin-password`

Retrieve a password only into a trusted terminal using an IAM identity that has `ssm:GetParameter` access. The instance role has no permission to read these parameters. Do not paste the result into shell history, logs, tickets, or chat.

Kestra's script Docker task runner can start containers through the Docker socket. This provides host-level execution and the instance role to workflows. Keep Kestra reachable only over SSM and grant UI access only to trusted lab operators. The role does not have AWS write permissions for infrastructure changes; those require a separately approved change path.

## Operations

The Compose file pins official GLPI 12.0.1, Kestra 2.0.8, MariaDB 11.4, and PostgreSQL 17 images by immutable multi-platform manifest digest. GLPI's auto-updater is disabled so database migrations occur only as a reviewed upgrade. The host bootstrap installs AWS CLI, Terraform 1.17.1, kubectl compatible with EKS 1.37, Helm 4.3.0, Docker Engine, and the Compose plugin.

Useful commands from an SSM shell:

```bash
sudo docker compose --project-name cosmotech-itsm \
  --env-file /opt/cosmotech/secrets/itsm/runtime.env \
  --file /opt/cosmotech/aws-serverless-lab/ops/itsm/docker-compose.yml ps
```

```bash
sudo docker compose --project-name cosmotech-itsm \
  --env-file /opt/cosmotech/secrets/itsm/runtime.env \
  --file /opt/cosmotech/aws-serverless-lab/ops/itsm/docker-compose.yml logs --tail=100 glpi kestra
```

The generated environment file and Kestra config are root-owned and mode-restricted under `/opt/cosmotech`. Never put them in Git. Do not use `docker compose down -v`; named volumes contain the GLPI files, both databases, and Kestra execution storage.

AWS Backup creates daily EC2 recovery points and retains them for 14 days in a private vault. A recovery point includes the encrypted root EBS volume. To recover, restore an EC2 recovery point into the existing private subnet, preserve the restored volume, verify SSM registration and Compose health, then use the restored instance ID for SSM port forwarding. Keep the original instance and recovery point until application login, database health, and ticket/flow data have been verified.

The deployment is reproduced by `terraform/environments/dev/itsm.tf`, `terraform/modules/itsm_host`, `terraform/bootstrap/state_backend`, and the files in this directory. The bootstrap assumes the public `main` branch contains these files before Terraform creates the instance.

### Login GLPI com cookie lembrar-me (06/11/2026)

O erro nativo `Duplicate entry ... token_uid` ao abrir uma GMUD ocorria porque
`Session::init()` reutilizava o UID do cookie também em um novo login por senha.
`prepare-glpi-session-fix.sh` extrai somente o `Session.php` da imagem fixada,
confere SHA256, aplica a condição `Auth::COOKIE`, valida PHP e prepara o bind mount
somente leitura. Login explícito recebe UID aleatório novo; autenticação pelo
cookie conserva seu UID. Não altera senha, autorização, CSRF, cookies seguros,
índice único ou registros existentes. O bootstrap prepara o arquivo antes do
Compose. Imagem/arquivo divergente bloqueia a aplicação do patch para revisão.

Reversão: usar o backup do Compose e retirar o bind mount numa manutenção
controlada; não apagar tokens para contornar o erro. O patch deve ser reavaliado
ao atualizar a imagem GLPI. O teste operacional usa sessões isoladas, repete login
com lembrar-me e abre uma Change real; não aprova GMUDs.

### Canary SQS — falha de drenagem e IAM

RCL000005/Worker1.0.3 revelou `AccessDenied` em ChangeMessageVisibility ao retornar
uma mensagem recebida durante expiração do lease; dois processos reiniciaram.
O backlog ultrapassou o limite120s durante promoção, e o controlador restaurou
stable1.0.2, drenou candidatas e restaurou HPA. Não registrar a GMUD como sucesso.
A política inline existente `eks_orders_checkpoints` recebeu somente
`sqs:ChangeMessageVisibility` na fila EKS principal. Terraform alvo:0 adições,
1 atualização,0 exclusões; backup privado de estado e plano preservados;
Access Analyzer sem findings. Simulação IAM permitiu a fila exata e negou a DLQ.
Nenhum recurso de computação novo ou operação NLB/CloudTrail.

A release corretiva Worker1.0.4 trata falha de devolução sem processar/apagar a
mensagem e sem encerrar o processo; expõe contador próprio. Prova integrada dessa
release deve ser consultada no índice de evidências, sem inferir sucesso do CI.
