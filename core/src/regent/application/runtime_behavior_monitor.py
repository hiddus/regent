"""Runtime behavior monitor — independent observation of deployed applications.

This module runs outside the execution pipeline. It periodically fetches
the deployed preview and analyzes behavioral quality:
- Dialogue time distribution (are characters active at unrealistic hours?)
- Content diversity (is the output repetitive or varied?)
- Character activity patterns (do all characters do the same thing?)
- World state consistency (does the world background match character actions?)

Design principles:
- **Independent**: Not embedded in execution_orchestrator. Runs as a
  background tick in the worker loop (like host_guard).
- **Observation only**: Produces BehaviorObservation records, does NOT
  modify the application or its code.
- **Configurable**: Each goal type has different check strategies.
- **Lightweight**: Uses HTTP fetches to the preview URL; no heavy analysis.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Observation model
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BehaviorObservation:
    """A single behavioral observation about a deployed application."""

    goal_id: uuid.UUID
    observed_at: datetime
    metric_name: str          # e.g. "dialogue_time_distribution"
    metric_value: dict[str, Any]
    anomaly: bool
    severity: str             # NONE / LOW / MEDIUM / HIGH
    detail: str
    preview_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "goal_id": str(self.goal_id),
            "observed_at": self.observed_at.isoformat(),
            "metric_name": self.metric_name,
            "metric_value": dict(self.metric_value),
            "anomaly": self.anomaly,
            "severity": self.severity,
            "detail": self.detail,
            "preview_url": self.preview_url,
        }


# ---------------------------------------------------------------------------
# Check strategies
# ---------------------------------------------------------------------------

# Night hours pattern: dialogue mentioning late-night hours (23:xx, 00:xx, etc.)
_NIGHT_HOUR_RE = re.compile(
    r"(?:23:[0-5]\d|00:[0-5]\d|凌晨|深夜|午夜|midnight|1[12]:[0-5]\d\s*(?:am|AM))",
)
# Day hours pattern
_DAY_HOUR_RE = re.compile(
    r"(?:[0-9]|10):[0-5]\d\s*(?:am|AM|pm|PM)|上午|下午|中午|早晨|早上",
)
# Outdoor/indoor hints
_OUTDOOR_RE = re.compile(r"(?:户外|公园|街道|外面|outside|park|street|广场)")
_INDOOR_RE = re.compile(r"(?:室内|家里|卧室|客厅|厨房|inside|home|bedroom)")


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _visible_text(html: str) -> str:
    return re.sub(r"\s+", " ", _strip_tags(html)).strip()


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class RuntimeBehaviorMonitor:
    """Independent observation loop for deployed application behavior.

    Usage:
        monitor = RuntimeBehaviorMonitor()
        observations = await monitor.observe(goal_id, preview_url, goal_profile)
    """

    def __init__(
        self,
        *,
        http_timeout: float = 15.0,
    ) -> None:
        self._http_timeout = http_timeout

    async def observe(
        self,
        goal_id: uuid.UUID,
        preview_url: str,
        *,
        goal_profile: dict[str, Any] | None = None,
    ) -> list[BehaviorObservation]:
        """Run all applicable checks and return observations."""
        if not preview_url:
            return []

        observations: list[BehaviorObservation] = []
        profile = goal_profile or {}
        domain = profile.get("domain", "other")

        try:
            async with httpx.AsyncClient(
                timeout=self._http_timeout, follow_redirects=True
            ) as http:
                resp = await http.get(preview_url)
                if resp.status_code >= 400:
                    observations.append(
                        BehaviorObservation(
                            goal_id=goal_id,
                            observed_at=datetime.now(UTC),
                            metric_name="preview_reachability",
                            metric_value={"status": resp.status_code},
                            anomaly=True,
                            severity="HIGH",
                            detail=f"Preview returned status {resp.status_code}",
                            preview_url=preview_url,
                        )
                    )
                    return observations

                html = resp.text
                visible = _visible_text(html)

                # Always check: content volume
                observations.append(
                    self._check_content_volume(goal_id, preview_url, visible)
                )

                # Domain-specific checks
                if domain == "interactive-app":
                    observations.extend(
                        self._check_dialogue_realism(goal_id, preview_url, visible)
                    )
                    observations.extend(
                        self._check_character_diversity(goal_id, preview_url, visible)
                    )
                    observations.extend(
                        self._check_world_background(goal_id, preview_url, visible, html)
                    )

                    # Deep analysis: fetch and analyze JS data files
                    js_observations = await self._check_js_data_quality(
                        goal_id, preview_url, html
                    )
                    observations.extend(js_observations)

                # Filter out NONE-severity observations
                observations = [o for o in observations if o.severity != "NONE"]

        except Exception as exc:
            logger.warning(
                "behavior monitor fetch failed",
                extra={"goal_id": str(goal_id), "error": str(exc)[:200]},
            )
            observations.append(
                BehaviorObservation(
                    goal_id=goal_id,
                    observed_at=datetime.now(UTC),
                    metric_name="monitor_error",
                    metric_value={"error": str(exc)[:200]},
                    anomaly=True,
                    severity="MEDIUM",
                    detail=f"Monitor could not reach preview: {type(exc).__name__}",
                    preview_url=preview_url,
                )
            )

        return observations

    def _check_content_volume(
        self, goal_id: uuid.UUID, preview_url: str, visible: str
    ) -> BehaviorObservation:
        """Check if the page has sufficient content (not an empty shell)."""
        char_count = len(visible)
        if char_count < 200:
            return BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="content_volume",
                metric_value={"visible_chars": char_count, "threshold": 200},
                anomaly=True,
                severity="MEDIUM",
                detail=f"页面可见文本仅 {char_count} 字符，可能为空白壳",
                preview_url=preview_url,
            )
        return BehaviorObservation(
            goal_id=goal_id,
            observed_at=datetime.now(UTC),
            metric_name="content_volume",
            metric_value={"visible_chars": char_count, "threshold": 200},
            anomaly=False,
            severity="NONE",
            detail=f"页面可见文本 {char_count} 字符",
            preview_url=preview_url,
        )

    def _check_dialogue_realism(
        self, goal_id: uuid.UUID, preview_url: str, visible: str
    ) -> list[BehaviorObservation]:
        """Check if character dialogues respect time-of-day logic.

        Anomalies:
        - Many dialogues at night hours (23:xx-05:xx) while characters are outdoors
        - All dialogues happening at the same time (no temporal diversity)
        """
        observations: list[BehaviorObservation] = []
        night_hits = len(_NIGHT_HOUR_RE.findall(visible))
        day_hits = len(_DAY_HOUR_RE.findall(visible))
        outdoor_hits = len(_OUTDOOR_RE.findall(visible))

        total_time_refs = night_hits + day_hits
        if total_time_refs == 0:
            return [BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="dialogue_time_distribution",
                metric_value={"night": 0, "day": 0, "outdoor": outdoor_hits},
                anomaly=False,
                severity="LOW",
                detail="未检测到时间标记，对话可能缺乏时间维度",
                preview_url=preview_url,
            )]

        night_ratio = night_hits / max(total_time_refs, 1)

        # Anomaly: >60% night references + outdoor mentions
        if night_ratio > 0.6 and outdoor_hits > 2:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="dialogue_time_distribution",
                metric_value={
                    "night": night_hits,
                    "day": day_hits,
                    "night_ratio": round(night_ratio, 2),
                    "outdoor": outdoor_hits,
                },
                anomaly=True,
                severity="MEDIUM",
                detail=(
                    f"深夜对话占比 {night_ratio:.0%}，但有 {outdoor_hits} 处户外场景，"
                    "角色可能在不合理时间出现在户外"
                ),
                preview_url=preview_url,
            ))

        # Anomaly: all dialogues at the same time (no diversity)
        if total_time_refs >= 3 and (night_ratio > 0.9 or night_ratio < 0.1):
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="dialogue_time_diversity",
                metric_value={
                    "night_ratio": round(night_ratio, 2),
                    "total_refs": total_time_refs,
                },
                anomaly=True,
                severity="LOW",
                detail="对话时间分布过于集中，缺乏时间多样性",
                preview_url=preview_url,
            ))

        return observations

    def _check_character_diversity(
        self, goal_id: uuid.UUID, preview_url: str, visible: str
    ) -> list[BehaviorObservation]:
        """Check if multiple characters are present and active."""
        # Look for character name patterns (Chinese names: 2-3 chars)
        # This is a heuristic — real implementation would use data.js
        observations: list[BehaviorObservation] = []

        # Check for repetitive content (same phrase repeated many times)
        phrases: dict[str, int] = {}
        words = visible.split("。")
        for w in words:
            w = w.strip()
            if 10 < len(w) < 100:
                phrases[w] = phrases.get(w, 0) + 1

        repeated = {k: v for k, v in phrases.items() if v >= 3}
        if repeated:
            top = max(repeated, key=repeated.get)  # type: ignore[arg-type]
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="content_repetition",
                metric_value={
                    "repeated_phrases": len(repeated),
                    "top_phrase": top[:80],
                    "top_count": repeated[top],
                },
                anomaly=True,
                severity="MEDIUM",
                detail=f"发现 {len(repeated)} 个重复段落，最多重复 {repeated[top]} 次",
                preview_url=preview_url,
            ))

        return observations

    def _check_world_background(
        self, goal_id: uuid.UUID, preview_url: str, visible: str, html: str
    ) -> list[BehaviorObservation]:
        """Check if the world has sufficient background/context."""
        observations: list[BehaviorObservation] = []

        # Look for world-building elements
        world_hints = {
            "location": bool(re.search(r"(?:小镇|村庄|城市|镇|地方|world|town|village)", visible, re.I)),
            "time_setting": bool(re.search(r"(?:古代|现代|未来|中世纪|当代|时代|era|period)", visible, re.I)),
            "atmosphere": bool(re.search(r"(?:宁静|热闹|繁忙|安静|喧嚣|peaceful|bustling)", visible, re.I)),
            "rules": bool(re.search(r"(?:规则|规律|传统|习俗|法律|rule|tradition|custom)", visible, re.I)),
        }
        present = sum(1 for v in world_hints.values() if v)

        if present <= 1:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="world_background",
                metric_value={
                    "elements_detected": world_hints,
                    "elements_present": present,
                    "elements_needed": 3,
                },
                anomaly=True,
                severity="MEDIUM",
                detail=(
                    f"世界背景要素不足（检测到 {present}/4）："
                    f"地点={world_hints['location']}, "
                    f"时代={world_hints['time_setting']}, "
                    f"氛围={world_hints['atmosphere']}, "
                    f"规则={world_hints['rules']}"
                ),
                preview_url=preview_url,
            ))

        return observations

    async def _check_js_data_quality(
        self, goal_id: uuid.UUID, preview_url: str, html: str
    ) -> list[BehaviorObservation]:
        """Deep analysis: fetch JS data files and analyze character/world data.

        For SPA applications, the static HTML is just a shell. The actual
        content lives in JS files. This method:
        1. Extracts <script src="..."> references from the HTML
        2. Fetches each JS file
        3. Analyzes character definitions, routines, world rules
        """
        observations: list[BehaviorObservation] = []

        # Extract script src references (relative paths only)
        script_srcs = re.findall(
            r'<script\s+src=["\']([^"\']+)["\']', html, re.I
        )
        if not script_srcs:
            return observations

        # Build base URL for resolving relative paths
        base = preview_url.rstrip("/")
        js_contents: dict[str, str] = {}

        async with httpx.AsyncClient(
            timeout=self._http_timeout, follow_redirects=True
        ) as http:
            for src in script_srcs:
                if src.startswith(("http://", "https://")):
                    url = src
                else:
                    url = f"{base}/{src.lstrip('./')}"
                try:
                    resp = await http.get(url)
                    if resp.status_code == 200:
                        js_contents[src] = resp.text
                except Exception:
                    pass

        if not js_contents:
            return observations

        all_js = " ".join(js_contents.values())

        # --- Check 1: Character count and diversity ---
        name_matches = re.findall(
            r"name:\s*['\"]([^'\"]{2,6})['\"]", all_js
        )
        unique_names = set(name_matches)
        if len(unique_names) < 3:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="character_count",
                metric_value={
                    "unique_names": len(unique_names),
                    "names": sorted(unique_names),
                },
                anomaly=True,
                severity="MEDIUM",
                detail=f"仅检测到 {len(unique_names)} 个角色（建议 ≥3）: {sorted(unique_names)}",
                preview_url=preview_url,
            ))
        else:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="character_count",
                metric_value={
                    "unique_names": len(unique_names),
                    "names": sorted(unique_names),
                },
                anomaly=False,
                severity="NONE",
                detail=f"检测到 {len(unique_names)} 个角色: {sorted(unique_names)}",
                preview_url=preview_url,
            ))

        # --- Check 2: Location Diversity ---
        location_names = {
            n for n in set(re.findall(r"name:\s*['\"]([^'\"]{2,10})['\"]", all_js))
            if re.search(r"[镇村坊场馆园所院厅堂街巷路桥寺庙学校店]", n)
            or n in {"家", "办公室"}
        }
        if len(location_names) < 3:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="location_count",
                metric_value={"locations": len(location_names)},
                anomaly=True,
                severity="LOW",
                detail=f"地点数量较少（{len(location_names)} 个）",
                preview_url=preview_url,
            ))

        # --- Check 3: Daily routine / sleep cycle ---
        has_sleep = bool(re.search(
            r"(?:睡觉|就寝|入睡|晚安|sleep|bedtime|night|22:|23:|00:)", all_js
        ))
        has_wake = bool(re.search(
            r"(?:起床|醒来|早起|wake|morning|06:|07:)", all_js
        ))
        if not has_sleep or not has_wake:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="sleep_cycle",
                metric_value={"has_sleep": has_sleep, "has_wake": has_wake},
                anomaly=True,
                severity="MEDIUM",
                detail=(
                    "昼夜节律不完整："
                    f"就寝逻辑={'有' if has_sleep else '缺失'}, "
                    f"起床逻辑={'有' if has_wake else '缺失'}"
                ),
                preview_url=preview_url,
            ))
        else:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="sleep_cycle",
                metric_value={"has_sleep": True, "has_wake": True},
                anomaly=False,
                severity="NONE",
                detail="昼夜节律完整（含就寝和起床逻辑）",
                preview_url=preview_url,
            ))

        # --- Check 4: Dialogue cooldown / limits ---
        has_cooldown = bool(re.search(
            r"(?:cooldown|冷却|间隔|cool_down|coolDown)", all_js
        ))
        has_daily_limit = bool(re.search(
            r"(?:MAX_DAILY|max_daily|每日.*上限|daily.*limit)", all_js, re.I
        ))
        if not has_cooldown and not has_daily_limit:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="dialogue_guards",
                metric_value={"has_cooldown": has_cooldown, "has_daily_limit": has_daily_limit},
                anomaly=True,
                severity="LOW",
                detail="未检测到对话冷却或每日上限机制，可能导致对话过度频繁",
                preview_url=preview_url,
            ))

        # --- Check 5: Character depth ---
        has_bio = bool(re.search(r"bio:\s*['\"]", all_js))
        has_personality = bool(re.search(r"personality:\s*\[", all_js))
        has_routine = bool(re.search(r"(?:dailyRoutine|routine|schedule):\s*\[", all_js))
        missing = []
        if not has_bio: missing.append("人物简介(bio)")
        if not has_personality: missing.append("性格特点(personality)")
        if not has_routine: missing.append("日常作息(routine)")
        if missing:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="character_depth",
                metric_value={"has_bio": has_bio, "has_personality": has_personality, "has_routine": has_routine},
                anomaly=True,
                severity="MEDIUM",
                detail=f"角色深度不足，缺少: {', '.join(missing)}",
                preview_url=preview_url,
            ))
        else:
            observations.append(BehaviorObservation(
                goal_id=goal_id,
                observed_at=datetime.now(UTC),
                metric_name="character_depth",
                metric_value={"has_bio": True, "has_personality": True, "has_routine": True},
                anomaly=False,
                severity="NONE",
                detail="角色深度完整（含简介、性格、作息）",
                preview_url=preview_url,
            ))

        return observations
