#!/usr/bin/env bash
# SSM accepts only an immutable main commit; this code never prints credentials.
set -Eeuo pipefail
umask 077
revision="${1:?Exact GitOps commit is required}"
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
exec 9>/run/cosmotech-orders-gitops.lock
flock -w 60 9
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
python3 scripts/validate_app_manifests.py
kubectl -n argocd patch secret cosmotech-eks --type merge \
  -p '{"stringData":{"namespaces":"orders-api,orders-worker"}}' >/dev/null
kubectl apply -f gitops/bootstrap/orders-project.yaml
kubectl apply -f gitops/bootstrap/orders-application.yaml
kubectl apply -f gitops/bootstrap/orders-worker-application.json
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
