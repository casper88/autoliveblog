"""平台適配層測試:開播檢查網址、觀看/嵌入網址、時間戳跳轉。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoliveblog import platforms


def test_detect_known_platforms():
    assert platforms.detect("https://www.youtube.com/@ch").key == "youtube"
    assert platforms.detect("https://youtu.be/abc123").key == "youtube"
    assert platforms.detect("https://www.twitch.tv/someone").key == "twitch"
    assert platforms.detect("https://kick.com/someone").key == "kick"
    # 未知平台退到通用適配,核心管線仍可運作
    assert platforms.detect("https://example.com/live").key == "generic"


def test_clean_url_strips_tracking_params():
    # 這正是先前害開播檢查失效的 ?si= 追蹤參數
    assert platforms.clean_url(
        "https://youtube.com/@ch?si=XYZ") == "https://youtube.com/@ch"
    assert platforms.clean_url("https://youtube.com/@ch/") == "https://youtube.com/@ch"
    assert platforms.clean_url("https://a.com/x#frag") == "https://a.com/x"


def test_live_url_per_platform():
    # YouTube 需要 /live 後綴
    assert platforms.live_url_of(
        "https://youtube.com/@ch?si=Z") == "https://youtube.com/@ch/live"
    # 已經有 /live 不重複附加
    assert platforms.live_url_of(
        "https://youtube.com/@ch/live") == "https://youtube.com/@ch/live"
    # Twitch/Kick 的頻道網址本身就是直播頁
    assert platforms.live_url_of(
        "https://twitch.tv/someone") == "https://twitch.tv/someone"
    assert platforms.live_url_of("https://kick.com/x") == "https://kick.com/x"


def test_watch_url_with_seek():
    yt = platforms.watch_url("https://youtube.com/@ch", "abc123", 125)
    assert yt == "https://www.youtube.com/watch?v=abc123&t=125s"
    # 沒給秒數就是純觀看網址
    assert platforms.watch_url("https://youtube.com/@ch", "abc123") == \
        "https://www.youtube.com/watch?v=abc123"
    tw = platforms.watch_url("https://twitch.tv/x", "v9", 90)
    assert tw == "https://www.twitch.tv/videos/v9?t=90s"
    # 平台不支援觀看網址 / 無影片 ID → 空字串,前端據此不顯示連結
    assert platforms.watch_url("https://kick.com/x", "id1") == ""
    assert platforms.watch_url("https://youtube.com/@ch", "") == ""


def test_embed_url():
    assert platforms.embed_url("https://youtube.com/@ch", "abc") == \
        "https://www.youtube.com/embed/abc?autoplay=1"
    # Twitch 嵌入必須帶 parent 參數才能播放
    tw = platforms.embed_url("https://twitch.tv/x", "v9")
    assert "player.twitch.tv" in tw and "parent=localhost" in tw
    assert platforms.embed_url("https://kick.com/x", "id1") == ""
