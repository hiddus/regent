"""Deployment providers for the release service."""

import hashlib
import shutil
import uuid
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from regent.application.auto_fix_service import AutoFixService
from regent.application.delivery_rejection import DeliveryRejection
from regent.application.delivery_review_service import review_files_for_delivery
from regent.application.p1_ports import (
    DeploymentRequest,
    DeploymentResult,
)

_ACTIVATION_JS = """
(function(){
  var btn=document.querySelector('[data-regent-event]');
  if(!btn){return;}
  btn.addEventListener('click', function(){
    var meta=document.querySelector('meta[name="regent-deployment-id"]');
    var q=new URLSearchParams(location.search).get('deployment_id');
    var id=(meta && meta.content) || q || '';
    if(!id){
      document.documentElement.setAttribute('data-regent-obs','missing-id');
      return;
    }
    fetch('/v1/deployments/'+id+'/events',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({event_id:'click-'+Date.now(),event_name:'activation'})
    }).then(function(r){
      document.documentElement.setAttribute('data-regent-obs', r.ok ? 'ok' : 'err');
    }).catch(function(){
      document.documentElement.setAttribute('data-regent-obs','err');
    });
  });
})();
"""

_ACTIVATION_SCRIPT_TAG = '<script src="./regent-preview.js"></script>\n'

# Unrendered server/template engines must never be published as static-html.
_UNRENDERED_TEMPLATE_MARKERS = ("{{", "{%", "{#")


def html_has_unrendered_template_markers(html: str) -> bool:
    """True when HTML still contains Jinja/Mustache-style template markers."""
    return any(marker in html for marker in _UNRENDERED_TEMPLATE_MARKERS)


_TEXT_SUFFIXES = {
    ".py",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
}


def _collect_text_files(root: Path, *, max_files: int = 80, max_bytes: int = 200_000) -> dict[str, str]:
    """Collect relative text files from a preview/workspace tree for delivery review."""
    files: dict[str, str] = {}
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name.lower() != "requirements.txt":
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".") or "/." in relative:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) > max_bytes:
            continue
        try:
            files[relative] = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(files) >= max_files:
            break
    # Normalize common entrypoints to basename keys expected by review_files.
    if "index.html" not in files:
        for key, content in list(files.items()):
            if key.endswith("/index.html") or key.endswith("\\index.html"):
                files["index.html"] = content
                break
    return files


def stamp_preview_deployment_id(
    preview_root: Path,
    *,
    project_key: str,
    release_key: str,
    deployment_id: str,
) -> None:
    """Write the real deployment UUID into published preview HTML for browser events."""
    index_path = (preview_root / project_key / release_key / "index.html").resolve()
    root = preview_root.resolve()
    if root not in index_path.parents or not index_path.is_file():
        raise ValueError("preview index.html not found for stamping")
    html = index_path.read_text(encoding="utf-8")
    marker = '<meta name="regent-deployment-id" content="'
    if marker in html:
        start = html.index(marker) + len(marker)
        end = html.index('"', start)
        html = html[:start] + deployment_id + html[end:]
    else:
        html = html.replace(
            "<head>",
            f'<head>\n<meta name="regent-deployment-id" content="{deployment_id}">',
            1,
        )
    index_path.write_text(html, encoding="utf-8")
    (index_path.parent / "regent-preview.js").write_text(_ACTIVATION_JS, encoding="utf-8")
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
            # Fail closed: static-html must be fully rendered (no Jinja/Mustache left).
            if html_has_unrendered_template_markers(html):
                raise ValueError(
                    "static-html preview rejected: index.html contains unrendered "
                    "template markers ({{, {%, or {#); refuse to publish blank/raw UI"
                )
            # R7 / P2-0: never synthesize interaction hooks; generated app must provide them.
            if "data-regent-event" not in html:
                raise ValueError(
                    "preview requires data-regent-event in index.html; "
                    "refusing to inject synthetic task controls"
                )
            # P0-4: review full project tree (not HTML-only) so pure-static backends fail.
            project_files = _collect_text_files(target_dir)
            review = review_files_for_delivery(
                project_files,
                acceptance_contract=request.acceptance_contract,
                success_criteria=request.success_criteria,
            )
            fix_result = None
            if not review.passed:
                # GAC-D6: Core auto-fix only mutates HTML; skip for structural gaps.
                failed_checks = [c for c in review.checks if not c.passed]
                from regent.config import get_settings
                from regent.application.delivery_success_policy import (
                    is_blocking_delivery_gap_code,
                )

                gates_mode = str(
                    getattr(get_settings(), "delivery_product_gates_mode", "soft") or "soft"
                ).lower()
                if gates_mode in {"soft", "off"}:
                    failed_checks = [
                        c for c in failed_checks if is_blocking_delivery_gap_code(c.name)
                    ]
                structural = {
                    "forbid-pure-static-backend",
                    "forbid-trivial-server",
                    "forbid-placeholder-content",
                    "forbid-unrendered-templates",
                    "require-dependencies-declared",
                    "min-file-count",
                }
                html_only_gaps = bool(failed_checks) and all(
                    c.name not in structural for c in failed_checks
                )
                if html_only_gaps:
                    auto_fix = AutoFixService()
                    fix_result = auto_fix.fix(
                        html,
                        acceptance_contract=request.acceptance_contract,
                        success_criteria=request.success_criteria,
                        failed_checks=failed_checks,
                    )
                    if fix_result.fixed:
                        html = fix_result.html
                        project_files["index.html"] = html
                        review = review_files_for_delivery(
                            project_files,
                            acceptance_contract=request.acceptance_contract,
                            success_criteria=request.success_criteria,
                        )
                        if gates_mode in {"soft", "off"}:
                            failed_checks = [
                                c
                                for c in review.checks
                                if (not c.passed) and is_blocking_delivery_gap_code(c.name)
                            ]
                        else:
                            failed_checks = [c for c in review.checks if not c.passed]
                if failed_checks and gates_mode == "full":
                    review.raise_if_failed()
                elif failed_checks:
                    # Soft/off: only raise on remaining blocking checks.
                    from regent.application.delivery_review_service import DeliveryReviewResult

                    blocking_review = DeliveryReviewResult(
                        passed=False,
                        capability=review.capability,
                        summary=review.summary,
                        checks=failed_checks,
                    )
                    blocking_review.raise_if_failed()
                # else: soft-pass preview with non-blocking gaps
            (target_dir / "regent-preview.js").write_text(_ACTIVATION_JS, encoding="utf-8")
            if "regent-preview.js" not in html:
                if "</body>" in html:
                    html = html.replace("</body>", _ACTIVATION_SCRIPT_TAG + "</body>", 1)
                else:
                    html += _ACTIVATION_SCRIPT_TAG
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
                    "delivery_verification": {
                        "verdict": "PASS",
                        "capability": review.capability,
                        "summary": review.summary,
                    },
                    "delivery_review": {
                        "capability": review.capability,
                        "passed": True,
                        "summary": review.summary,
                        "checks": [
                            {"name": c.name, "passed": c.passed, "detail": c.detail}
                            for c in review.checks
                        ],
                    },
                    "auto_fix": {
                        "applied": fix_result is not None and fix_result.fixed,
                        "fixes": fix_result.fixes_applied if fix_result else [],
                        "attempts": fix_result.attempts if fix_result else 0,
                    },
                },
            )
        except DeliveryRejection:
            # TS §13.8.3: typed delivery rejection only — re-raise for orchestrator recovery.
            raise
        except Exception as exc:
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
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
