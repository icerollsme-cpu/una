"""
analytics_agent.py — Tracks post performance and closes the improvement loop.

Two data sources:
  PostLog    — JSON file that records every post (platform, topic, UTM params)
  GA4Client  — Google Analytics 4 Data API (free, needs service-account key)

Workflow:
  1. orchestrator calls  log_post()          after every post
  2. orchestrator calls  get_performance()   after a delay (e.g. 48 h later)
  3. orchestrator calls  get_recommendations() to get AI improvement brief
  4. improvement brief fed back into next content_agent run

UTM tracking:
  Every post link is tagged:  ?utm_source=<platform>&utm_medium=social
                               &utm_campaign=<slug>&utm_content=<date>
  GA4 then shows exactly which posts drove sessions/conversions.

GA4 setup (one-time, completely free):
  1. google.com/analytics → Admin → Service Accounts → create key → download JSON
  2. In GA4 Admin → Property Access Management → add service account email as Viewer
  3. Set GA4_PROPERTY_ID and GA4_CREDENTIALS_FILE in .env
"""

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from openai import OpenAI

log = logging.getLogger(__name__)

POST_LOG_PATH = Path(os.getenv("POST_LOG_FILE", "output/post_log.json"))


def _env(key: str, required: bool = False) -> Optional[str]:
    val = os.getenv(key)
    if required and not val:
        raise EnvironmentError(f"Missing required env var: {key}")
    return val


def _ai_client() -> OpenAI:
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=_env("NVIDIA_API_KEY", required=True),
    )


# ---------------------------------------------------------------------------
# UTM link builder
# ---------------------------------------------------------------------------

def build_utm_url(
    base_url: str,
    platform: str,
    topic: str,
    campaign: Optional[str] = None,
) -> str:
    """
    Append UTM tracking parameters to a URL so GA4 can attribute traffic.

    Example:
        https://mysite.com/products?utm_source=tiktok&utm_medium=social
        &utm_campaign=3d_printing&utm_content=20240415
    """
    slug = (campaign or topic).lower().replace(" ", "_")[:40]
    params = {
        "utm_source": platform.lower(),
        "utm_medium": "social",
        "utm_campaign": slug,
        "utm_content": datetime.utcnow().strftime("%Y%m%d"),
    }
    parsed = urlparse(base_url)
    existing = parse_qs(parsed.query)
    existing.update({k: [v] for k, v in params.items()})
    flat = {k: v[0] for k, v in existing.items()}
    new_query = urlencode(flat)
    return urlunparse(parsed._replace(query=new_query))


# ---------------------------------------------------------------------------
# Post log — persists every published post to a JSON file
# ---------------------------------------------------------------------------

class PostLog:
    def __init__(self, path: Path = POST_LOG_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"posts": []}, indent=2))

    def _read(self) -> dict:
        return json.loads(self.path.read_text())

    def _write(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2))

    def log(
        self,
        platform: str,
        topic: str,
        site_url: str,
        utm_campaign: str,
        post_result: dict,
        content_type: str,
        files: Optional[dict] = None,
    ) -> str:
        """Record a published post. Returns the generated log entry ID."""
        entry_id = str(uuid.uuid4())[:8]
        entry = {
            "id": entry_id,
            "platform": platform,
            "topic": topic,
            "site_url": site_url,
            "utm_campaign": utm_campaign,
            "content_type": content_type,
            "post_result": post_result,
            "files": files or {},
            "posted_at": datetime.utcnow().isoformat(),
            "ga_sessions": None,       # filled in by fetch_ga_performance()
            "ga_conversions": None,
            "platform_metrics": None,  # filled in by fetch_platform_metrics()
            "performance_score": None, # filled in by score_post()
        }
        data = self._read()
        data["posts"].append(entry)
        self._write(data)
        log.info("Post logged: %s [%s / %s]", entry_id, platform, topic)
        return entry_id

    def get_all(self) -> list[dict]:
        return self._read().get("posts", [])

    def get_recent(self, days: int = 30) -> list[dict]:
        cutoff = datetime.utcnow() - timedelta(days=days)
        return [
            p for p in self.get_all()
            if datetime.fromisoformat(p["posted_at"]) > cutoff
        ]

    def update(self, entry_id: str, updates: dict) -> None:
        data = self._read()
        for post in data["posts"]:
            if post["id"] == entry_id:
                post.update(updates)
                break
        self._write(data)


# ---------------------------------------------------------------------------
# Google Analytics 4 — Data API
# ---------------------------------------------------------------------------

