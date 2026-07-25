"""核心純函式的單元測試:字幕解析、秒數解析、辭典、工具函式、費用計算。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoliveblog import glossary, stats
from autoliveblog.live import _fmt_elapsed, _safe_name
from autoliveblog.subtitles import parse_vtt, to_transcript
from autoliveblog.summarizer import _parse_seconds_list

VTT = """WEBVTT
Kind: captions
Language: zh-TW

00:00:01.000 --> 00:00:03.000
第一句話

00:00:03.000 --> 00:00:05.000
第一句話
第二句話

00:00:05.000 --> 00:00:07.500
第二句話
第三句話

00:01:10.000 --> 00:01:12.000
<c>帶標籤的</c>句子
"""


def test_parse_vtt_dedupes_rolling_captions(tmp_path):
    p = tmp_path / "test.vtt"
    p.write_text(VTT, encoding="utf-8")
    cues = parse_vtt(p)
    joined = " ".join(text for _, text in cues)
    assert joined.count("第一句話") == 1
    assert joined.count("第二句話") == 1
    assert joined.count("第三句話") == 1
    assert "帶標籤的句子" in joined  # HTML 標籤要剝掉
    assert cues[0][0] == pytest.approx(1.0)


def test_to_transcript_inserts_time_markers(tmp_path):
    p = tmp_path / "test.vtt"
    p.write_text(VTT, encoding="utf-8")
    text = to_transcript(parse_vtt(p), marker_interval=30)
    assert "[0:01]" in text
    assert "[1:10]" in text  # 超過 30 秒間隔要有新標記


def test_parse_seconds_list_tolerates_formats():
    assert _parse_seconds_list([45, "90", "1:30", "abc", 999], 120) == [45, 90, 90]
    assert _parse_seconds_list(None, 120) == []
    assert _parse_seconds_list([], 120) == []
    assert _parse_seconds_list([0, 120], 120) == [0, 120]  # 邊界值含端點


def test_fmt_elapsed():
    assert _fmt_elapsed(0) == "0:00"
    assert _fmt_elapsed(125) == "2:05"
    assert _fmt_elapsed(3661) == "1:01:01"


def test_safe_name_strips_illegal_chars():
    assert "/" not in _safe_name('a/b\\c:d*e?f"g<h>i|j')
    assert len(_safe_name("超" * 100, limit=60)) <= 60


def test_glossary_matching(tmp_path, monkeypatch):
    monkeypatch.setattr(glossary, "FILE", tmp_path / "g.json")
    glossary.add_terms("財經頻道", ["樺漢(6414)"])
    glossary.add_terms("*", ["台積電(2330)"])
    terms = glossary.terms_for("某某財經頻道日報")
    assert "樺漢(6414)" in terms      # 雙向包含比對
    assert "台積電(2330)" in terms    # 萬用鍵
    assert glossary.terms_for("") == []
    # 重複加入不重複儲存
    glossary.add_terms("財經頻道", ["樺漢(6414)"])
    assert glossary.load_all()["財經頻道"].count("樺漢(6414)") == 1


def test_stats_cost_math(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(stats, "_data", stats._fresh())
    stats.record_usage("openai", in_tokens=1_000_000, out_tokens=0,
                       audio_seconds=600)
    snap = stats.snapshot()
    # 1M 輸入 tokens × $0.15 + 10 分鐘轉錄 × $0.003
    assert snap["openai"]["usd"] == pytest.approx(0.15 + 0.03)
    stats.record_usage("gemini", in_tokens=0, out_tokens=1_000_000)
    snap = stats.snapshot()
    # 1M 輸出 tokens × flash-lite 出價
    assert snap["gemini"]["usd_equivalent"] == pytest.approx(
        stats.PRICES["gemini"]["out"])
