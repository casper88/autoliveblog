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


_FEED_SEGMENTS = {"feed", "feeds", "rss", "atom", "podcast.xml", "rss.xml"}
_FEED_HOSTS = ("anchor.fm", "feed.podbean.com", "libsyn.com",
               "feeds.megaphone.fm", "feeds.simplecast.com",
               "feeds.buzzsprout.com", "feeds.captivate.fm",
               "feeds.transistor.fm", "feeds.acast.com", "feeds.npr.org")


def looks_like_feed(url: str) -> bool:
    """判斷是否為 RSS/Atom feed 網址(自動路由用,不做網路請求)。

    比對「路徑片段」而非子字串:否則 twitch.tv/feedme 這種頻道會被誤判成 feed。
    影音平台(YouTube/Twitch/...)一律交給 yt-dlp,即使網址長得像 feed。
    """
    u = url.strip().lower()
    if not u.startswith(("http://", "https://")):
        return False
    from . import platforms  # 延後匯入避免循環相依
    if platforms.detect(u).key != "generic":
        return False  # 已知影音平台由 yt-dlp 處理(含 YouTube 的頻道 RSS)
    parsed = urlparse(u)
    path = parsed.path
    if path.endswith((".xml", ".rss", ".atom")):
        return True
    if any(h in (parsed.hostname or "") for h in _FEED_HOSTS):
        return True
    segments = {s for s in path.split("/") if s}
    if segments & _FEED_SEGMENTS:
        return True
    return "format=rss" in (parsed.query or "") or "feed=" in (parsed.query or "")


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
    """解析發布時間。日期異常(過舊/過新/無時區)絕不能讓整個 feed 掛掉:
    Windows 上對 1970 前的 naive datetime 呼叫 .timestamp() 會拋 OSError。"""
    if not raw:
        return None
    raw = raw.strip()
    for parse in (parsedate_to_datetime,
                  lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            dt = parse(raw)
            if dt.tzinfo is None:  # 無時區資訊時一律當 UTC,不要用本機時區
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
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


def parse_feed(xml_text: str | bytes) -> tuple[str, list[Episode]]:
    """解析 RSS/Atom 內容 → (節目名稱, 集數列表);最新的排最前面。

    傳 bytes 時直接交給解析器,讓它讀 XML 宣告裡的編碼 —— 用猜的會產生亂碼。
    """
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
        # 單一條目異常不該毀掉整個 feed
        try:
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
        except Exception as e:
            print(f"[feeds] 略過無法解析的條目:{str(e)[:80]}")
    episodes.sort(key=lambda e: e.published or 0, reverse=True)
    return feed_title or "(未命名節目)", episodes


def fetch_feed(url: str, timeout: int = 30) -> tuple[str, list[Episode]]:
    """下載並解析 feed。"""
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    # 傳 raw bytes:解析器會讀 XML 宣告的編碼,比猜測可靠
    return parse_feed(r.content)


def latest_episode(url: str) -> Episode | None:
    _, eps = fetch_feed(url)
    return eps[0] if eps else None


def feed_info(feed_url: str) -> dict:
    """RSS feed → 與 yt-dlp info 相容的字典(最新一集),供 CLI 與 Web 共用。"""
    import re
    ep = latest_episode(feed_url)
    if not ep:
        raise RuntimeError(f"這個 feed 沒有可用的音檔集數:{feed_url}")
    return {
        "title": ep.title, "uploader": ep.feed_title,
        "id": re.sub(r"[^\w-]", "", ep.guid)[-24:] or "episode",
        "duration": ep.duration or 0, "is_live": False,
        "webpage_url": ep.page_url or feed_url,
        "_direct_audio_url": ep.audio_url,
    }


def get_info_any(url: str, cookies_from_browser: str | None = None) -> dict:
    """依網址型態取得 info:feed 走 RSS 解析,其餘交給 yt-dlp。
    feed 解析失敗時回退 yt-dlp,避免誤判把可用網址擋死。"""
    from . import ytdl
    if looks_like_feed(url):
        try:
            return feed_info(url)
        except Exception as e:
            print(f"[feeds] 當作 feed 解析失敗({str(e)[:60]}),改用 yt-dlp")
    return ytdl.get_info(url, cookies_from_browser)
