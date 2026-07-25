"""Tests for GoalAnchorService — 目标锚点机制."""
import pytest

from regent.application.goal_anchor_service import (
    build_goal_anchored_prompt,
    extract_goal_keywords,
    validate_goal_alignment,
)


class TestExtractGoalKeywords:
    def test_english_goal(self):
        keywords = extract_goal_keywords("Show current timestamp in a web page")
        assert "show" in keywords
        assert "current" in keywords
        assert "timestamp" in keywords
        assert "web" in keywords
        assert "page" in keywords
        # Stop words filtered
        assert "a" not in keywords
        assert "in" not in keywords

    def test_chinese_goal(self):
        keywords = extract_goal_keywords("\u505a\u4e00\u4e2aAI\u4ece\u4e1a\u8005\u793e\u533aAPP\uff0c\u80fd\u591f\u53d1\u73b0\u6700\u65b0AI\u4e1a\u754c\u52a8\u6001")
        assert "ai" in keywords
        # Chinese bi-grams extracted
        assert "\u793e\u533a" in keywords  # 社区
        assert "\u53d1\u73b0" in keywords  # 发现
        assert "\u4e1a\u754c" in keywords  # 业界

    def test_empty_goal(self):
        assert extract_goal_keywords("") == []

    def test_deduplication(self):
        keywords = extract_goal_keywords("news news news digest digest")
        assert keywords.count("news") == 1
        assert keywords.count("digest") == 1


class TestBuildGoalAnchoredPrompt:
    def test_includes_goal_text(self):
        result = build_goal_anchored_prompt(
            "base prompt content",
            goal_text="Show current timestamp",
        )
        assert "Show current timestamp" in result
        assert "GOAL ANCHOR" in result
        assert "PRIMARY OBJECTIVE" in result

    def test_includes_success_criteria(self):
        result = build_goal_anchored_prompt(
            "base",
            goal_text="test",
            success_criteria={"shows_time": True, "updates": True},
        )
        assert "shows_time" in result
        assert "SUCCESS CRITERIA" in result

    def test_includes_first_deliverable(self):
        result = build_goal_anchored_prompt(
            "base",
            goal_text="test",
            first_deliverable="A live timestamp page",
        )
        assert "A live timestamp page" in result
        assert "FIRST DELIVERABLE" in result

    def test_includes_retry_context(self):
        result = build_goal_anchored_prompt(
            "base",
            goal_text="test",
            retry_context="Previous attempt failed: missing CSS",
        )
        assert "RETRY" in result
        assert "missing CSS" in result

    def test_no_retry_context_when_empty(self):
        result = build_goal_anchored_prompt("base", goal_text="test")
        assert "RETRY" not in result


class TestValidateGoalAlignment:
    def test_aligned_timestamp_page(self):
        html = """
        <html><head><title>Live Clock</title></head>
        <body><main>
        <h1>Current Timestamp</h1>
        <p id="clock">2026-07-25 12:00:00</p>
        <script>setInterval(()=>{document.getElementById('clock').textContent=new Date().toLocaleString()},1000)</script>
        </main></body></html>
        """
        result = validate_goal_alignment(
            html,
            "Show current timestamp in a web page",
            success_criteria={"shows_current_time": True},
            first_deliverable="A single HTML file with embedded JavaScript that shows a live timestamp",
        )
        # Should be aligned (keywords match, above threshold)
        assert result.aligned is True
        assert result.score >= 0.1
        assert "goal keywords" in result.details[0]

    def test_misaligned_idea_validator(self):
        html = """
        <html><head><title>Idea Validator – Product Validation Card</title></head>
        <body><main>
        <h1>Idea Validator</h1>
        <form id="idea-form">
        <textarea name="productIdea" placeholder="Describe your product idea..."></textarea>
        <input type="text" name="targetUsers" placeholder="e.g., freelancers">
        <button type="submit">Generate Validation Card</button>
        </form>
        </main></body></html>
        """
        result = validate_goal_alignment(
            html,
            "Show current timestamp in a web page",
            success_criteria={"shows_current_time": True},
            first_deliverable="A single HTML file that shows a live timestamp updating every second",
        )
        assert result.aligned is False
        assert result.score < 0.25

    def test_misaligned_japanese_chatbot(self):
        html = """
        <html><head><title>AIアシスタント</title></head>
        <body><main>
        <h1>AIアシスタント</h1>
        <p class="subtitle">FAQにお答えします</p>
        <div class="chat-container">
        <div class="message bot"><div class="bubble">こんにちは！</div></div>
        </div>
        <form id="chatForm">
        <input type="text" id="userInput" placeholder="質問を入力...">
        <button type="submit">送信</button>
        </form>
        </main></body></html>
        """
        result = validate_goal_alignment(
            html,
            "Show current timestamp in a web page",
        )
        assert result.aligned is False

    def test_aligned_news_digest(self):
        html = """
        <html><head><title>Tech News Digest</title></head>
        <body><main>
        <h1>Today's Tech News</h1>
        <article><a href="https://techcrunch.com/news1">AI Breakthrough</a><p>Summary of AI news</p></article>
        <article><a href="https://techcrunch.com/news2">Startup Funding</a><p>Summary of funding</p></article>
        <article><a href="https://techcrunch.com/news3">Product Launch</a><p>Summary of launch</p></article>
        </main></body></html>
        """
        result = validate_goal_alignment(
            html,
            "Build a tech news digest showing latest headlines from TechCrunch",
            first_deliverable="A news digest page showing TechCrunch headlines",
        )
        assert result.aligned is True

    def test_chinese_goal_chinese_html(self):
        # Use Chinese text with bi-gram matching
        goal = "\u505a\u4e00\u4e2aAI\u4ece\u4e1a\u8005\u793e\u533aAPP\uff0c\u80fd\u591f\u53d1\u73b0\u6700\u65b0AI\u4e1a\u754c\u52a8\u6001"
        html = """
        <html><head><title>AI\u4ece\u4e1a\u8005\u793e\u533a</title></head>
        <body><main>
        <h1>AI\u4ece\u4e1a\u8005\u793e\u533a\u52a8\u6001</h1>
        <article><a href="https://example.com/1">\u6700\u65b0AI\u4e1a\u754c\u52a8\u6001</a></article>
        <article><a href="https://example.com/2">AI\u4ece\u4e1a\u8005\u53d1\u73b0\u65b0\u95fb</a></article>
        </main></body></html>
        """
        result = validate_goal_alignment(html, goal)
        assert result.aligned is True

    def test_empty_goal_returns_neutral(self):
        html = "<html><body><p>Hello</p></body></html>"
        result = validate_goal_alignment(html, "")
        # Empty goal → neutral score
        assert result.score >= 0.0

    def test_details_populated(self):
        result = validate_goal_alignment(
            "<html><body>test</body></html>",
            "Show current timestamp",
        )
        assert len(result.details) > 0
