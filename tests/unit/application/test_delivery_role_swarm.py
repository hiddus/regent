"""Delivery Role Agents + Swarm — Product/Tech/Test/UX/Ops hard gates."""

from __future__ import annotations

import httpx
import pytest

from regent.application.delivery_framework_fix import framework_fix_plan
from regent.application.delivery_role_agents import (
    APP_PROJECT_ROLES,
    DELIVERY_ROLE_AGENTS,
    delivery_role_catalog,
    select_roles_for_goal,
)
from regent.application.delivery_role_swarm import run_delivery_role_swarm

_SUBSTANTIAL_CSS = """
:root { --bg:#0f1419; --text:#e7ecf3; --accent:#3d8bfd; }
body { margin:0; color:var(--text); background:var(--bg);
  font-family:"IBM Plex Sans","Source Han Sans SC",sans-serif; }
.container { max-width:960px; margin:0 auto; padding:24px 16px; }
.card { display:flex; gap:12px; padding:16px; }
.card:hover { background:#1a2332; }
main { display:grid; gap:16px; }
""" + ("/* pad */\n" * 40)


def _rich_point() -> dict:
    return {
        "title": "Consent Obligation / 同意义务",
        "statute": "PDPA §13-17",
        "source": "https://www.pdpc.gov.sg/overview-of-pdpa/the-data-protection-obligations",
        "obligations": (
            "在收集、使用或披露个人数据前取得有效同意；同意须知情、自愿、明确；"
            "提供简便的撤回同意机制，并在撤回后停止相应处理，法律另有规定除外。"
            "记录同意版本、时间与渠道，供审计抽查。"
        ),
        "scenario": (
            "组织在营销获客、账号开通或第三方共享场景中收集、使用或披露个人数据，"
            "需要证明同意基础与撤回路径可执行。"
        ),
        "risk": (
            "未经同意处理数据，PDPC 可处以最高 S$1M 或年营业额 10% 的罚款，"
            "并责令停止处理与整改公示。"
        ),
        "priority": "high",
    }


def _thin_point() -> dict:
    return {
        "title": "Consent",
        "statute": "PDPA",
        "source": "https://example.test/pdpa",
        "obligations": "取得同意。",
        "scenario": "收集数据",
        "risk": "可能被罚",
        "priority": "high",
    }


def _rich_step() -> dict:
    return {
        "trigger": "准备将美国消费者个人数据跨境传输至新加坡处理方开展业务运营",
        "action": (
            "对照 CCPA 知情权与 PDPA 同意义务，完成转移影响评估，"
            "并在合同中固化目的限制与撤回路径"
        ),
        "check": "确认目的限制与撤回路径已在接收方数据处理协议中落地并可抽查",
        "evidence": "转移影响评估报告全文与双方签署的数据处理协议签字页扫描件",
        "owner": "DPO",
        "priority": "P1",
    }


def test_delivery_role_catalog_defines_five_agents() -> None:
    cat = delivery_role_catalog(goal_input="Crosswalk 合规预览应用")
    assert cat["schema"] == "regent-delivery-role-agents/v2"
    assert cat["self_supplement"] is True
    ids = {a.role_id for a in DELIVERY_ROLE_AGENTS}
    assert ids == {"product", "tech", "test", "ux", "ops"}
    assert cat["selected_roles"] == list(APP_PROJECT_ROLES)
    assert "delivery-roles-v1" in cat["certified_hive_gap"]


def test_select_roles_ops_only_goal() -> None:
    roles = select_roles_for_goal("host heal disk preview-venv kswapd 运维自愈")
    assert roles == ["ops", "tech"]


def test_framework_fix_plan_has_owners() -> None:
    plan = framework_fix_plan(goal_input="Crosswalk app preview")
    assert plan["schema"] == "delivery-framework-fix/v1"
    owners = {p["owner"] for p in plan["phases"]}
    assert {"product", "tech", "test", "ux", "ops"} <= owners
    assert plan["selected_roles"] == list(APP_PROJECT_ROLES)


