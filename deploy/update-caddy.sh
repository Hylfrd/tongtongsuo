#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

run=${1:?Expected maintenance run ID}
[[ "$run" =~ ^[0-9]+-[0-9]+$ ]] || exit 2
[[ $(id -u) == 0 && $(uname -s) == Linux && $(uname -m) == x86_64 ]] || {
  echo 'Expected root on the existing Linux x86_64 host.'
  exit 1
}
incoming="/var/www/tongtongsuo/caddy-security/$run"
candidate="$incoming/caddy"
config=/etc/caddy/Caddyfile
for command in caddy getcap setcap flock curl python3; do
  command -v "$command" >/dev/null
done
[[ -f "$incoming/caddy.gz" && -f "$incoming/caddy.sha256" && -f "$config" && ! -e "$candidate" ]]
gzip --decompress --keep -- "$incoming/caddy.gz"
expected=$(cut -d ' ' -f1 "$incoming/caddy.sha256")
[[ "$expected" =~ ^[0-9a-f]{64}$ ]]
[[ $(sha256sum "$candidate" | cut -d ' ' -f1) == "$expected" ]]
chmod 755 "$candidate"

verify_build() {
  "$1" version | grep -Eq '^v2\.11\.7($| )'
  "$1" build-info > "$incoming/verified-build-info.txt"
  grep -Eq '(^|[[:space:]])go1\.27\.3([[:space:]]|$)' "$incoming/verified-build-info.txt"
  grep -Eq 'golang.org/x/net[[:space:]]+v0\.60\.0([[:space:]]|$)' "$incoming/verified-build-info.txt"
}
verify_build "$candidate"
exec 9>/var/www/tongtongsuo/deploy.lock
flock -n 9 || { echo 'An application deployment is running.'; exit 1; }
systemctl is-active --quiet caddy
binary=$(readlink -f "$(command -v caddy)")
[[ "$binary" == /usr/bin/caddy || "$binary" == /usr/local/bin/caddy ]]
pid=$(systemctl show --property=MainPID --value caddy)
[[ "$pid" =~ ^[1-9][0-9]*$ && $(readlink -f "/proc/$pid/exe") == "$binary" ]]
"$binary" list-modules | awk '/^[a-z][a-z0-9_.-]*$/' | sort -u > "$incoming/modules.before"
"$candidate" list-modules | awk '/^[a-z][a-z0-9_.-]*$/' | sort -u > "$incoming/modules.candidate"
comm -23 "$incoming/modules.before" "$incoming/modules.candidate" > "$incoming/modules.missing"
[[ ! -s "$incoming/modules.missing" ]] || { echo 'Candidate lacks existing Caddy modules.'; cat "$incoming/modules.missing"; exit 1; }
"$candidate" validate --config "$config" --adapter caddyfile
snapshot_config() {
  find /etc/caddy -type f -print0 | sort -z | xargs -0 -r sha256sum > "$1"
}
snapshot_config "$incoming/config.before.sha256"
before_hash=$(sha256sum "$binary" | cut -d ' ' -f1)

check_sites() {
  local host scope status
  for host in hmcl-helper.cn bnbu.hmcl-helper.cn mail.hmcl-helper.cn; do
    for scope in local public; do
      local options=()
      [[ "$scope" != local ]] || options=(--resolve "$host:443:127.0.0.1")
      status=$(curl --silent --show-error --connect-timeout 3 --max-time 8 --output /dev/null --write-out '%{http_code}' "${options[@]}" "https://$host/") || return 1
      [[ "$status" =~ ^[23][0-9][0-9]$ ]] || return 1
      printf '%s %s HTTPS %s\n' "$host" "$scope" "$status"
    done
  done
}
check_sites
app_version=$(curl --fail --silent --show-error --max-time 15 https://bnbu.hmcl-helper.cn/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "ok"; print(d["version"])')
backup="$incoming/caddy.previous"
stage="$binary.security-$run"
restore="$binary.restore-$run"
[[ ! -e "$backup" && ! -e "$stage" && ! -e "$restore" ]]
cp --preserve=all -- "$binary" "$backup"
cp --preserve=all -- "$binary" "$stage"
capabilities=$(getcap "$binary")
capabilities=${capabilities#"$binary"}
capabilities=${capabilities# }
switched=false
rollback() {
  code=$?
  [[ "$code" != 0 ]] || code=1
  trap - ERR HUP INT TERM
  set +e
  if $switched; then
    echo 'Caddy verification failed; restoring the previous binary.'
    cp --preserve=all -- "$backup" "$restore" && mv -Tf -- "$restore" "$binary" && systemctl restart caddy
    systemctl is-active --quiet caddy && check_sites
  fi
  rm -f -- "$stage"
  exit "$code"
}
trap rollback ERR HUP INT TERM
cat "$candidate" > "$stage"
[[ -z "$capabilities" ]] || setcap "$capabilities" "$stage"
[[ $(sha256sum "$stage" | cut -d ' ' -f1) == "$expected" ]]
[[ $(sha256sum "$binary" | cut -d ' ' -f1) == "$before_hash" ]]
snapshot_config "$incoming/config.pre-switch.sha256"
cmp -s "$incoming/config.before.sha256" "$incoming/config.pre-switch.sha256"
switched=true
mv -Tf -- "$stage" "$binary"
systemctl restart caddy
systemctl is-active --quiet caddy
pid=$(systemctl show --property=MainPID --value caddy)
[[ "$pid" =~ ^[1-9][0-9]*$ ]]
[[ $(sha256sum "/proc/$pid/exe" | cut -d ' ' -f1) == "$expected" ]]
verify_build "/proc/$pid/exe"
healthy=false
for _attempt in $(seq 1 3); do
  if check_sites; then healthy=true; break; fi
  sleep 2
done
$healthy
curl --fail --silent --show-error --max-time 15 https://bnbu.hmcl-helper.cn/api/health | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d["status"] == "ok" and d["version"] == sys.argv[1]' "$app_version"
snapshot_config "$incoming/config.after.sha256"
cmp -s "$incoming/config.before.sha256" "$incoming/config.after.sha256"
trap - ERR HUP INT TERM
printf 'Verified running Caddy v2.11.7, Go 1.27.3, x/net v0.60.0; SHA256 %s\n' "$expected"
printf 'Previous binary retained at %s\n' "$backup"
cat "$incoming/verified-build-info.txt"
