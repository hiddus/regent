import io
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from regent.api.preview_security import PREVIEW_CONTENT_SECURITY_POLICY
from regent.application.p1_ports import DeploymentRequest, DeploymentResult
from regent.infrastructure.deployment import (
    InMemoryDeploymentProvider,
    StaticPreviewDeploymentProvider,
    html_has_unrendered_template_markers,
)
from regent.infrastructure.runtime_preview import _artifact_looks_static


def test_succeeded_deployment_requires_endpoint() -> None:
    with pytest.raises(ValidationError):
        DeploymentResult(external_request_id="id", status="SUCCEEDED")


@pytest.mark.asyncio
async def test_fake_preview_provider_is_idempotent_and_queryable() -> None:
    provider = InMemoryDeploymentProvider()
    request = DeploymentRequest(
        build_artifact_uri="artifact://build",
        environment="preview",
        idempotency_key="deploy-1",
        correlation_id="corr",
    )
    first = await provider.deploy(request)
    second = await provider.deploy(request)
    assert first == second
    assert await provider.query(first.external_request_id) == first
    rolled_back = await provider.rollback(first.external_request_id, "corr")
    assert rolled_back.evidence["rolled_back"] is True


def test_html_has_unrendered_template_markers() -> None:
    assert html_has_unrendered_template_markers("<p>{{ title }}</p>") is True
    assert html_has_unrendered_template_markers("{% extends 'base.html' %}") is True
    assert html_has_unrendered_template_markers("{# comment #}") is True
    assert html_has_unrendered_template_markers("<p>Hello world</p>") is False


def _zip_with_index(tmp_path: Path, html: str) -> Path:
    artifact = tmp_path / "artifact.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", html)
    artifact.write_bytes(buf.getvalue())
    return artifact


@pytest.mark.asyncio
async def test_static_preview_rejects_unrendered_jinja_in_index(tmp_path: Path) -> None:
    html = """
    <html><head><title>{{ page_title }}</title>
    <style>body { color: red; }</style></head>
    <body><main>
      <h1>{% block title %}Digest{% endblock %}</h1>
      <button data-regent-event="activation">Go</button>
    </main></body></html>
    """
    artifact = _zip_with_index(tmp_path, html)
    provider = StaticPreviewDeploymentProvider(tmp_path / "previews", base_url="")
    result = await provider.deploy(
        DeploymentRequest(
            build_artifact_uri=artifact.resolve().as_uri(),
            environment="preview",
            idempotency_key="jinja-reject-1",
            correlation_id="corr",
        )
    )
    assert result.status == "FAILED"
    assert result.endpoint is None
    error = (result.evidence or {}).get("error", "")
    assert "unrendered" in error.lower() or "template markers" in error.lower()
    # Fail-closed: do not leave a servable preview tree for a rejected deploy.
    assert list((tmp_path / "previews").rglob("index.html")) == []


def test_preview_csp_allows_inline_styles() -> None:
    assert "style-src 'self' 'unsafe-inline'" in PREVIEW_CONTENT_SECURITY_POLICY
    assert "script-src 'self'" in PREVIEW_CONTENT_SECURITY_POLICY
    assert "script-src 'self' 'unsafe-inline'" not in PREVIEW_CONTENT_SECURITY_POLICY
    # Runtime Preview injects <base href="/preview/runtime/.../">; CSP must allow it.
    assert "base-uri 'self'" in PREVIEW_CONTENT_SECURITY_POLICY
    assert "base-uri 'none'" not in PREVIEW_CONTENT_SECURITY_POLICY


def _zip_entries(tmp_path: Path, entries: dict[str, str]) -> Path:
    artifact = tmp_path / "artifact.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    artifact.write_bytes(buf.getvalue())
    return artifact


def test_artifact_looks_static_detects_shape(tmp_path: Path) -> None:
    static = _zip_entries(
        tmp_path, {"index.html": "<html></html>", "static/app.js": "1"}
    )
    assert _artifact_looks_static(static.resolve().as_uri()) is True
    runtime = _zip_entries(
        tmp_path,
        {"index.html": "<html></html>", "src/app.py": "app = None"},
    )
    assert _artifact_looks_static(runtime.resolve().as_uri()) is False
    assert _artifact_looks_static(None) is False


@pytest.mark.asyncio
async def test_static_preview_publishes_genuine_site_without_event_hooks(
    tmp_path: Path,
) -> None:
    """Defect #11: soft gates must not block static sites lacking telemetry hooks."""
    css_body = "x" * 900
    html = (
        "<html><head><title>青溪镇</title>"
        "<style>body{font-family:sans-serif}main{max-width:960px;margin:0 auto}"
        + css_body
        + "</style></head><body><main>"
        "<h1>青溪镇 · AI 虚拟小镇</h1>"
        "<section><p>居民按照昼夜节律自主生活，地图实时展示位置与对话。</p>"
        "<p>每个居民拥有独立人设、职业与作息，深夜无人出门。</p></section>"
        "</main></body></html>"
    )
    artifact = _zip_with_index(tmp_path, html)
    provider = StaticPreviewDeploymentProvider(tmp_path / "previews", base_url="")
    result = await provider.deploy(
        DeploymentRequest(
            build_artifact_uri=artifact.resolve().as_uri(),
            environment="preview",
            idempotency_key="static-no-hooks-1",
            correlation_id="corr",
        )
    )
    assert result.status == "SUCCEEDED", result.evidence
    assert result.endpoint
