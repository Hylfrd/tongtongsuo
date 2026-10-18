#!/usr/bin/env bash
set -Eeuo pipefail
umask 022

# The existing host is Ubuntu with systemd and Caddy. Never replace other sites.
sha=${1:?Expected commit SHA}
run=${2:?Expected deployment run ID}
[[ "$sha" =~ ^[0-9a-f]{40}$ && "$run" =~ ^[0-9]+-[0-9]+$ ]] || exit 2
[[ $(id -u) == 0 ]] || { echo 'Deployment needs root for its systemd unit and Caddy import.'; exit 1; }
base=/var/www/tongtongsuo
incoming="$base/incoming/$run"
release="$base/releases/$sha-$run"
backup="$base/backups/$run"
unit=/etc/systemd/system/tongtongsuo.service
caddyfile=/etc/caddy/Caddyfile
site=/etc/caddy/tongtongsuo.caddy
import='import /etc/caddy/tongtongsuo.caddy'

mkdir -p "$base/releases" "$base/backups"
exec 9>"$base/deploy.lock"
flock -n 9 || { echo 'Another deployment is running.'; exit 1; }
[[ -f "$incoming/release.tar.gz" && -f "$caddyfile" ]]
[[ ! -e "$release" && ! -e "$backup" ]] || { echo 'Run already exists; rerun the workflow for a new attempt.'; exit 1; }
systemctl is-active --quiet caddy
if ! systemctl is-active --quiet tongtongsuo && ss -ltnH 'sport = :18731' | grep -q .; then
  echo 'Port 18731 belongs to another process; refusing to deploy.'
  exit 1
fi
if [[ -e "$base/current" && ! -L "$base/current" ]]; then
  echo 'The current release path is not a managed symlink.'
  exit 1
fi
if [[ -e "$site" ]] && ! grep -qF '# Managed by the tongtongsuo repository;' "$site"; then
  echo 'An unmanaged Caddy site file exists; refusing to overwrite it.'
  exit 1
fi
if [[ -e "$unit" ]] && ! grep -qF 'Description=Tongtongsuo web and API' "$unit"; then
  echo 'An unrelated service already uses the tongtongsuo name.'
  exit 1
fi

