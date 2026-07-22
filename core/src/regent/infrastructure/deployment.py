"""Deployment providers for the release service."""

import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from regent.application.p1_ports import (
    DeploymentRequest,
    DeploymentResult,
)

_ACTIVATION_SNIPPET = """
<script>
(function(){
  var btn=document.querySelector('[data-regent-event]');
  if(!btn){return;}
  btn.addEventListener('click', function(){
    var meta=document.querySelector('meta[name="regent-deployment-id"]');
    if(!meta){return;}
    fetch('/v1/deployments/'+meta.content+'/events',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_id:'click-'+Date.now(),event_name:'activation'})
    }).catch(function(){});
  });
})();
</script>
"""


class StaticPreviewDeploymentProvider:
    """Deploy build artifacts as static previews served by the API /preview/ route.

    Extracts the build artifact (source zip) into the preview directory where
    the FastAPI static file handler can serve it. Requires a real index.html;
    never synthesizes placeholder pages.
    """

    def __init__(self, preview_root: Path, base_url: str = "") -> None:
        self._root = preview_root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._base_url = base_url.rstrip("/")
        self._deployments: dict[str, DeploymentResult] = {}

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        existing = self._deployments.get(request.idempotency_key)
        if existing is not None:
            return existing

        project_key = uuid.uuid4()
        release_key = uuid.uuid4()
        target_dir = self._root / str(project_key) / str(release_key)

        artifact_path = self._resolve_artifact(request.build_artifact_uri)
        if artifact_path is None:
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={"error": "build artifact not found or not a local file"},
            )
            self._deployments[request.idempotency_key] = result
            return result

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if zipfile.is_zipfile(artifact_path):
                with zipfile.ZipFile(artifact_path) as zf:
                    zf.extractall(target_dir)
            else:
                raise ValueError("build artifact must be a zip archive")

            index = self._locate_index(target_dir)
            if index is None:
                raise ValueError("preview requires index.html in build artifact")
            if index.parent != target_dir:
                shutil.copy2(index, target_dir / "index.html")
            index_path = target_dir / "index.html"
            html = index_path.read_text(encoding="utf-8")
            # R7 / P2-0: never synthesize interaction hooks; generated app must provide them.
            if "data-regent-event" not in html:
                raise ValueError(
                    "preview requires data-regent-event in index.html; "
                    "refusing to inject synthetic task controls"
                )
            snippet = _ACTIVATION_SNIPPET
            if "</body>" in html:
                html = html.replace("</body>", snippet + "</body>", 1)
            else:
                html += snippet
            if '<meta name="regent-deployment-id"' not in html:
                html = html.replace(
                    "<head>",
                    '<head>\n<meta name="regent-deployment-id" content="">',
                    1,
                )
            index_path.write_text(html, encoding="utf-8")

            endpoint = f"{self._base_url}/preview/{project_key}/{release_key}/"
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="SUCCEEDED",
                endpoint=endpoint,
                evidence={
                    "provider": "static-preview",
                    "project_key": str(project_key),
                    "release_key": str(release_key),
                    "artifact_hash": hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                    "runtime": "static-html",
                },
            )
        except Exception as exc:
            result = DeploymentResult(
                external_request_id=request.idempotency_key,
                status="FAILED",
                evidence={"provider": "static-preview", "error": str(exc)},
            )

        self._deployments[request.idempotency_key] = result
        return result

    async def query(self, external_request_id: str) -> DeploymentResult:
        if external_request_id not in self._deployments:
            raise LookupError("unknown deployment")
        return self._deployments[external_request_id]

    async def rollback(self, external_request_id: str, correlation_id: str) -> DeploymentResult:
        if external_request_id not in self._deployments:
            raise LookupError("unknown deployment")
        original = self._deployments[external_request_id]
        evidence = original.evidence or {}
        project_key = evidence.get("project_key")
        release_key = evidence.get("release_key")
        if project_key and release_key:
            target_dir = self._root / project_key / release_key
            if target_dir.is_dir():
                shutil.rmtree(target_dir, ignore_errors=True)
        return DeploymentResult(
            external_request_id=external_request_id,
            status="SUCCEEDED",
            endpoint=original.endpoint,
            evidence={
                "provider": "static-preview",
                "rolled_back": True,
                "correlation_id": correlation_id,
            },
        )

    @staticmethod
    def _locate_index(target_dir: Path) -> Path | None:
        for candidate in (
            target_dir / "index.html",
            target_dir / "src" / "index.html",
            target_dir / "static" / "index.html",
            target_dir / "app" / "index.html",
        ):
            if candidate.is_file():
                return candidate
        return None

    @staticmethod
    def _resolve_artifact(uri: str) -> Path | None:
        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return None
        raw = unquote(parsed.path)
        if len(raw) > 2 and raw[2] == ":":
            raw = raw[1:]
        path = Path(raw).resolve()
        if not path.is_file() or path.is_symlink():
            return None
        return path


class InMemoryDeploymentProvider:
    """In-memory stub for testing only."""

    def __init__(self) -> None:
        self.results: dict[str, DeploymentResult] = {}
        self.requests: list[DeploymentRequest] = []

    async def deploy(self, request: DeploymentRequest) -> DeploymentResult:
        self.requests.append(request)
        existing = self.results.get(request.idempotency_key)
        if existing is not None:
            return existing
        result = DeploymentResult(
            external_request_id=request.idempotency_key,
            status="SUCCEEDED",
            endpoint=f"https://preview.invalid/{request.idempotency_key}",
            evidence={"provider": "in-memory"},
        )
        self.results[request.idempotency_key] = result
        return result

    async def query(self, external_request_id: str) -> DeploymentResult:
        try:
            return self.results[external_request_id]
        except KeyError as exc:
            raise LookupError("unknown deployment") from exc

    async def rollback(self, external_request_id: str, correlation_id: str) -> DeploymentResult:
        if external_request_id not in self.results:
            raise LookupError("unknown deployment")
        return DeploymentResult(
            external_request_id=external_request_id,
            status="SUCCEEDED",
            endpoint=f"https://preview.invalid/{external_request_id}",
            evidence={
                "provider": "in-memory",
                "rolled_back": True,
                "correlation_id": correlation_id,
            },
        )
