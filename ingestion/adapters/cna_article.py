"""CNA article adapter — 從文章頁面抽取內文。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import httpx

_ARTICLE_URL_TPL = "https://www.cna.com.tw/news/{cat}/{pid}.aspx"
_CAT_RE = re.compile(r"/news/(\w+)/")

# 抽取 <p> 內文
_PARA_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class ArticleContent:
    """文章內文。"""

    pid: str
    url: str
    paragraphs: list[str]
    full_text: str


def _extract_paragraphs(html: str) -> list[str]:
    """從 HTML 中抽取段落文字（去除 tag）。"""
    raw = _PARA_RE.findall(html)
    paragraphs: list[str] = []
    for p in raw:
        clean = _TAG_RE.sub("", p).strip()
        # 過濾太短或是 boilerplate
        if len(clean) < 15:
            continue
        if any(
            kw in clean
            for kw in [
                "隱私權規範",
                "Traditional Chinese",
                "Focus Taiwan",
                "加入中央社",
                "本網站使用",
            ]
        ):
            continue
        paragraphs.append(clean)
    return paragraphs


def get_article(
    pid: str,
    url: str,
    *,
    timeout: float = 15.0,
) -> Optional[ArticleContent]:
    """透過文章頁面 URL 抓取內文段落。

    Args:
        pid: 文章 ID（如 202603160156）
        url: 完整文章 URL
        timeout: HTTP timeout 秒數

    Returns:
        ArticleContent 或 None（失敗時）
    """
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; CNASmoke/1.0)",
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError:
        return None

    paragraphs = _extract_paragraphs(resp.text)
    if not paragraphs:
        return None

    return ArticleContent(
        pid=pid,
        url=url,
        paragraphs=paragraphs,
        full_text="\n".join(paragraphs),
    )
