"""平台適配層:把「頻道網址 → 開播檢查網址」「影片 ID → 觀看/嵌入網址」
這些各站不同的規則集中在一處,核心管線(下載、切段、總結)本來就靠 yt-dlp 通吃。

新增平台只要在 PLATFORMS 加一筆,不必動 server / bot / 前端。
"""
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class Platform:
    key: str
    label: str
    hosts: tuple[str, ...]
    # 頻道網址 → 直播檢查網址的模板;{base} 為去除參數後的頻道網址
    live_url_template: str
    # 影片 ID → 觀看網址 / 嵌入網址;None 表示不支援該功能
    watch_url_template: str | None = None
    embed_url_template: str | None = None
    # 觀看網址附加時間戳的模板(秒);None 表示該平台不支援跳轉
    seek_param_template: str | None = None


PLATFORMS: tuple[Platform, ...] = (
    Platform(
        key="youtube",
        label="YouTube",
        hosts=("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"),
        live_url_template="{base}/live",
        watch_url_template="https://www.youtube.com/watch?v={id}",
        embed_url_template="https://www.youtube.com/embed/{id}?autoplay=1",
        seek_param_template="&t={seconds}s",
    ),
    Platform(
        key="twitch",
        label="Twitch",
        hosts=("twitch.tv", "www.twitch.tv", "m.twitch.tv"),
        # Twitch 的頻道網址本身就是直播頁,不需要額外後綴
        live_url_template="{base}",
        watch_url_template="https://www.twitch.tv/videos/{id}",
        # Twitch 嵌入需要 parent 網域參數才能播放
        embed_url_template=(
            "https://player.twitch.tv/?video={id}&parent=localhost"
            "&parent=127.0.0.1&autoplay=true"
        ),
        seek_param_template="?t={seconds}s",
    ),
    Platform(
        key="kick",
        label="Kick",
        hosts=("kick.com", "www.kick.com"),
        live_url_template="{base}",
    ),
)

_GENERIC = Platform(
    key="generic",
    label="其他",
    hosts=(),
    live_url_template="{base}",
)


def clean_url(url: str) -> str:
    """去掉查詢參數、錨點與尾斜線(?si= 這類追蹤參數會讓網址拼接壞掉)。"""
    return url.split("?")[0].split("#")[0].rstrip("/")


def detect(url: str) -> Platform:
    """依網址主機名判斷平台;未知平台回傳通用適配(核心管線仍可運作)。"""
    host = (urlparse(url if "//" in url else f"https://{url}").hostname or "").lower()
    if host.startswith("www."):
        host_alt = host[4:]
    else:
        host_alt = host
    for p in PLATFORMS:
        if host in p.hosts or host_alt in p.hosts:
            return p
    return _GENERIC


def live_url_of(channel_url: str) -> str:
    """頻道網址 → 用來檢查是否開播的網址。"""
    base = clean_url(channel_url)
    p = detect(base)
    if p.live_url_template == "{base}/live" and base.endswith("/live"):
        return base
    return p.live_url_template.format(base=base)


def watch_url(url_or_platform, video_id: str, seconds: int | None = None) -> str:
    """影片 ID → 觀看網址(可帶時間戳)。第一個參數可以是原始網址或 Platform。"""
    p = url_or_platform if isinstance(url_or_platform, Platform) \
        else detect(url_or_platform)
    if not p.watch_url_template or not video_id:
        return ""
    url = p.watch_url_template.format(id=video_id)
    if seconds and p.seek_param_template:
        url += p.seek_param_template.format(seconds=int(seconds))
    return url


def embed_url(url_or_platform, video_id: str) -> str:
    """影片 ID → 內嵌播放器網址;平台不支援時回傳空字串。"""
    p = url_or_platform if isinstance(url_or_platform, Platform) \
        else detect(url_or_platform)
    if not p.embed_url_template or not video_id:
        return ""
    return p.embed_url_template.format(id=video_id)