class GA4Client:
    """
    Thin wrapper around the GA4 Data API.

    Requires:
      GA4_PROPERTY_ID      — numeric property ID (e.g. "123456789")
      GA4_CREDENTIALS_FILE — path to service-account JSON key file
    """

    def __init__(self):
        self.property_id = _env("GA4_PROPERTY_ID")
        self.creds_file = _env("GA4_CREDENTIALS_FILE")
        self._client = None

    def _is_configured(self) -> bool:
        return bool(self.property_id and self.creds_file and
                    os.path.exists(self.creds_file or ""))

    def _get_client(self):
        if self._client is None:
            from google.analytics.data_v1beta import BetaAnalyticsDataClient
            self._client = BetaAnalyticsDataClient.from_service_account_file(
                self.creds_file
            )
        return self._client

    def get_social_traffic(self, days: int = 14) -> list[dict]:
        """
        Return sessions + conversions grouped by utm_source / utm_campaign
        for the last N days.
        """
        if not self._is_configured():
            log.warning("GA4 not configured — skipping analytics fetch. "
                        "Set GA4_PROPERTY_ID and GA4_CREDENTIALS_FILE in .env")
            return []

        from google.analytics.data_v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange, FilterExpression,
            Filter, DimensionFilter,
        )

        request = RunReportRequest(
            property=f"properties/{self.property_id}",
            dimensions=[
                Dimension(name="sessionSource"),
                Dimension(name="sessionMedium"),
                Dimension(name="sessionCampaignName"),
                Dimension(name="date"),
            ],
            metrics=[
                Metric(name="sessions"),
                Metric(name="conversions"),
                Metric(name="engagementRate"),
                Metric(name="averageSessionDuration"),
            ],
            date_ranges=[DateRange(
                start_date=f"{days}daysAgo",
                end_date="today",
            )],
            dimension_filter=FilterExpression(
                filter=DimensionFilter(
                    field_name="sessionMedium",
                    string_filter=Filter.StringFilter(value="social"),
                )
            ),
        )

        response = self._get_client().run_report(request)
        rows = []
        for row in response.rows:
            dims = [d.value for d in row.dimension_values]
            mets = [m.value for m in row.metric_values]
            rows.append({
                "source": dims[0],
                "medium": dims[1],
                "campaign": dims[2],
                "date": dims[3],
                "sessions": int(mets[0]),
                "conversions": int(mets[1]),
                "engagement_rate": float(mets[2]),
                "avg_session_duration": float(mets[3]),
            })
        return rows

    def get_top_landing_pages(self, days: int = 14) -> list[dict]:
        """Return top landing pages driven by social traffic."""
        if not self._is_configured():
            return []

        from google.analytics.data_v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange,
        )

        request = RunReportRequest(
            property=f"properties/{self.property_id}",
            dimensions=[
                Dimension(name="landingPage"),
                Dimension(name="sessionSource"),
            ],
            metrics=[
                Metric(name="sessions"),
                Metric(name="bounceRate"),
                Metric(name="conversions"),
            ],
            date_ranges=[DateRange(
                start_date=f"{days}daysAgo",
                end_date="today",
            )],
            limit=20,
        )

        response = self._get_client().run_report(request)
        pages = []
        for row in response.rows:
            dims = [d.value for d in row.dimension_values]
            mets = [m.value for m in row.metric_values]
            pages.append({
                "page": dims[0],
                "source": dims[1],
                "sessions": int(mets[0]),
                "bounce_rate": float(mets[1]),
                "conversions": int(mets[2]),
            })
        return pages


# ---------------------------------------------------------------------------
# Performance scorer — matches GA4 data with post log entries
# ---------------------------------------------------------------------------

def fetch_ga_performance(log_obj: PostLog, ga: GA4Client, days: int = 14) -> None:
    """
    Pull GA4 social traffic data and write sessions/conversions back into
    matching post log entries (matched by utm_campaign).
    """
    rows = ga.get_social_traffic(days=days)
    if not rows:
        return

    # Build lookup: {campaign: {source: {sessions, conversions}}}
    campaign_map: dict = {}
    for row in rows:
        camp = row["campaign"]
        src = row["source"]
        if camp not in campaign_map:
            campaign_map[camp] = {}
        if src not in campaign_map[camp]:
            campaign_map[camp][src] = {"sessions": 0, "conversions": 0}
        campaign_map[camp][src]["sessions"] += row["sessions"]
        campaign_map[camp][src]["conversions"] += row["conversions"]

    for post in log_obj.get_all():
        camp = post.get("utm_campaign", "")
        platform = post.get("platform", "")
        match = campaign_map.get(camp, {}).get(platform, {})
        if match:
            log_obj.update(post["id"], {
                "ga_sessions": match["sessions"],
                "ga_conversions": match["conversions"],
            })
            log.info(
                "Post %s (%s/%s): %d sessions, %d conversions",
                post["id"], platform, camp,
                match["sessions"], match["conversions"],
            )


# ---------------------------------------------------------------------------
# AI-powered improvement recommendations
# ---------------------------------------------------------------------------

