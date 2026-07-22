import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}$")
request = json.loads(Path("/input/request.json").read_text(encoding="utf-8"))
output = Path("/output")
wheels = output / "wheels"
wheels.mkdir()
resolved = []
try:
    for item in request.get("dependency_intents", []):
        name = str(item.get("name", ""))
        version = str(item.get("version", ""))
        expected = str(item.get("sha256", ""))
        if not NAME.fullmatch(name) or not VERSION.fullmatch(version) or len(expected) != 64:
            raise ValueError("every dependency must freeze name, version and sha256")
        before = set(wheels.iterdir())
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "download",
                "--only-binary=:all:",
                "--no-deps",
                "--disable-pip-version-check",
                "--dest",
                str(wheels),
                f"{name}=={version}",
            ],
            check=True,
        )
        created = set(wheels.iterdir()) - before
        if len(created) != 1:
            raise ValueError(f"resolver produced unexpected files for {name}")
        candidate = created.pop()
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        if digest != expected:
            raise ValueError(f"hash mismatch for {name}")
        resolved.append(
            {"name": name, "version": version, "sha256": digest, "file": candidate.name}
        )
    lock = output / "requirements.lock.json"
    lock.write_text(json.dumps(resolved, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    sbom = output / "sbom.cdx.json"
    sbom.write_text(
        json.dumps(
            {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": resolved}, sort_keys=True
        ),
        encoding="utf-8",
    )
    bundle = output / "dependencies.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(wheels.iterdir()):
            archive.write(path, f"wheels/{path.name}")
        archive.write(lock, "requirements.lock.json")
        archive.write(sbom, "sbom.cdx.json")
    result = {
        "status": "MATERIALIZED",
        "lockfile": lock.name,
        "bundle_file": bundle.name,
        "sbom": sbom.name,
        "evidence": {"resolver": "python-web-v1", "count": len(resolved)},
    }
except Exception as exc:
    result = {
        "status": "REJECTED",
        "failure_code": "DEPENDENCY_UNRESOLVED",
        "evidence": {"error": str(exc)},
    }
Path("/output/result.json").write_text(json.dumps(result), encoding="utf-8")
