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
printf '\nUbuntu Python and OpenSSL package versions and cached APT policy:\n'
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W -f='${binary:Package} ${Version}\n' python3.12 libpython3.12-minimal libpython3.12-stdlib openssl libssl3t64 2>/dev/null || true
fi
if command -v apt-cache >/dev/null 2>&1; then
  apt-cache policy python3.12 libpython3.12-minimal libpython3.12-stdlib openssl libssl3t64
fi
caddy version 2>/dev/null || true
caddy build-info 2>/dev/null || true
printf '\nInstalled Node.js runtime and active Node.js processes:\n'
for command in node nodejs; do
  if command -v "$command" >/dev/null 2>&1; then
    command -v "$command"
    "$command" --version
  fi
done
ps -C node,nodejs -o pid=,comm= || true
for pid in $(pgrep -x 'node|nodejs' || true); do
  if [ -x "/proc/$pid/exe" ]; then
    printf 'Running Node.js process %s: ' "$pid"
    "/proc/$pid/exe" --version
  fi
done
printf '\nInstalled application packages:\n'
if [ -x /var/www/tongtongsuo/current/.venv/bin/python ]; then
  /var/www/tongtongsuo/current/.venv/bin/python -c '
from importlib.metadata import distributions
for package in sorted(distributions(), key=lambda item: item.metadata["Name"].lower()):
    print(package.metadata["Name"], package.version)
'
fi
printf '\nPHP executables and installed packages:\n'
for command in php php-fpm frankenphp; do
  command -v "$command" || true
done
if command -v dpkg-query >/dev/null 2>&1; then
  dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package} ${Version}\n' 'php*' 'frankenphp*' 2>/dev/null | grep '^ii ' || true
fi
printf '\nActive PHP services and Caddy PHP modules:\n'
systemctl list-units --type=service --state=running --no-legend 'php*' 'frankenphp*' 2>/dev/null || true
caddy list-modules 2>/dev/null | grep -Ei 'php|franken' || true
if python3 -m venv --help >/dev/null 2>&1; then
  printf 'venv module: available\n'
fi
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
