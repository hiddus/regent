from regent.infrastructure.html_evidence import inject_observed_entries, render_observed_articles


def test_inject_replaces_placeholder() -> None:
    html = "<html><body>__ARTICLES_HTML__</body></html>"
    entries = [
        {
            "title": "Observed Headline One",
            "link": "https://techcrunch.com/a",
            "summary": "Summary",
            "source_uri": "https://techcrunch.com/feed/",
        }
    ]
    out = inject_observed_entries(html, entries)
    assert "__ARTICLES_HTML__" not in out
    assert "Observed Headline One" in out
    assert "https://techcrunch.com/a" in out


def test_inject_appends_when_no_marker() -> None:
    html = "<html><body><h1>Digest</h1></body></html>"
    out = inject_observed_entries(
        html,
        [{"title": "HN Item", "link": "https://hnrss.org/x", "summary": "", "source_uri": "hn"}],
    )
    assert "regent-observed-entries" in out
    assert "HN Item" in out


def test_render_escapes_html() -> None:
    rendered = render_observed_articles(
        [{"title": "<script>x</script>", "link": "http://x", "summary": "a&b", "source_uri": "s"}]
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "a&amp;b" in rendered
