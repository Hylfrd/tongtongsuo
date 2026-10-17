#!/usr/bin/env bash
set -eu

printf 'OS: '
sed -n 's/^PRETTY_NAME=//p' /etc/os-release
printf 'Current uid: '
id -u
if [ "$(id -u)" -eq 0 ] || sudo -n true 2>/dev/null; then
  printf 'Service management privileges: available\n'
else
  printf 'Service management privileges: unavailable\n'
fi
for command in python3 python3.13 python3.12 caddy nginx systemctl curl; do
  command -v "$command" || true
done
python3 --version
python3 -m venv --help >/dev/null 2>&1 && printf 'venv module: available\n' || true
systemctl is-active caddy nginx tongtongsuo 2>/dev/null || true
printf '\nListening TCP sockets:\n'
ss -ltnp
printf '\nDisk capacity:\n'
df -h /var/www /tmp
if [ -r /etc/caddy/Caddyfile ]; then
  printf '\nCaddy site routing (no full config or credentials):\n'
  caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null | python3 -c '
import json, sys
config = json.load(sys.stdin)
def visit(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("host", "listen"):
                print(key, json.dumps(item))
            visit(item)
    elif isinstance(value, list):
        for item in value:
            visit(item)
visit(config.get("apps", {}).get("http", {}))
'
fi
