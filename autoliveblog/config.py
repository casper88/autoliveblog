"""設定載入:.env、模型、輸出路徑、ffmpeg 位置偵測。"""
import glob
import os
import shutil
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# 舊前綴保留相容:專案原名 cast_yt,既有 .env 不必立刻改
_LEGACY_PREFIX = "CAST_YT_"


def env(name: str, default: str = "") -> str:
    """讀取 AUTOLIVEBLOG_<name>;找不到時回退到舊的 CAST_YT_<name>。"""
    v = os.getenv(f"AUTOLIVEBLOG_{name}")
    if v is None:
        v = os.getenv(f"{_LEGACY_PREFIX}{name}")
    return default if v is None else v


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# flash-lite 系列同樣支援音訊與圖片理解,免費層額度較寬、付費價格約 flash 的 1/3。
# 注意:gemini-2.5-flash 已對新專案下架,不要當預設值。
MODEL = env("MODEL", "gemini-3.5-flash-lite")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_STT_MODEL = env("OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")

# auto:優先 Gemini(免費),額度耗盡自動切換 OpenAI;也可指定 gemini / openai
PROVIDER = env("PROVIDER", "auto")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # 白名單,逗號分隔

# 單次任務允許自動花費的 OpenAI 轉錄上限(美元);超過就中止並要求明確同意
MAX_AUTO_SPEND_USD = float(env("MAX_AUTO_SPEND_USD", "0.25"))

# Podcast 音檔下載上限:enclosure 可能指向無限長的網路電台
MAX_AUDIO_MB = int(env("MAX_AUDIO_MB", "500"))
MAX_DOWNLOAD_SECONDS = int(env("MAX_DOWNLOAD_SECONDS", "1800"))

# 訂閱頻道的開播檢查間隔(秒);太密集會增加被 YouTube 限流的風險
SUB_POLL_SECONDS = int(env("SUB_POLL_SECONDS", "300"))

# 每日晨報:當天所有總結彙整推播;設空字串停用
DIGEST_TIME = env("DIGEST_TIME", "12:30")

# Obsidian 匯出:設定 vault 路徑後,每份總結自動複製一份過去
OBSIDIAN_VAULT = env("OBSIDIAN_VAULT", "")

# 轉錄引擎:openai(付費、快)或 local(faster-whisper,免費、較慢)
STT_PROVIDER = env("STT_PROVIDER", "openai")
# 轉錄語言:空字串=自動偵測;設 zh 可強化中文內容的辨識
STT_LANG = env("STT_LANG", "")
# 介面語言(網頁、Telegram、CLI、報告標題):en 或 zh-TW
UI_LANG = env("UI_LANG", "en")
# AI 總結輸出的語言;預設跟著介面語言走
LANG = env("LANG", "繁體中文" if UI_LANG.lower().startswith("zh") else "English")
CHUNK_SECONDS = int(env("CHUNK_SECONDS", "180"))
OUTPUT_DIR = Path(env("OUTPUT_DIR", str(PROJECT_ROOT / "summaries")))

# 轉錄文字超過此長度時改用分段(map-reduce)總結
MAX_TRANSCRIPT_CHARS = 400_000

# 音訊超過此大小改走 Files API 上傳(inline 上限 20MB)
INLINE_AUDIO_LIMIT = 15 * 1024 * 1024


def find_ffmpeg() -> str | None:
    """尋找 ffmpeg:PATH → winget Links → winget Packages。"""
    p = shutil.which("ffmpeg")
    if p:
        return p
    localappdata = os.getenv("LOCALAPPDATA", "")
    candidates = [
        os.path.join(localappdata, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
    ]
    candidates += glob.glob(
        os.path.join(
            localappdata, "Microsoft", "WinGet", "Packages",
            "Gyan.FFmpeg*", "**", "bin", "ffmpeg.exe",
        ),
        recursive=True,
    )
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


FFMPEG = find_ffmpeg()