def _handler_outline(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    prefix = "http://example.test/preview/runtime/outline"
    if url.rstrip("/") == prefix or url == prefix + "/":
        home = """<!DOCTYPE html><html><head>
<link rel="stylesheet" href="/preview/runtime/outline/static/style.css">
</head><body><main>
<h1>Crosswalk 合规</h1>
<p>PDPA / CCPA outline shell via /api/countries</p>
<a href="/preview/runtime/outline/countries">Countries</a>
</main></body></html>"""
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"})
    if url.endswith("/api/countries"):
        return httpx.Response(
            200,
            json=[
                {"country_code": "SG", "points": [_thin_point() for _ in range(10)]},
                {"country_code": "US", "points": [_thin_point() for _ in range(10)]},
            ],
        )
    if "/api/crosswalks/" in url:
        return httpx.Response(
            200,
            json={
                "steps": [
                    {
                        "trigger": "transfer",
                        "action": "map",
                        "check": "ok",
                        "evidence": "doc",
                        "owner": "DPO",
                        "priority": "P1",
                    }
                    for _ in range(10)
                ]
            },
        )
    if url.endswith("/countries"):
        return httpx.Response(
            200,
            text="<html><body><main>" + ("国家目录详情 " * 40) + "</main></body></html>",
            headers={"content-type": "text/html"},
        )
    if "/crosswalks/" in url:
        return httpx.Response(
            200,
            text="<html><body><main>" + ("适配指南 " * 20) + "</main></body></html>",
            headers={"content-type": "text/html"},
        )
    return httpx.Response(404, text="missing")


def _handler_rich(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    prefix = "http://example.test/preview/runtime/rich"
    if url.rstrip("/") == prefix or url == prefix + "/":
        home = (
            "<!DOCTYPE html><html><head>"
            '<link rel="stylesheet" href="/preview/runtime/rich/static/style.css">'
            "</head><body><main>"
            "<h1>Crosswalk · 跨境数据合规适配操作说明书</h1>"
            "<p>" + ("持续更新的合规内容收集汇总平台。从本国数据合规适配到目标国合规。" * 12) + "</p>"
            '<a href="/preview/runtime/rich/countries">国家目录</a>'
            '<a href="/preview/runtime/rich/crosswalks/US-SG">US→SG</a>'
            "<p>本手册要求义务、场景、风险与证据均可执行，禁止仅有大纲条目。</p>"
            "</main></body></html>"
        )
        return httpx.Response(200, text=home, headers={"content-type": "text/html"})
    if url.endswith("/static/style.css"):
        return httpx.Response(200, text=_SUBSTANTIAL_CSS, headers={"content-type": "text/css"})
    if url.endswith("/api/countries"):
        return httpx.Response(
            200,
            json=[
                {"country_code": "SG", "points": [_rich_point() for _ in range(10)]},
                {"country_code": "US", "points": [_rich_point() for _ in range(10)]},
            ],
        )
    if "/api/crosswalks/" in url:
        return httpx.Response(200, json={"steps": [_rich_step() for _ in range(10)]})
    if url.endswith("/countries/SG") or url.endswith("/countries/US"):
        titles = " ".join(
            (_rich_point()["title"].split("/")[0].strip()) for _ in range(10)
        )
        body = (
            "<html><body><main><h1>Country handbook</h1><p>"
            + titles
            + (" 操作细则与法源引用 " * 40)
            + "</p></main></body></html>"
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})
    if url.endswith("/countries"):
        return httpx.Response(
            200,
            text="<html><body><main>" + ("国家目录详情文字 " * 50) + "</main></body></html>",
            headers={"content-type": "text/html"},
        )
    if "/crosswalks/" in url:
        return httpx.Response(
            200,
            text="<html><body><main>" + ("US→SG 适配操作步骤与证据要求 " * 40) + "</main></body></html>",
            headers={"content-type": "text/html"},
        )
    return httpx.Response(404, text="missing")


@pytest.mark.asyncio
async def test_swarm_rejects_outline_thin_catalog() -> None:
    transport = httpx.MockTransport(_handler_outline)
    live_qa = {
        "passed": True,
        "checks": [
            {"name": "preview-content-depth", "passed": True, "detail": "count-only"},
            {"name": "stylesheet-substance", "passed": True, "detail": "ok"},
            {"name": "styled-surface", "passed": True, "detail": "ok"},
            {"name": "semantic-main", "passed": True, "detail": "ok"},
            {"name": "preview-internal-nav", "passed": True, "detail": "ok"},
            {"name": "stylesheet-present", "passed": True, "detail": "ok"},
        ],
    }
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_delivery_role_swarm(
            "http://example.test/preview/runtime/outline/",
            live_qa=live_qa,
            goal_input="Crosswalk PDPA CCPA",
            client=client,
        )
    assert result.accepted is False
    by_role = {r.role_id: r for r in result.roles}
    assert by_role["product"].accepted is False
    assert "delivery-product-outline" in by_role["product"].gaps
    assert set(by_role) == {"product", "tech", "test", "ux", "ops"}


@pytest.mark.asyncio
async def test_swarm_accepts_rich_catalog() -> None:
    transport = httpx.MockTransport(_handler_rich)
    live_qa = {
        "passed": True,
        "checks": [
            {"name": "preview-content-depth", "passed": True, "detail": "ok"},
            {"name": "stylesheet-substance", "passed": True, "detail": "ok"},
            {"name": "styled-surface", "passed": True, "detail": "ok"},
            {"name": "semantic-main", "passed": True, "detail": "ok"},
            {"name": "preview-internal-nav", "passed": True, "detail": "ok"},
            {"name": "stylesheet-present", "passed": True, "detail": "ok"},
            {"name": "preview-home-reachable", "passed": True, "detail": "ok"},
            {"name": "preview-asset-reachability", "passed": True, "detail": "ok"},
        ],
    }
    async with httpx.AsyncClient(transport=transport) as client:
        result = await run_delivery_role_swarm(
            "http://example.test/preview/runtime/rich/",
            live_qa=live_qa,
            goal_input="Crosswalk PDPA CCPA",
            client=client,
        )
    assert result.accepted is True, {
        r.role_id: {"ok": r.accepted, "gaps": r.gaps, "findings": r.findings[:3]}
        for r in result.roles
    }
    assert all(r.accepted for r in result.roles)
    assert {r.role_id for r in result.roles} == set(APP_PROJECT_ROLES)
