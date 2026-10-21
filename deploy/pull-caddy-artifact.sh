#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

run=${MAINTENANCE_RUN:?Expected maintenance run ID}
[[ "$run" =~ ^[0-9]+-[0-9]+$ && $(id -u) == 0 ]] || exit 2
incoming="/var/www/tongtongsuo/caddy-security/$run"
[[ -d "$incoming" && -f "$incoming/artifact-url" && ! -e "$incoming/artifact.zip" ]]
trap 'rm -f "$incoming/artifact-url"' EXIT
trap 'exit 1' HUP INT TERM
python3 - "$incoming/artifact-url" <<'PY'
import re, sys
from pathlib import Path
from urllib.parse import urlsplit
transfer = Path(sys.argv[1])
lines = transfer.read_text().splitlines()
assert len(lines) == 2 and re.fullmatch(r"[0-9a-f]{64}", lines[1]), "Expected authenticated artifact digest"
url = urlsplit(lines[0])
assert url.scheme == "https" and url.port in (None, 443) and not url.username and not url.password
assert re.fullmatch(r"productionresultssa[0-9a-z]*\.blob\.core\.windows\.net", url.hostname or ""), "Unexpected artifact host"
assert url.path.startswith("/actions-results/") and not url.fragment
(transfer.parent / "archive.sha256").write_text(lines[1] + "  artifact.zip\n")
PY
IFS= read -r artifact_url < "$incoming/artifact-url"
curl --fail --silent --show-error --connect-timeout 15 --max-time 900 --proto '=https' --output "$incoming/artifact.zip" "$artifact_url"
unset artifact_url
rm -f "$incoming/artifact-url"
(cd "$incoming" && sha256sum --check archive.sha256)
python3 - "$incoming" <<'PY'
from pathlib import Path
import sys, zipfile
destination = Path(sys.argv[1])
expected = {"caddy.gz", "caddy.sha256", "deploy/update-caddy.sh"}
with zipfile.ZipFile(destination / "artifact.zip") as archive:
    items = archive.infolist()
    assert len(items) == len(expected) and {item.filename for item in items} == expected, "Unexpected artifact entries"
    assert all(not item.is_dir() and item.file_size < 100_000_000 for item in items), "Unexpected artifact sizes"
    archive.extractall(destination)
print("Downloaded and extracted the verified Caddy artifact over HTTPS")
PY
bash "$incoming/deploy/update-caddy.sh" "$run"