def get_recommendations(
    log_obj: PostLog,
    site_url: str,
    days: int = 30,
) -> dict:
    """
    Analyse recent post performance and return AI-generated recommendations
    for improving the next batch of content.

    Returns:
        {
          "top_performers": [...],
          "underperformers": [...],
          "platform_insights": {...},
          "content_type_insights": {...},
          "recommendations": [...],
          "next_focus_topics": [...],
          "next_focus_platforms": [...],
        }
    """
    posts = log_obj.get_recent(days=days)
    if not posts:
        return {"recommendations": ["No post history yet — run the pipeline first."]}

    # Build a trimmed summary for the AI (no file paths, just metrics)
    summary = []
    for p in posts:
        summary.append({
            "platform": p["platform"],
            "topic": p["topic"],
            "content_type": p["content_type"],
            "utm_campaign": p["utm_campaign"],
            "ga_sessions": p.get("ga_sessions"),
            "ga_conversions": p.get("ga_conversions"),
            "posted_at": p["posted_at"],
        })

    client = _ai_client()
    prompt = f"""Analyse this social media post performance data for site {site_url}.
Each entry shows what was posted and how much GA4 traffic it drove back to the site.

Performance data (last {days} days):
{json.dumps(summary, indent=2)}

Return ONLY a JSON object with these keys:
{{
  "top_performers": [
    {{"platform": "...", "topic": "...", "content_type": "...", "reason": "..."}}
  ],
  "underperformers": [
    {{"platform": "...", "topic": "...", "why_it_failed": "..."}}
  ],
  "platform_insights": {{
    "<platform>": "<what content works best here based on data>"
  }},
  "content_type_insights": {{
    "video": "<performance pattern>",
    "image": "<performance pattern>",
    "text": "<performance pattern>",
    "graph": "<performance pattern>"
  }},
  "recommendations": [
    "<specific actionable improvement for the next batch>"
  ],
  "next_focus_topics": ["<topic1>", "<topic2>"],
  "next_focus_platforms": ["<platform1>", "<platform2>"]
}}"""

    resp = client.chat.completions.create(
        model=_env("KIMI_MODEL") or "moonshotai/kimi-k2-instruct",
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a data-driven social media performance analyst. "
                    "Return only valid JSON — no markdown, no commentary."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1500,
    )

    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    result = json.loads(raw)
    log.info(
        "Recommendations ready: %d top performers, %d recommendations",
        len(result.get("top_performers", [])),
        len(result.get("recommendations", [])),
    )
    return result


# ---------------------------------------------------------------------------
# Full performance report
# ---------------------------------------------------------------------------

def get_performance_report(
    site_url: str,
    days: int = 14,
    update_ga: bool = True,
) -> dict:
    """
    Pull GA4 data, update the post log, and return recommendations.

    Args:
        site_url:  The site being promoted (used as context for AI analysis)
        days:      How many days of data to pull
        update_ga: Whether to fetch fresh GA4 data (requires GA4 configured)

    Returns:
        {
          "post_count": int,
          "total_ga_sessions": int,
          "total_ga_conversions": int,
          "top_landing_pages": [...],
          "recommendations": {...},
        }
    """
    log_obj = PostLog()
    ga = GA4Client()

    if update_ga:
        fetch_ga_performance(log_obj, ga, days=days)

    posts = log_obj.get_recent(days=days)
    total_sessions = sum(p.get("ga_sessions") or 0 for p in posts)
    total_conversions = sum(p.get("ga_conversions") or 0 for p in posts)

    recommendations = get_recommendations(log_obj, site_url, days=days)
    top_pages = ga.get_top_landing_pages(days=days)

    report = {
        "site_url": site_url,
        "period_days": days,
        "post_count": len(posts),
        "total_ga_sessions": total_sessions,
        "total_ga_conversions": total_conversions,
        "top_landing_pages": top_pages[:10],
        "recommendations": recommendations,
    }

    log.info(
        "Performance report: %d posts, %d sessions, %d conversions over %d days",
        len(posts), total_sessions, total_conversions, days,
    )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Pull GA4 performance data and get content improvement recommendations"
    )
    parser.add_argument("site_url", help="The site being promoted, e.g. https://example.com")
    parser.add_argument("--days", type=int, default=14,
                        help="Days of data to analyse (default 14)")
    parser.add_argument("--no-ga", action="store_true",
                        help="Skip GA4 fetch (use cached post log data only)")
    parser.add_argument("--output", "-o", help="Save report JSON to this file")
    args = parser.parse_args()

    report = get_performance_report(
        site_url=args.site_url,
        days=args.days,
        update_ga=not args.no_ga,
    )

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"Report saved to {args.output}")
    else:
        print(json.dumps(report, indent=2))
