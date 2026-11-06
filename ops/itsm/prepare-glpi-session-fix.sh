#!/usr/bin/env bash
# Guarded patch for the pinned GLPI image: rotate UID on explicit login.
set -Eeuo pipefail
umask 077
image=glpi/glpi@sha256:f1a08a72afa579393e8bda76edbe9adad00591b5a2228d7953cadc7f04e221be
patch_dir=/opt/cosmotech/itsm/patches
install -d -m 0755 "$patch_dir"
container=$(docker create --network none --entrypoint /bin/true "$image")
trap 'docker rm "$container" >/dev/null' EXIT
docker cp "$container:/var/www/glpi/src/Session.php" "$patch_dir/Session.php.new"
python3 - "$patch_dir/Session.php.new" <<'PY'
import hashlib,sys
from pathlib import Path
p=Path(sys.argv[1]);source=p.read_bytes()
if hashlib.sha256(source).hexdigest()!='d98b7f67e4bd450a2ebdccda4da6081825c555b39212c05f4ea8ca6b40d172e4':
    raise SystemExit('Unreviewed GLPI Session.php; patch blocked')
old=b"if (isset($_COOKIE[$cookie_name])) {\n            $login_session_uid = explode(':', $_COOKIE[$cookie_name])[0];"
new=b"if ($auth->getAuthType() === Auth::COOKIE && isset($_COOKIE[$cookie_name])) {\n            $login_session_uid = explode(':', $_COOKIE[$cookie_name])[0];"
assert source.count(old)==1
p.write_bytes(source.replace(old,new))
PY
docker run --rm --network none --entrypoint php --mount "type=bind,source=$patch_dir/Session.php.new,target=/tmp/Session.php,readonly" "$image" -l /tmp/Session.php
if [ -e "$patch_dir/Session.php" ]; then cp -p "$patch_dir/Session.php" "$patch_dir/Session.php.backup-$(date -u +%Y%m%dT%H%M%SZ)"; fi
chmod 0644 "$patch_dir/Session.php.new"
mv "$patch_dir/Session.php.new" "$patch_dir/Session.php"
