"""翻譯層的回歸測試。

這裡的每個測試都對應一個真的發生過、而且不會當掉的失敗:
翻譯缺 key 時 t() 只是回傳 key 本身,程式照跑,壞掉的地方要到
使用者眼前才看得到 —— 所以只能靠測試在 CI 擋下來。
"""
import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autoliveblog import i18n
from autoliveblog.summarizer import _QUOTA_MARKERS, AutoSummarizer

PKG = Path(__file__).resolve().parent.parent / "autoliveblog"


def _placeholders(text: str) -> set[str]:
    return set(re.findall(r"{(\w+)}", text))


def _python_call_sites() -> list[tuple[str, int, str, set[str]]]:
    """掃出所有 t("key", kw=...) 呼叫點:(檔名, 行號, key, 傳入的參數名)。"""
    sites = []
    for path in PKG.rglob("*.py"):
        if path.name == "i18n.py":
            continue
        # 有些檔案帶 BOM,ast.parse 吃到 U+FEFF 會炸,所以用 utf-8-sig
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "t" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                passed = {kw.arg for kw in node.keywords if kw.arg}
                sites.append((path.name, node.lineno, node.args[0].value, passed))
    return sites


def test_catalogs_have_identical_keys():
    assert set(i18n.EN) == set(i18n.ZH_TW)


def test_placeholders_match_across_locales():
    """翻譯把 {title} 打成 {tilte} 不會當掉,只會少一段字。"""
    for key, en in i18n.EN.items():
        assert _placeholders(en) == _placeholders(i18n.ZH_TW[key]), key


def test_no_positional_placeholders():
    """位置參數 {} 在翻譯裡對不起來,一律用具名的。"""
    for cat in (i18n.EN, i18n.ZH_TW):
        for key, text in cat.items():
            assert not re.search(r"{\d*}", text), key


@pytest.mark.parametrize("name,lineno,key,passed", _python_call_sites())
def test_call_site_key_exists_and_args_match(name, lineno, key, passed):
    """程式用到的 key 一定要在目錄裡,而且參數名要對得上。

    key 不存在時 t() 會連 kwargs 一起丟掉 —— 對 engine.* 來說,被丟掉的
    正是 {err} 裡原廠的錯誤字串,而 AutoSummarizer 就是靠那串字判斷要不要
    切換備援引擎。一個缺的 key 會讓額度自動切換整個失效。
    """
    assert key in i18n.EN, f"{name}:{lineno} 用了未定義的 key {key}"
    assert passed == _placeholders(i18n.EN[key]), f"{name}:{lineno} {key}"


def test_every_string_renders_in_both_locales():
    args = {k: "X" for k in set().union(*(_placeholders(v)
                                          for v in i18n.EN.values()))}
    original = i18n.get_lang()
    try:
        for lang in i18n.CATALOGS:
            i18n.set_lang(lang)
            for key in i18n.EN:
                out = i18n.t(key, **args)
                assert "{" not in out, f"{lang} {key} 有沒填的佔位符"
                assert out != key, f"{lang} {key} 沒有對應文字"
    finally:
        i18n.set_lang(original)


def test_missing_key_falls_back_to_english():
    original = i18n.get_lang()
    try:
        i18n.set_lang("zh-TW")
        # 只存在於 EN 的 key 應該退回英文,而不是回傳 key 本身
        i18n.EN["test.only_english"] = "English only {x}"
        assert i18n.t("test.only_english", x=1) == "English only 1"
    finally:
        i18n.EN.pop("test.only_english", None)
        i18n.set_lang(original)


def test_engine_texts_do_not_contain_failover_markers():
    """引擎錯誤訊息的固定文字不能誤觸自動切換備援的判斷字串。

    AutoSummarizer 是比對「錯誤訊息的文字」決定要不要切到 OpenAI、或把備援
    標記成沒額度。目錄文字若自己就含有 quota / billing 之類的字,等於憑空
    捏造一個引擎狀態。真正的依據只能來自 {err} 裡原廠的錯誤字串。
    """
    for lang, cat in i18n.CATALOGS.items():
        for key, text in cat.items():
            if not key.startswith("engine."):
                continue
            for marker in _QUOTA_MARKERS:
                assert marker not in text, f"{lang} {key} 含有切換標記 {marker}"
            assert not AutoSummarizer._is_dead_credit_error(text), \
                f"{lang} {key} 會被誤判為餘額耗盡"


def test_provider_error_still_reaches_the_router():
    """把原廠錯誤包進翻譯後,判斷字串仍要看得到。"""
    original = i18n.get_lang()
    try:
        for lang in i18n.CATALOGS:
            i18n.set_lang(lang)
            msg = i18n.t("engine.gemini_failed",
                         err="429 RESOURCE_EXHAUSTED: quota exceeded")
            assert any(m in msg for m in _QUOTA_MARKERS), lang
            dead = i18n.t("engine.openai_failed",
                          err="insufficient_quota: credit balance is 0")
            assert AutoSummarizer._is_dead_credit_error(dead), lang
    finally:
        i18n.set_lang(original)
