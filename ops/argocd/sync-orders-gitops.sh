#!/usr/bin/env bash
# SSM accepts only an immutable main commit; this code never prints credentials.
set -Eeuo pipefail
umask 077
revision="${1:?Exact GitOps commit is required}"
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
source /etc/cosmotech/promotion/enforcement.env
[[ "${PROMOTION_ENFORCEMENT_READY:-false}" == true ]] || exit 3
[[ "${PROMOTION_GPG_FINGERPRINT:-}" =~ ^([A-F0-9]{40}|[A-F0-9]{64})$ ]] || exit 3
export GNUPGHOME=/opt/cosmotech/promotion/gnupg-public
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
exec 9>/run/cosmotech-orders-gitops.lock
# The release gate and GitHub observer may reconcile the same revision together.
# A healthy rollout can hold this lock for longer than one minute.
if ! flock -w 180 9; then
  echo 'Another Argo reconciliation still owns the lock after 180 seconds; retry this immutable revision.' >&2
  exit 75
fi
work_dir="$(mktemp -d /opt/cosmotech/argocd/sync.XXXXXX)"
trap 'rm -rf "$work_dir"' EXIT
kubectl -n argocd get configmap argocd-ssh-known-hosts-cm -o jsonpath='{.data.ssh_known_hosts}' > "$work_dir/known_hosts"
export GIT_SSH_COMMAND="ssh -i /opt/cosmotech/argocd/github-deploy-key -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=$work_dir/known_hosts"
git clone --quiet --no-checkout ssh://git@ssh.github.com:443/rlcosmo/cosmotech-gitops.git "$work_dir/source"
cd "$work_dir/source"
if [[ "$(git rev-parse origin/main)" != "$revision" ]]; then
  echo 'Superseded by a newer main commit; the newer pipeline owns delivery.'
  exit 0
fi
git checkout --quiet --detach "$revision"
git -c core.hooksPath=/dev/null verify-commit --raw "$revision" 2>"$work_dir/signature-status"
python3 - "$work_dir/signature-status" "$PROMOTION_GPG_FINGERPRINT" <<'VERIFY'
import pathlib,sys
records=[line.split() for line in pathlib.Path(sys.argv[1]).read_text().splitlines()
         if line.startswith('[GNUPG:] VALIDSIG ')]
if not any(sys.argv[2] in (record[2],record[-1]) for record in records):
    raise SystemExit('GitOps revision is not signed by the promotion authority')
VERIFY
# This validator and the AppProject are managed outside the delivery repository.
python3 /opt/cosmotech/promotion/validate_app_manifests.py
for app in orders-api orders-worker; do
  if [[ "$app" == orders-worker ]] && ! python3 -c 'import json,sys; sys.exit(0 if json.load(open("gitops/bootstrap/orders-worker-application.json"))["spec"]["syncPolicy"]["automated"]["enabled"] else 1)'; then
    echo 'Worker activation is pending its first health-enabled CI image.'
    continue
  fi
  kubectl -n argocd annotate application "$app-dev" argocd.argoproj.io/refresh=hard --overwrite >/dev/null
  # Require this exact main revision as well as Synced/Healthy; old healthy status is insufficient.
  python3 - "$app-dev" "$revision" <<'PY'
import json,subprocess,sys,time
name,revision=sys.argv[1:]
for attempt in range(150):
    app=json.loads(subprocess.check_output(['kubectl','-n','argocd','get','application',name,'-o','json']))
    status=app.get('status',{})
    if status.get('sync',{}).get('revision')==revision and status['sync'].get('status')=='Synced' and status.get('health',{}).get('status')=='Healthy':
        print(name+': Synced/Healthy at '+revision)
        break
    time.sleep(2)
else:
    print(json.dumps({'application':name,'conditions':status.get('conditions',[]),'sync':status.get('sync',{}),'health':status.get('health',{})}))
    raise SystemExit('Argo CD did not reconcile the expected revision')
PY
done
