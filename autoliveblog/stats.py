"""API 用量統計:呼叫數、token、金額(每日,持久化到 usage_stats.json)。"""
import json
import threading
from datetime import date
from pathlib import Path

from . import config

# 每百萬 token 美元價;audio_per_min 為轉錄每分鐘美元價
PRICES = {
    "gemini": {"in": 0.10, "out": 0.40},          # flash-lite 付費層等值
    "openai": {"in": 0.15, "out": 0.60,           # gpt-4o-mini
               "audio_per_min": 0.003},           # gpt-4o-mini-transcribe
}

_FILE = Path(config.PROJECT_ROOT) / "usage_stats.json"
_lock = threading.Lock()


def _fresh() -> dict:
    return {"day": str(date.today()), "calls": 0, "retries_429": 0,
            "retries_503": 0, "failures": 0,
            "gemini": {"calls": 0, "in_tokens": 0, "out_tokens": 0},
            "openai": {"calls": 0, "in_tokens": 0, "out_tokens": 0,
                       "audio_seconds": 0.0}}


def _load() -> dict:
    if _FILE.exists():
        try:
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            if d.get("day") == str(date.today()):
                base = _fresh()
                base.update({k: d[k] for k in d if k in base and
                             not isinstance(base[k], dict)})
                for p in ("gemini", "openai"):
                    base[p].update(d.get(p) or {})
                return base
        except Exception:
            pass
    return _fresh()


_data = _load()


def _roll():
    global _data
    if _data["day"] != str(date.today()):
        _data = _fresh()


def _save():
    try:
        _FILE.write_text(json.dumps(_data, ensure_ascii=False),
                         encoding="utf-8")
    except OSError:
        pass


def record_call(provider: str = "gemini"):
    with _lock:
        _roll()
        _data["calls"] += 1
        if provider in _data:
            _data[provider]["calls"] += 1
        _save()


def record_usage(provider: str, in_tokens: int = 0, out_tokens: int = 0,
                 audio_seconds: float = 0.0):
    with _lock:
        _roll()
        p = _data.get(provider)
        if not p:
            return
        p["in_tokens"] += int(in_tokens or 0)
        p["out_tokens"] += int(out_tokens or 0)
        if "audio_seconds" in p:
            p["audio_seconds"] += float(audio_seconds or 0)
        _save()


def record_retry(message: str):
    with _lock:
        _roll()
        if "429" in message or "RESOURCE_EXHAUSTED" in message:
            _data["retries_429"] += 1
        else:
            _data["retries_503"] += 1
        _save()


def record_failure():
    with _lock:
        _roll()
        _data["failures"] += 1
        _save()


def _cost(provider: str) -> float:
    p = _data[provider]
    pr = PRICES[provider]
    usd = (p["in_tokens"] * pr["in"] + p["out_tokens"] * pr["out"]) / 1_000_000
    if "audio_per_min" in pr:
        usd += p.get("audio_seconds", 0) / 60 * pr["audio_per_min"]
    return round(usd, 4)


def snapshot() -> dict:
    with _lock:
        _roll()
        out = json.loads(json.dumps(_data))
        out["gemini"]["usd_equivalent"] = _cost("gemini")  # 免費層實付 $0
        out["openai"]["usd"] = _cost("openai")
        # 舊欄位相容
        out["calls_gemini"] = out["gemini"]["calls"]
        out["calls_openai"] = out["openai"]["calls"]
        return out
