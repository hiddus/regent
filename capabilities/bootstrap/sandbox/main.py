import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

input_dir = Path("/input")
output = Path("/output")
work = Path("/tmp/work")
checks = []
try:
    with zipfile.ZipFile(input_dir / "source.zip") as archive:
        for member in archive.infolist():
            target = (work / member.filename).resolve()
            if work.resolve() not in target.parents:
                raise ValueError("source archive path escape")
        archive.extractall(work)
    with zipfile.ZipFile(input_dir / "dependencies.bundle") as archive:
        for member in archive.infolist():
            target = (work / ".deps" / member.filename).resolve()
            if (work / ".deps").resolve() not in target.parents:
                raise ValueError("dependency archive path escape")
        archive.extractall(work / ".deps")
    lock = json.loads((work / ".deps" / "requirements.lock.json").read_text(encoding="utf-8"))
    site = Path("/tmp/site")
    requirements = [f"{item['name']}=={item['version']}" for item in lock]
    if requirements:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--no-deps",
                "--disable-pip-version-check",
                "--target",
                str(site),
                "--find-links",
                str(work / ".deps" / "wheels"),
                *requirements,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    commands = [
        [sys.executable, "-m", "compileall", "-q", str(work)],
    ]
    has_tests = (work / "tests").is_dir() or any(work.rglob("test_*.py")) or any(
        work.rglob("*_test.py")
    )
    if has_tests:
        commands.append([sys.executable, "-m", "pytest", "-q"])
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": os.pathsep.join((str(site), str(work / "src"), str(work))),
        "HOME": "/tmp",
    }
    for command in commands:
        completed = subprocess.run(
            command, cwd=work, env=env, capture_output=True, text=True, timeout=300
        )
        checks.append(
            {
                "command": command,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-4000:],
                "stderr": completed.stderr[-4000:],
            }
        )
        # pytest exit code 5 = no tests collected
        if completed.returncode != 0 and not (
            "pytest" in command and completed.returncode == 5
        ):
            raise RuntimeError("verification command failed")
    artifact = output / "app-source.zip"
    with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in work.rglob("*") if item.is_file()):
            if ".deps" not in path.parts and "__pycache__" not in path.parts:
                archive.write(path, path.relative_to(work).as_posix())
    evidence = output / "verification.json"
    evidence.write_text(json.dumps({"checks": checks}, sort_keys=True), encoding="utf-8")
    result = {
        "status": "PASSED",
        "evidence_file": evidence.name,
        "build_artifact_file": artifact.name,
        "checks": checks,
    }
except Exception as exc:
    evidence = output / "verification.json"
    evidence.write_text(
        json.dumps({"checks": checks, "error": str(exc)}, sort_keys=True), encoding="utf-8"
    )
    result = {"status": "FAILED", "evidence_file": evidence.name, "checks": checks}
(output / "result.json").write_text(json.dumps(result), encoding="utf-8")
