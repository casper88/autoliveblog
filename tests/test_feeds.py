"""Podcast RSS/Atom 解析測試(純解析,不觸網)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoliveblog import feeds

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>測試財經 Podcast</title>
    <item>
      <title>第 2 集:比較新的一集</title>
      <pubDate>Fri, 24 Jul 2026 08:00:00 +0800</pubDate>
      <guid>ep-002</guid>
      <link>https://example.com/ep2</link>
      <itunes:duration>1:02:03</itunes:duration>
      <enclosure url="https://cdn.example.com/ep2.mp3" type="audio/mpeg" length="1"/>
    </item>
    <item>
      <title>第 1 集:比較舊的一集</title>
      <pubDate>Thu, 23 Jul 2026 08:00:00 +0800</pubDate>
      <guid>ep-001</guid>
      <itunes:duration>1800</itunes:duration>
      <enclosure url="https://cdn.example.com/ep1.mp3" type="audio/mpeg" length="1"/>
    </item>
    <item>
      <title>純文字公告(無音檔,應被略過)</title>
      <pubDate>Sat, 25 Jul 2026 08:00:00 +0800</pubDate>
      <guid>note-1</guid>
    </item>
  </channel>
</rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom 節目</title>
  <entry>
    <title>Atom 第一集</title>
    <id>atom-1</id>
    <published>2026-07-20T10:00:00Z</published>
    <link rel="enclosure" type="audio/mpeg" href="https://cdn.example.com/a1.mp3"/>
  </entry>
</feed>"""


def test_looks_like_feed():
    assert feeds.looks_like_feed("https://example.com/feed.xml")
    assert feeds.looks_like_feed("https://anchor.fm/s/12345/podcast/rss")
    assert feeds.looks_like_feed("https://feeds.megaphone.fm/ABC123")
    # 一般影音網址不該被誤判成 feed
    assert not feeds.looks_like_feed("https://www.youtube.com/watch?v=abc")
    assert not feeds.looks_like_feed("https://www.twitch.tv/someone")
    assert not feeds.looks_like_feed("not-a-url")


def test_parse_rss_orders_and_filters():
    title, eps = feeds.parse_feed(RSS)
    assert title == "測試財經 Podcast"
    # 無音檔的條目要被略過(即使它日期最新)
    assert len(eps) == 2
    # 最新的排最前面
    assert eps[0].title.startswith("第 2 集")
    assert eps[0].audio_url == "https://cdn.example.com/ep2.mp3"
    assert eps[0].guid == "ep-002"
    assert eps[0].page_url == "https://example.com/ep2"
    # itunes:duration 兩種格式都要能解析
    assert eps[0].duration == 3723   # 1:02:03
    assert eps[1].duration == 1800   # 純秒數
    assert eps[0].published > eps[1].published
    assert eps[0].feed_title == "測試財經 Podcast"


def test_parse_atom():
    title, eps = feeds.parse_feed(ATOM)
    assert title == "Atom 節目"
    assert len(eps) == 1
    assert eps[0].audio_url == "https://cdn.example.com/a1.mp3"
    assert eps[0].guid == "atom-1"
    assert eps[0].published is not None


def test_duration_parser_edge_cases():
    assert feeds._parse_duration(None) is None
    assert feeds._parse_duration("") is None
    assert feeds._parse_duration("abc") is None
    assert feeds._parse_duration("90") == 90
    assert feeds._parse_duration("05:30") == 330


# ---- 以下為稽核抓到的問題的回歸測試 ----

def test_bad_dates_never_crash():
    """1970 前/無時區的日期在 Windows 會讓 .timestamp() 拋 OSError,
    絕不能讓整個 feed 掛掉。"""
    for raw in ("Thu, 01 Jan 1960 00:00:00 -0000",
                "Mon, 01 Jan 1900 00:00:00 GMT",
                "Fri, 31 Dec 9999 23:59:59 +0000",
                "not a date", "", None):
        feeds._parse_date(raw)  # 不該拋例外
    # 無時區資訊要當 UTC,不能用本機時區
    utc = feeds._parse_date("Wed, 01 Jan 2020 00:00:00 -0000")
    assert utc == 1577836800.0


def test_one_bad_item_does_not_kill_feed():
    """單一條目異常時,其他集數仍要正常解析。"""
    bad = RSS.replace("<pubDate>Thu, 23 Jul 2026 08:00:00 +0800</pubDate>",
                      "<pubDate>Mon, 01 Jan 1900 00:00:00 GMT</pubDate>")
    _, eps = feeds.parse_feed(bad)
    assert len(eps) == 2
    assert eps[0].title.startswith("第 2 集")


def test_video_platforms_never_routed_to_feeds():
    """影音平台一律交給 yt-dlp,即使網址含 feed/rss 字樣。"""
    assert not feeds.looks_like_feed(
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCabc")
    assert not feeds.looks_like_feed("https://www.twitch.tv/feedme")
    assert not feeds.looks_like_feed("https://kick.com/feedthebeast")
    # 真正的 podcast feed 仍要被辨識
    assert feeds.looks_like_feed("https://feeds.npr.org/510289/podcast.xml")
    assert feeds.looks_like_feed("https://example.com/rss")
    assert feeds.looks_like_feed("https://anchor.fm/s/123/podcast/rss")
    # 只是路徑裡有 rss 字樣的一般網址不該誤判
    assert not feeds.looks_like_feed("https://example.com/rssingtons/video")


def test_bytes_input_honours_declared_encoding():
    """傳 bytes 時要用 XML 宣告的編碼,不能靠猜(否則非 ASCII 標題會變亂碼)。"""
    xml = ('<?xml version="1.0" encoding="ISO-8859-1"?>'
           '<rss version="2.0"><channel><title>Caf\xe9 Podcast</title>'
           '<item><title>Ep</title><guid>1</guid>'
           '<enclosure url="https://x/a.mp3" type="audio/mpeg"/>'
           '</item></channel></rss>').encode("iso-8859-1")
    title, eps = feeds.parse_feed(xml)
    assert title == "Café Podcast"
    assert len(eps) == 1
