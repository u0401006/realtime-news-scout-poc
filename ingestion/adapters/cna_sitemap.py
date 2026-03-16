"""CNA sitemap adapter — 從 Google News sitemap XML 解析新聞清單。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

import httpx

SITEMAP_URL = "https://www.cna.com.tw/googlenewssitemap_fromremote_cfp.xml"

# XML namespaces
NS = {
    "sm": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

# 從 URL 抽取 pid，例如 202603160156
_PID_RE = re.compile(r"/(\d{12,})\.aspx")


@dataclass
class SitemapEntry:
    """單筆 sitemap 條目。"""

    url: str
    pid: str
    title: str
    published_at: datetime
    keywords: List[str] = field(default_factory=list)


def _parse_dt(text: str) -> datetime:
    """Parse ISO-8601 datetime string with timezone."""
    # Python 3.11+ fromisoformat handles +08:00
    return datetime.fromisoformat(text)


def fetch_sitemap(
    *,
    timeout: float = 15.0,
) -> List[SitemapEntry]:
    """下載並解析 CNA sitemap，回傳全部條目。"""
    resp = httpx.get(SITEMAP_URL, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return parse_sitemap_xml(resp.text)


def parse_sitemap_xml(xml_text: str) -> List[SitemapEntry]:
    """解析 sitemap XML 文字為 SitemapEntry 清單。"""
    root = ET.fromstring(xml_text)
    entries: List[SitemapEntry] = []

    for url_el in root.findall("sm:url", NS):
        loc = url_el.findtext("sm:loc", "", NS).strip()
        news_el = url_el.find("news:news", NS)
        if news_el is None:
            continue

        title = (news_el.findtext("news:title", "", NS) or "").strip()
        pub_date_str = (news_el.findtext("news:publication_date", "", NS) or "").strip()
        kw_str = (news_el.findtext("news:keywords", "", NS) or "").strip()

        if not loc or not pub_date_str:
            continue

        m = _PID_RE.search(loc)
        pid = m.group(1) if m else ""

        entries.append(
            SitemapEntry(
                url=loc,
                pid=pid,
                title=title,
                published_at=_parse_dt(pub_date_str),
                keywords=[k.strip() for k in kw_str.split(",") if k.strip()],
            )
        )

    return entries


def filter_by_window(
    entries: List[SitemapEntry],
    start: datetime,
    end: datetime,
) -> List[SitemapEntry]:
    """篩選落在 [start, end) 時間窗內的條目。"""
    # 確保 start/end 有時區
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    return [e for e in entries if start <= e.published_at < end]