mkdir "$release" "$backup"
tar -xzf "$incoming/release.tar.gz" -C "$release"
[[ "$(cat "$release/RELEASE")" == "$sha" ]]
[[ -f "$release/frontend/dist/index.html" ]]
# Fetch exactly the wheels selected by CI, verifying their published checksums.
python3 "$release/deploy/wheels.py" download "$release/wheel-manifest.json" "$release/wheels"
# Ubuntu can provide venv without ensurepip. Bootstrap from the verified pip
# wheel, leaving system Python untouched and installing from the local wheel set.
python3 -m venv --without-pip "$release/.venv"
pip_wheels=("$release"/wheels/pip-*.whl)
[[ ${#pip_wheels[@]} == 1 && -f "${pip_wheels[0]}" ]]
PYTHONPATH="${pip_wheels[0]}" "$release/.venv/bin/python" -m pip install \
  --disable-pip-version-check --no-index --find-links "$release/wheels" \
  pip -r "$release/backend/requirements.txt"
printf 'APP_VERSION=%s\n' "$sha" > "$release/release.env"

previous=''
if [[ -L "$base/current" ]]; then
  previous=$(readlink -f "$base/current")
  [[ -d "$previous" && "$previous" == "$base/releases/"* ]] || {
    echo 'Current release does not point to a managed release directory.'
    exit 1
  }
fi
was_active=false
systemctl is-active --quiet tongtongsuo && was_active=true
was_enabled=false
systemctl is-enabled --quiet tongtongsuo 2>/dev/null && was_enabled=true
cp -p "$caddyfile" "$backup/Caddyfile"
caddy_hash=$(sha256sum "$caddyfile" | cut -d ' ' -f1)
[[ ! -e "$unit" ]] || cp -p "$unit" "$backup/tongtongsuo.service"
[[ ! -e "$site" ]] || cp -p "$site" "$backup/tongtongsuo.caddy"
[[ ! -e /etc/tongtongsuo.env ]] || cp -p /etc/tongtongsuo.env "$backup/tongtongsuo.env"
caddy_changed=false
admin_config_changed=false

rollback() {
  code=$?
  trap - ERR
  set +e
  echo 'Deployment failed; restoring the previous application and proxy configuration.'
  if $caddy_changed; then
    cp -p "$backup/Caddyfile" "$caddyfile"
    if [[ -e "$backup/tongtongsuo.caddy" ]]; then
      cp -p "$backup/tongtongsuo.caddy" "$site"
    else
      rm -f "$site"
    fi
    caddy validate --config "$caddyfile" --adapter caddyfile && systemctl reload caddy
  fi
  systemctl stop tongtongsuo
  if $admin_config_changed; then
    if [[ -e "$backup/tongtongsuo.env" ]]; then
      cp -p "$backup/tongtongsuo.env" /etc/tongtongsuo.env
    else
      rm -f /etc/tongtongsuo.env
    fi
  fi
  if [[ -n "$previous" && -d "$previous" ]]; then
    ln -sfn "$previous" "$base/current.rollback"
    mv -Tf "$base/current.rollback" "$base/current"
  else
    rm -f "$base/current"
  fi
  if [[ -e "$backup/tongtongsuo.service" ]]; then
    cp -p "$backup/tongtongsuo.service" "$unit"
  else
    systemctl disable tongtongsuo 2>/dev/null
    rm -f "$unit"
  fi
  systemctl daemon-reload
  if $was_enabled; then systemctl enable tongtongsuo; else systemctl disable tongtongsuo 2>/dev/null; fi
  if $was_active; then systemctl start tongtongsuo; fi
  exit "$code"
}
trap rollback ERR

if [[ -s "$incoming/admin.env" ]]; then
  admin_config_changed=true
  install -m 600 "$incoming/admin.env" /etc/tongtongsuo.env
  rm -f "$incoming/admin.env"
fi
install -m 644 "$release/deploy/tongtongsuo.service" "$unit"
ln -sfn "$release" "$base/current.next"
mv -Tf "$base/current.next" "$base/current"
systemctl daemon-reload
systemctl restart tongtongsuo

check_release() {
  curl --fail --silent --show-error --max-time 5 "$1" | python3 -c '
import json, sys
data = json.load(sys.stdin)
assert data.get("status") == "ok" and data.get("service") == "tongtongsuo"
assert data.get("version") == sys.argv[1], "Unexpected deployed revision"
' "$sha"
}
healthy=false
for _attempt in $(seq 1 15); do
  if check_release http://127.0.0.1:18731/api/health 2>/dev/null; then healthy=true; break; fi
  sleep 2
done
$healthy || { echo 'Application did not become healthy.'; false; }
curl --fail --silent --show-error --max-time 5 http://127.0.0.1:18731/ >/dev/null

# Stop if another administrator changed the main configuration during deployment.
[[ "$(sha256sum "$caddyfile" | cut -d ' ' -f1)" == "$caddy_hash" ]]
caddy_changed=true
install -m 644 "$release/deploy/bnbu.caddy" "$site"
if ! grep -qxF "$import" "$caddyfile"; then
  printf '\n# Tongtongsuo: isolated campus lock site\n%s\n' "$import" >> "$caddyfile"
fi
caddy validate --config "$caddyfile" --adapter caddyfile
systemctl reload caddy

# Allow automatic certificate issuance, checking through the actual public URL.
healthy=false
for _attempt in $(seq 1 24); do
  if check_release https://bnbu.hmcl-helper.cn/api/health 2>/dev/null; then healthy=true; break; fi
  sleep 5
done
$healthy || { echo 'Public HTTPS did not become healthy; check DNS and certificate issuance.'; false; }
curl --fail --silent --show-error --max-time 10 https://bnbu.hmcl-helper.cn/ >/dev/null
systemctl enable tongtongsuo
trap - ERR
printf 'Deployed and verified %s at https://bnbu.hmcl-helper.cn\n' "$sha"
printf 'Backup retained at %s\n' "$backup"
