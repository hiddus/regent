import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from regent.application.p1_ports import (
    DependencyMaterializationRequest,
    DependencyMaterializationResult,
    SandboxBuildRequest,
    SandboxBuildResult,
)

CommandRunner = Callable[[list[str]], Awaitable[int]]
PermitValidator = Callable[[str, str], Awaitable[None]]


def _bounded_output(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or not path.is_file() or path.is_symlink():
        raise ValueError("provider output path escapes output root")
    return path


async def subprocess_runner(command: list[str]) -> int:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await process.wait()


class DockerSandboxDriver:
    def __init__(
        self,
        *,
        root: Path,
        image: str,
        runner: CommandRunner = subprocess_runner,
        host_path_map: dict[str, str] | None = None,
        run_as_user: str | None = None,
        require_host_path_map_in_container: bool = False,
        egress_proxy: str | None = None,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._image = image
        self._runner = runner
        self._requests: dict[str, Path] = {}
        self._host_path_map = dict(host_path_map or {})
        self._run_as_user = run_as_user or "65532:65532"
        self._require_host_path_map_in_container = require_host_path_map_in_container
        self._egress_proxy = (egress_proxy or "").strip() or None
        if self._run_as_user.startswith("0:") or self._run_as_user == "0":
            raise ValueError("agent sandbox --user must not be root (CD-6.2)")

    async def build(self, request: SandboxBuildRequest) -> SandboxBuildResult:
        operation_id = uuid.uuid4().hex
        operation = self._root / operation_id
        input_dir = operation / "input"
        output_dir = operation / "output"
        input_dir.mkdir(parents=True)
        output_dir.mkdir()
        output_dir.chmod(0o777)

        source = self._local_artifact(request.workspace_snapshot_uri)
        bundle = self._local_artifact(request.dependency_bundle_uri)
        bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
        if bundle_hash != request.dependency_bundle_hash:
            raise ValueError("dependency bundle hash mismatch")

        shutil.copy2(source, input_dir / "source.zip")
        shutil.copy2(bundle, input_dir / "dependencies.bundle")
        (input_dir / "request.json").write_text(request.model_dump_json(), encoding="utf-8")
        self._requests[operation_id] = operation

        exit_code = await self._runner(self.command(operation))
        result = output_dir / "result.json"
        if not result.is_file():
            return self._unknown(operation_id, operation, exit_code)
        body = json.loads(result.read_text(encoding="utf-8"))
        evidence = _bounded_output(operation / "output", str(body["evidence_file"]))
        artifact = (
            _bounded_output(operation / "output", str(body["build_artifact_file"]))
            if body.get("build_artifact_file")
            else None
        )
        return SandboxBuildResult(
            external_request_id=operation_id,
            status=str(body["status"]),
            evidence_artifact_uri=evidence.resolve().as_uri(),
            evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest(),
            build_artifact_uri=artifact.resolve().as_uri() if artifact else None,
            build_artifact_hash=hashlib.sha256(artifact.read_bytes()).hexdigest()
            if artifact
            else None,
            checks=list(body.get("checks", [])),
        )

    async def query(self, external_request_id: str) -> SandboxBuildResult:
        operation = self._requests.get(external_request_id)
        if operation is None:
            raise LookupError("unknown sandbox operation")
        result = operation / "output" / "result.json"
        if not result.is_file():
            return self._unknown(external_request_id, operation, None)
        body = json.loads(result.read_text(encoding="utf-8"))
        evidence = _bounded_output(operation / "output", str(body["evidence_file"]))
        artifact = (
            _bounded_output(operation / "output", str(body["build_artifact_file"]))
            if body.get("build_artifact_file")
            else None
        )
        return SandboxBuildResult(
            external_request_id=external_request_id,
            status=str(body["status"]),
            evidence_artifact_uri=evidence.resolve().as_uri(),
            evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest(),
            build_artifact_uri=artifact.resolve().as_uri() if artifact else None,
            build_artifact_hash=hashlib.sha256(artifact.read_bytes()).hexdigest()
            if artifact
            else None,
            checks=list(body.get("checks", [])),
        )

    def command(self, operation: Path) -> list[str]:
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--user",
            self._run_as_user,
            "--mount",
            f"type=bind,src={apply_host_path_map(operation / 'input', self._host_path_map)},"
            f"dst=/input,readonly",
            "--mount",
            f"type=bind,src={apply_host_path_map(operation / 'output', self._host_path_map)},"
            f"dst=/output",
            self._image,
        ]

    def workspace_exec_command(
        self,
        workspace: Path,
        shell_command: str,
        *,
        allow_network: bool = False,
    ) -> list[str]:
        """Docker argv for an agent shell command confined to ``workspace``.

        CD-6.1: always pass ``--entrypoint sh`` so build-sandbox ENTRYPOINT cannot
        swallow the command. CD-6.2/6.3: ``--user`` and host path map apply here.
        """
        if (
            self._require_host_path_map_in_container
            and running_in_container()
            and not self._host_path_map
        ):
            raise RuntimeError(
                "REGENT_HOST_PATH_MAP is required when sandbox_mode=docker "
                "runs inside a container (CD-6.3 / N-3d); refusing silent empty mounts"
            )
        mount_src = apply_host_path_map(workspace.resolve(), self._host_path_map)
        # CD-7.5 / N-4: never bare-bridge without controlled egress proxy.
        if allow_network:
            from urllib.parse import urlparse

            if (
                not self._egress_proxy
                or urlparse(self._egress_proxy).scheme not in {"http", "https"}
            ):
                raise PermissionError(
                    "agent network requires REGENT_DEPENDENCY_EGRESS_PROXY "
                    "(CD-7.5 / N-4); refusing bare bridge"
                )
        network = "bridge" if allow_network else "none"
        argv = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--user",
            self._run_as_user,
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,src={mount_src},dst=/workspace",
        ]
        if allow_network and self._egress_proxy:
            argv.extend(
                [
                    "--env",
                    f"HTTPS_PROXY={self._egress_proxy}",
                    "--env",
                    f"HTTP_PROXY={self._egress_proxy}",
                    "--env",
                    f"https_proxy={self._egress_proxy}",
                    "--env",
                    f"http_proxy={self._egress_proxy}",
                ]
            )
        argv.extend(
            [
                "--entrypoint",
                "sh",
                self._image,
                "-lc",
                shell_command,
            ]
        )
        return argv

    async def exec_in_workspace(
        self,
        workspace: Path,
        shell_command: str,
        *,
        timeout_seconds: int = 60,
        allow_network: bool = False,
    ) -> str:
        """Run a whitelisted agent command inside an isolated container."""
        timeout = max(1, min(int(timeout_seconds or 60), 120))
        argv = self.workspace_exec_command(
            workspace, shell_command, allow_network=allow_network
        )
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return f"TIMEOUT after {timeout}s: {shell_command}"
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + ("\n" + err if err else "")).strip()
        preview = combined[:8_000] + ("\n...[truncated]" if len(combined) > 8_000 else "")
        return f"exit={process.returncode}\n{preview}"

    @staticmethod
    def _local_artifact(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError("sandbox accepts local immutable artifacts only")
        raw = parsed.path[1:] if len(parsed.path) > 2 and parsed.path[2] == ":" else parsed.path
        path = Path(raw).resolve()
        if not path.is_file() or path.is_symlink():
            raise ValueError("sandbox input artifact is invalid")
        return path

    @staticmethod
    def _unknown(operation_id: str, operation: Path, exit_code: int | None) -> SandboxBuildResult:
        evidence = operation / "output" / "unknown.json"
        evidence.write_text(
            json.dumps({"status": "UNKNOWN", "exit_code": exit_code}), encoding="utf-8"
        )
        return SandboxBuildResult(
            external_request_id=operation_id,
            status="UNKNOWN",
            evidence_artifact_uri=evidence.resolve().as_uri(),
            evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest(),
        )


class LocalSandboxDriver:
    """Build verification that runs natively (no Docker-in-Docker needed).

    Mirrors the logic of capabilities/bootstrap/sandbox/main.py:
    extract source + deps, compileall, optional pytest, produce artifact.
    """

    def __init__(self, *, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._results: dict[str, SandboxBuildResult] = {}

    async def build(self, request: SandboxBuildRequest) -> SandboxBuildResult:
        operation_id = uuid.uuid4().hex
        operation = self._root / operation_id
        output_dir = operation / "output"
        output_dir.mkdir(parents=True)

        source_path = self._local_artifact(request.workspace_snapshot_uri)
        bundle_path = self._local_artifact(request.dependency_bundle_uri)
        bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        if bundle_hash != request.dependency_bundle_hash:
            raise ValueError("dependency bundle hash mismatch")

        checks: list[dict[str, Any]] = []
        status = "PASSED"
        error_msg: str | None = None

        work = Path(tempfile.mkdtemp(prefix="regent-build-"))
        try:
            # Extract source
            with zipfile.ZipFile(source_path) as archive:
                for member in archive.infolist():
                    target = (work / member.filename).resolve()
                    if work.resolve() not in target.parents:
                        raise ValueError("source archive path escape")
                archive.extractall(work)

            # Extract dependencies
            deps_dir = work / ".deps"
            with zipfile.ZipFile(bundle_path) as archive:
                for member in archive.infolist():
                    target = (deps_dir / member.filename).resolve()
                    if deps_dir.resolve() not in target.parents:
                        raise ValueError("dependency bundle path escape")
                archive.extractall(deps_dir)

            # compileall check
            compile_cmd = [sys.executable, "-m", "compileall", "-q", str(work)]
            compile_result = subprocess.run(
                compile_cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(work),
            )
            checks.append({
                "command": compile_cmd,
                "exit_code": compile_result.returncode,
                "stdout": compile_result.stdout[-4000:],
                "stderr": compile_result.stderr[-4000:],
            })
            if compile_result.returncode != 0:
                status = "FAILED"
                error_msg = "compileall failed"

            # pytest if tests exist
            has_tests = (
                (work / "tests").is_dir()
                or any(work.rglob("test_*.py"))
                or any(work.rglob("*_test.py"))
            )
            if has_tests and status == "PASSED":
                test_cmd = [sys.executable, "-m", "pytest", "-q"]
                test_result = subprocess.run(
                    test_cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    cwd=str(work),
                    env={
                        "PATH": subprocess.os.environ.get("PATH", ""),
                        "PYTHONPATH": str(work / "src") + subprocess.os.pathsep + str(work),
                        "HOME": tempfile.gettempdir(),
                    },
                )
                checks.append({
                    "command": test_cmd,
                    "exit_code": test_result.returncode,
                    "stdout": test_result.stdout[-4000:],
                    "stderr": test_result.stderr[-4000:],
                })
                # pytest exit code 5 = no tests collected
                if test_result.returncode not in (0, 5):
                    status = "FAILED"
                    error_msg = "pytest failed"

            # Produce build artifact (source zip without .deps/__pycache__)
            artifact = output_dir / "app-source.zip"
            with zipfile.ZipFile(artifact, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in sorted(
                    p for p in work.rglob("*") if p.is_file()
                    and ".deps" not in p.parts
                    and "__pycache__" not in p.parts
                ):
                    zf.write(path, path.relative_to(work).as_posix())

            # Write evidence
            evidence = output_dir / "verification.json"
            evidence_data = {"checks": checks}
            if error_msg:
                evidence_data["error"] = error_msg
            evidence.write_text(
                json.dumps(evidence_data, sort_keys=True), encoding="utf-8"
            )

            result = SandboxBuildResult(
                external_request_id=operation_id,
                status=status,
                evidence_artifact_uri=evidence.resolve().as_uri(),
                evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                build_artifact_uri=artifact.resolve().as_uri()
                if status == "PASSED"
                else None,
                build_artifact_hash=hashlib.sha256(artifact.read_bytes()).hexdigest()
                if status == "PASSED"
                else None,
                checks=checks,
            )
        except Exception as exc:
            evidence = output_dir / "verification.json"
            evidence.write_text(
                json.dumps({"checks": checks, "error": str(exc)}, sort_keys=True),
                encoding="utf-8",
            )
            result = SandboxBuildResult(
                external_request_id=operation_id,
                status="FAILED",
                evidence_artifact_uri=evidence.resolve().as_uri(),
                evidence_hash=hashlib.sha256(evidence.read_bytes()).hexdigest(),
                checks=checks,
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)

        self._results[operation_id] = result
        return result

    async def query(self, external_request_id: str) -> SandboxBuildResult:
        if external_request_id not in self._results:
            raise LookupError("unknown sandbox operation")
        return self._results[external_request_id]

    @staticmethod
    def _local_artifact(uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            raise ValueError("sandbox accepts local immutable artifacts only")
        raw = parsed.path[1:] if len(parsed.path) > 2 and parsed.path[2] == ":" else parsed.path
        path = Path(raw).resolve()
        if not path.is_file() or path.is_symlink():
            raise ValueError("sandbox input artifact is invalid")
        return path

    async def exec_in_workspace(
        self,
        workspace: Path,
        shell_command: str,
        *,
        timeout_seconds: int = 60,
        allow_network: bool = False,
    ) -> str:
        """Dev/test-only host execution. Production must use DockerSandboxDriver."""
        del allow_network  # host path has no network isolation; tests only
        timeout = max(1, min(int(timeout_seconds or 60), 120))
        process = await asyncio.create_subprocess_shell(
            shell_command,
            cwd=str(workspace.resolve()),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return f"TIMEOUT after {timeout}s: {shell_command}"
        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        combined = (out + ("\n" + err if err else "")).strip()
        preview = combined[:8_000] + ("\n...[truncated]" if len(combined) > 8_000 else "")
        return f"exit={process.returncode}\n{preview}"


def parse_host_path_map(raw: str | None) -> dict[str, str]:
    """Parse ``REGENT_HOST_PATH_MAP`` as ``container=host;container2=host2``."""
    if not raw or not str(raw).strip():
        return {}
    mapping: dict[str, str] = {}
    for part in str(raw).split(";"):
        piece = part.strip()
        if not piece:
            continue
        if "=" not in piece:
            raise ValueError(
                f"invalid REGENT_HOST_PATH_MAP entry {piece!r}; expected container=host"
            )
        left, right = piece.split("=", 1)
        container_prefix = left.strip().rstrip("/") or "/"
        host_prefix = right.strip().rstrip("/") or "/"
        mapping[container_prefix] = host_prefix
    return mapping


def docker_fs_path(path: Path) -> str:
    """POSIX path string for Docker bind mounts (Linux daemon).

    On Windows, ``Path('/var/lib/...').resolve()`` gains a drive letter; strip it
    so ``REGENT_HOST_PATH_MAP`` keys still match.
    """
    text = path.as_posix()
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        text = text[2:]
    return text


def apply_host_path_map(path: Path, path_map: dict[str, str]) -> str:
    """Translate a container-visible path to the host path Docker daemon expects."""
    text = docker_fs_path(path if path.is_absolute() else path.resolve())
    if not path_map:
        return text
    for container_prefix, host_prefix in sorted(
        path_map.items(), key=lambda item: len(item[0]), reverse=True
    ):
        if text == container_prefix or text.startswith(container_prefix + "/"):
            suffix = text[len(container_prefix) :]
            return host_prefix + suffix
    return text


def running_in_container() -> bool:
    """Best-effort detection for CD-6.3 fail-closed (N-3d)."""
    if Path("/.dockerenv").is_file():
        return True
    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.is_file():
            body = cgroup.read_text(encoding="utf-8", errors="replace")
            if "docker" in body or "containerd" in body or "kubepods" in body:
                return True
    except OSError:
        return False
    return False


def resolve_agent_sandbox_user(settings: Any) -> str:
    """CD-6.2: align sandbox uid with the process that writes workspace files."""
    override = getattr(settings, "agent_sandbox_uid", None)
    if override is not None and str(override).strip():
        user = str(override).strip()
        if user.startswith("0:") or user == "0":
            raise ValueError("REGENT_AGENT_SANDBOX_UID must not be root")
        return user if ":" in user else f"{user}:{user}"
    try:
        import os

        uid = int(os.getuid())  # type: ignore[attr-defined]
        gid = int(os.getgid())  # type: ignore[attr-defined]
    except AttributeError:
        # Windows / non-POSIX: keep historical non-root default
        return "65532:65532"
    if uid == 0:
        raise ValueError(
            "refusing to run agent sandbox as root; set REGENT_AGENT_SANDBOX_UID "
            "to a non-root uid:gid (CD-6.2)"
        )
    return f"{uid}:{gid}"


def build_agent_sandbox(*, settings: Any | None = None) -> DockerSandboxDriver | LocalSandboxDriver:
    """Construct the sandbox used for agent tool command execution.

    Production always uses Docker. ``local`` is allowed only outside production.
    CD-6.1 uses ``agent_sandbox_image`` (not the build sandbox image).
    """
    from regent.config import get_settings

    cfg = settings or get_settings()
    mode = str(getattr(cfg, "sandbox_mode", "local"))
    env = str(getattr(cfg, "environment", "development"))
    if env == "production" and mode != "docker":
        raise RuntimeError(
            "REGENT_SANDBOX_MODE=local is forbidden in production "
            "(Tech-Spec §13.8 / CD-0.1)"
        )
    root = Path(str(getattr(cfg, "build_root", "/var/lib/regent/builds"))) / "agent-sandbox"
    if mode == "docker":
        path_map = parse_host_path_map(getattr(cfg, "host_path_map", None))
        return DockerSandboxDriver(
            root=root,
            image=str(
                getattr(cfg, "agent_sandbox_image", None)
                or getattr(cfg, "sandbox_image", "regent-agent-exec-v1:1")
            ),
            host_path_map=path_map,
            run_as_user=resolve_agent_sandbox_user(cfg),
            require_host_path_map_in_container=True,
            egress_proxy=getattr(cfg, "dependency_egress_proxy", None),
        )
    return LocalSandboxDriver(root=root)


class DockerDependencyMaterializer:
    def __init__(
        self,
        *,
        root: Path,
        image: str,
        egress_proxy: str | None,
        permit_validator: PermitValidator,
        runner: CommandRunner = subprocess_runner,
        host_path_map: dict[str, str] | None = None,
        run_as_user: str | None = None,
    ) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._image = image
        self._proxy = egress_proxy
        self._permit_validator = permit_validator
        self._runner = runner
        self._host_path_map = dict(host_path_map or {})
        self._run_as_user = run_as_user or "65532:65532"

    async def materialize(
        self, request: DependencyMaterializationRequest
    ) -> DependencyMaterializationResult:
        # If no dependencies to resolve, return a valid empty zip bundle.
        if not request.dependency_intents:
            import zipfile

            operation = self._root / f"deps-{uuid.uuid4().hex}"
            output = operation / "output"
            output.mkdir(parents=True, exist_ok=True)
            lockfile = output / "requirements.lock.json"
            lockfile.write_text("[]", encoding="utf-8")
            sbom = output / "sbom.cdx.json"
            sbom.write_text(
                '{"bomFormat":"CycloneDX","specVersion":"1.5","components":[]}',
                encoding="utf-8",
            )
            bundle = output / "dependencies.zip"
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.write(lockfile, "requirements.lock.json")
                archive.write(sbom, "sbom.cdx.json")
            return DependencyMaterializationResult(
                status="MATERIALIZED",
                lockfile_uri=lockfile.resolve().as_uri(),
                bundle_uri=bundle.resolve().as_uri(),
                bundle_hash=hashlib.sha256(bundle.read_bytes()).hexdigest(),
                sbom_uri=sbom.resolve().as_uri(),
                evidence={"mode": "no-dependencies", "reason": "dependency_intents is empty"},
            )
        if not self._proxy or urlparse(self._proxy).scheme not in {"http", "https"}:
            raise PermissionError("dependency egress proxy is not configured")
        await self._permit_validator(request.permit_id, "dependency-materialize")
        operation = self._root / f"deps-{uuid.uuid4().hex}"
        output = operation / "output"
        output.mkdir(parents=True)
        output.chmod(0o777)
        (operation / "request.json").write_text(request.model_dump_json(), encoding="utf-8")
        command = [
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            "512m",
            "--cpus",
            "1",
            "--user",
            self._run_as_user,
            "--env",
            f"HTTPS_PROXY={self._proxy}",
            "--env",
            f"HTTP_PROXY={self._proxy}",
            "--mount",
            f"type=bind,src={apply_host_path_map(operation / 'request.json', self._host_path_map)},"
            f"dst=/input/request.json,readonly",
            "--mount",
            f"type=bind,src={apply_host_path_map(output, self._host_path_map)},dst=/output",
            self._image,
        ]
        exit_code = await self._runner(command)
        result = output / "result.json"
        if not result.is_file():
            return DependencyMaterializationResult(
                status="UNKNOWN", evidence={"exit_code": exit_code}, failure_code="UNKNOWN_RESULT"
            )
        body: dict[str, Any] = json.loads(result.read_text(encoding="utf-8"))

        def uri(name: str) -> str:
            return _bounded_output(output, str(body[name])).as_uri()

        bundle = _bounded_output(output, str(body["bundle_file"]))
        return DependencyMaterializationResult(
            status=str(body["status"]),
            lockfile_uri=uri("lockfile"),
            bundle_uri=bundle.resolve().as_uri(),
            bundle_hash=hashlib.sha256(bundle.read_bytes()).hexdigest(),
            sbom_uri=uri("sbom"),
            evidence=dict(body.get("evidence", {})),
            failure_code=body.get("failure_code"),
        )
