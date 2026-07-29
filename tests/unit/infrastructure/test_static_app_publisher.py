import uuid

import pytest
from regent.domain.errors import DomainError
from regent.infrastructure.static_app_publisher import StaticAppPublisher


def valid_files() -> dict[str, str]:
    items = "".join(
        f'<li>Deliverable headline {i} with enough readable product copy</li>'
        for i in range(1, 8)
    )
    return {
        "index.html": (
            "<html><head><title>Product Release</title>"
            "<link rel='stylesheet' href='./styles.css'></head><body>"
            "<main><h1>Product Release</h1>"
            "<p>Shippable briefing surface for real users, not a demo shell page.</p>"
            f"<section><ul>{items}</ul></section>"
            "<button data-regent-event='activate'>Start</button></main>"
            "<script src='./app.js'></script></body></html>"
        ),
        "styles.css": (
            "body { color: #111; background: #fff; } main { max-width: 60rem; margin: auto; }"
        ),
        "app.js": (
            "document.querySelector('button').addEventListener('click', "
            "() => document.body.classList.add('active'));"
        ),
    }


def test_static_preview_is_immutable_and_verified(tmp_path) -> None:
    publisher = StaticAppPublisher(tmp_path)
    project_id, release_id = uuid.uuid4(), uuid.uuid4()
    first = publisher.publish(project_id, release_id, valid_files())
    replay = publisher.publish(project_id, release_id, valid_files())
    assert first.source_hash == replay.source_hash
    assert all(item["passed"] for item in first.checks)
    assert (first.root / "index.html").is_file()


def test_static_preview_rejects_demo_shell(tmp_path) -> None:
    publisher = StaticAppPublisher(tmp_path)
    files = {
        "index.html": (
            "<html><head><title>Welcome</title>"
            "<link rel='stylesheet' href='./styles.css'></head><body>"
            "<main><h1>Welcome</h1>"
            "<button data-regent-event='activate'>Start</button></main>"
            "<script src='./app.js'></script></body></html>"
        ),
        "styles.css": "body{}",
        "app.js": "void 0;",
    }
    with pytest.raises(DomainError, match="delivery-review"):
        publisher.publish(uuid.uuid4(), uuid.uuid4(), files)


def test_static_preview_rejects_external_network_and_path_set(tmp_path) -> None:
    publisher = StaticAppPublisher(tmp_path)
    files = valid_files()
    files["app.js"] = "fetch('https://example.com/api')"
    with pytest.raises(DomainError):
        publisher.publish(uuid.uuid4(), uuid.uuid4(), files)
    files = valid_files()
    files["secret.txt"] = "not allowed"
    with pytest.raises(DomainError):
        publisher.publish(uuid.uuid4(), uuid.uuid4(), files)
