from pathlib import Path

from regent.infrastructure.deployment import stamp_preview_deployment_id


def test_stamp_preview_deployment_id_rewrites_meta(tmp_path: Path) -> None:
    root = tmp_path / "previews"
    project = "11111111-1111-1111-1111-111111111111"
    release = "22222222-2222-2222-2222-222222222222"
    target = root / project / release
    target.mkdir(parents=True)
    (target / "index.html").write_text(
        "<!DOCTYPE html><html><head>"
        '<meta name="regent-deployment-id" content="">'
        "</head><body>"
        '<button data-regent-event="activation">Go</button>'
        "</body></html>",
        encoding="utf-8",
    )
    stamp_preview_deployment_id(
        root,
        project_key=project,
        release_key=release,
        deployment_id="33333333-3333-3333-3333-333333333333",
    )
    html = (target / "index.html").read_text(encoding="utf-8")
    assert 'content="33333333-3333-3333-3333-333333333333"' in html
    assert (target / "regent-preview.js").is_file()
