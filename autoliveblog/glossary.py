"""頻道專有名詞辭典:修正語音辨識的同音字(樺漢/華漢、佳必琪/嘉碧奇)。

glossary.json 格式:{"頻道名或關鍵字": ["樺漢(6414)", "佳必琪(6197)", ...]}
頻道比對為雙向包含(存「游庭皓」即可命中「游庭皓的財經皓角」)。
"""
import json
import threading

from . import config

FILE = config.PROJECT_ROOT / "glossary.json"
_lock = threading.Lock()


def load_all() -> dict[str, list[str]]:
    if FILE.exists():
        try:
            return json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def add_terms(channel: str, terms: list[str]) -> None:
    with _lock:
        data = load_all()
        cur = data.setdefault(channel.strip(), [])
        for t in terms:
            if t and t not in cur:
                cur.append(t)
        FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                        encoding="utf-8")


def terms_for(channel: str) -> list[str]:
    """取得某頻道適用的詞(頻道名雙向包含比對;'*' 鍵套用到所有頻道)。"""
    if not channel:
        return []
    out: list[str] = []
    for key, terms in load_all().items():
        if key == "*" or key in channel or channel in key:
            out.extend(t for t in terms if t not in out)
    return out[:40]
