"""Prepare and fetch an exact wheel manifest without transferring wheels over SSH."""

import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen


def prepare(report_path: Path, manifest_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = [
        {
            "url": item["download_info"]["url"],
            "sha256": item["download_info"]["archive_info"]["hashes"]["sha256"],
        }
        for item in report["install"]
    ]
    if not manifest:
        raise ValueError("Empty dependency manifest")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def download(manifest_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest:
        url = urlsplit(item["url"])
        filename = unquote(url.path.rsplit("/", 1)[-1])
        if (
            url.scheme != "https" or url.netloc != "files.pythonhosted.org"
            or "/" in filename or "\\" in filename or not filename.endswith(".whl")
        ):
            raise ValueError("Expected an official PyPI wheel URL")
        target = destination / filename
        partial = target.with_suffix(".part")
        digest = hashlib.sha256()
        print(f"Downloading {filename}", flush=True)
        with urlopen(item["url"], timeout=30) as response, partial.open("wb") as output:
            while chunk := response.read(65536):
                digest.update(chunk)
                output.write(chunk)
        if digest.hexdigest() != item["sha256"]:
            raise ValueError(f"Checksum mismatch for {filename}")
        partial.replace(target)
        print(f"Verified {filename}", flush=True)


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in ("prepare", "download"):
        raise SystemExit("Usage: wheels.py prepare|download INPUT OUTPUT")
    operation = prepare if sys.argv[1] == "prepare" else download
    operation(Path(sys.argv[2]), Path(sys.argv[3]))
