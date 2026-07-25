"""Podcast RSS/Atom 支援:yt-dlp 不處理 feed,這裡自己解析。

只用標準庫的 XML 解析,不引入額外相依。取得的音檔網址(enclosure)可以直接
餵進既有的 VOD 管線 —— 對總結器來說,它跟 yt-dlp 下載下來的音訊沒有差別。
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

_NS = {
    "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd",
    "atom": "http://www.w3.org/2005/Atom",
    "media": "http://search.yahoo.com/mrss/",
}
_AUDIO_EXT = (".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac")
_HEADERS = {"User-Agent": "autoliveblog/0.1 (+https://github.com/casper88/autoliveblog)"}


@dataclass(frozen=True)
class Episode:
    feed_title: str
    title: str
    audio_url: str
    guid: str
    published: float | None  # unix timestamp;無法解析時為 None
    duration: int | None     # 秒
    page_url: str = ""

    @property
    def published_iso(self) -> str:
        if not self.published:
            return ""
        return datetime.fromtimestamp(self.published, timezone.utc).isoformat()


def looks_like_feed(url: str) -> bool:
    """粗略判斷是否為 RSS/Atom feed 網址(用於自動路由,不做網路請求)。"""
    u = url.lower()
    if not u.startswith(("http://", "https://")):
        return False
    path = urlparse(u).path
    if path.endswith((".xml", ".rss", ".atom")):
        return True
    return any(k in u for k in ("/feed", "/rss", "feed=", "format=rss",
                                "feeds.", "anchor.fm/s/", "feed.podbean",
                                "libsyn.com/rss", "megaphone.fm/"))


def _parse_duration(raw: str | None) -> int | None:
    """itunes:duration 可能是 '3600'、'1:02:03' 或 '05:30'。"""
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    parts = raw.split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    secs = 0
    for n in nums:
        secs = secs * 60 + n
    return secs


def _parse_date(raw: str | None) -> float | None:
    if not raw:
        return None
    raw = raw.strip()
    try:  # RSS:RFC 2822
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        pass
    try:  # Atom:ISO 8601
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _text(el, path: str, ns=None) -> str:
    found = el.find(path, ns or _NS)
    return (found.text or "").strip() if found is not None and found.text else ""


def _audio_from_item(item) -> str:
    """依序找:enclosure → media:content → atom link → 內文中的音檔連結。"""
    for enc in item.findall("enclosure"):
        url = enc.get("url", "")
        typ = (enc.get("type") or "").lower()
        if url and (typ.startswith("audio") or url.lower().split("?")[0]
                    .endswith(_AUDIO_EXT)):
            return url
    for mc in item.findall("media:content", _NS):
        url = mc.get("url", "")
        if url and ((mc.get("type") or "").startswith("audio")
                    or url.lower().split("?")[0].endswith(_AUDIO_EXT)):
            return url
    for link in item.findall("atom:link", _NS) + item.findall("link"):
        href = link.get("href", "") if link.get("href") else ""
        typ = (link.get("type") or "").lower()
        if href and (typ.startswith("audio") or href.lower().split("?")[0]
                     .endswith(_AUDIO_EXT)):
            return href
    return ""


def parse_feed(xml_text: str) -> tuple[str, list[Episode]]:
    """解析 RSS/Atom 內容 → (節目名稱, 集數列表);最新的排最前面。"""
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is not None:            # RSS 2.0
        feed_title = _text(channel, "title")
        items = channel.findall("item")
        date_paths = ("pubDate",)
    else:                              # Atom
        feed_title = _text(root, "atom:title") or _text(root, "title")
        items = root.findall("atom:entry", _NS) or root.findall("entry")
        date_paths = ("atom:published", "atom:updated", "published", "updated")

    episodes: list[Episode] = []
    for item in items:
        audio = _audio_from_item(item)
        if not audio:
            continue  # 沒有音檔的條目(純文字文章)略過
        title = _text(item, "title") or _text(item, "atom:title") or "(無標題)"
        published = None
        for p in date_paths:
            published = _parse_date(_text(item, p))
            if published:
                break
        guid = (_text(item, "guid") or _text(item, "atom:id")
                or _text(item, "id") or audio)
        page = _text(item, "link")
        episodes.append(Episode(
            feed_title=feed_title or "(未命名節目)",
            title=title, audio_url=audio, guid=guid, published=published,
            duration=_parse_duration(_text(item, "itunes:duration")),
            page_url=page if page.startswith("http") else "",
        ))
    episodes.sort(key=lambda e: e.published or 0, reverse=True)
    return feed_title or "(未命名節目)", episodes


def fetch_feed(url: str, timeout: int = 30) -> tuple[str, list[Episode]]:
    """下載並解析 feed。"""
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    # requests 對 XML 的編碼推測不可靠,優先用內容宣告的編碼
    text = r.content.decode(r.apparent_encoding or "utf-8", errors="replace")
    return parse_feed(text)


def latest_episode(url: str) -> Episode | None:
    _, eps = fetch_feed(url)
    return eps[0] if eps else None
