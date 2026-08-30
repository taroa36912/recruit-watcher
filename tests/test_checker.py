import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "src"))
import checker
from checker import (
    build_batch_chunks,
    build_batch_items,
    confidence_percent,
    deepseek_classify_batch,
    heuristic_classify,
    parse_batch_results,
    parse_categories_arg,
    posting_type_notifiable,
    reduce_text,
    select_targets,
    status_from_is_open,
    validate_config,
)

SETTINGS = {
    "graduation_year": 2028,
    "positive_keywords": ["インターン", "応募受付中", "新卒採用"],
    "negative_keywords": ["募集終了", "受付終了"],
    "role_keywords": {"quant": ["クオンツ", "金融工学"]},
}
TARGET = {"category": "quant", "required_any": ["クオンツ"]}


# --- 既存のヒューリスティック分類テスト ---
def test_heuristic_open():
    status, score, _, _ = heuristic_classify("2028年卒 クオンツ インターン 応募受付中", TARGET, SETTINGS)
    assert status == "open"
    assert score >= 7


def test_heuristic_closed():
    status, _, _, _ = heuristic_classify("2028年卒 クオンツ インターン 募集終了", TARGET, SETTINGS)
    assert status != "open"


# --- 1. バッチチャンク分割 ---
def _item(text):
    return {"id": "x", "company": "c", "category": "it", "url": "https://example.com", "text": text}


def test_batch_chunks_single_when_under_limit():
    items = [_item("あ" * 100), _item("い" * 100)]
    chunks = build_batch_chunks(items, 100000)
    assert len(chunks) == 1
    assert len(chunks[0]) == 2


def test_batch_chunks_minimal_when_over_limit():
    items = [_item("あ" * 200) for _ in range(5)]
    item_len = len(json.dumps(items[0], ensure_ascii=False))
    # 上限をちょうど2項目分にすると、最小個数（[2, 2, 1]）に分割される。
    chunks = build_batch_chunks(items, item_len * 2)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [2, 2, 1]
    # 各チャンクの直列化サイズが上限以下
    for c in chunks:
        assert sum(len(json.dumps(i, ensure_ascii=False)) for i in c) <= item_len * 2


# --- 2. テキスト削減 ---
def test_reduce_text_respects_max_chars_and_keeps_keywords():
    text = "a" * 5000 + "新卒採用 募集 エントリー" + "b" * 5000
    reduced = reduce_text(text, 6000)
    assert len(reduced) <= 6000
    assert "新卒採用" in reduced


def test_reduce_text_short_circuit():
    text = "短い本文"
    assert reduce_text(text, 6000) == text


# --- 3. 応答パーサーがidを正しく対応付ける ---
def test_parse_batch_results_maps_ids():
    parsed = {
        "results": [
            {"id": "a", "is_open": True, "posting_type": "internship"},
            {"id": "b", "is_open": False, "posting_type": "newgrad"},
        ]
    }
    mapping = parse_batch_results(parsed)
    assert mapping["a"]["posting_type"] == "internship"
    assert mapping["b"]["is_open"] is False


# --- 4. id欠落時の再要求とunknownフォールバック ---
def test_missing_id_triggers_one_requery_then_fallback():
    items = [{"id": "a", "company": "c", "category": "it", "url": "u", "text": "t"},
             {"id": "b", "company": "c", "category": "it", "url": "u", "text": "t"}]
    calls = []

    def fake_post(chunk):
        calls.append([i["id"] for i in chunk])
        if "b" in [i["id"] for i in chunk]:
            return {"results": [{"id": "b", "is_open": True, "posting_type": "newgrad"}]}, {}
        # 初回は a を欠落させる
        return {"results": []}, {}

    mapping, stats = deepseek_classify_batch(
        items, {}, {"max_total_chars": 100000}, "2028", post_fn=fake_post
    )
    assert len(calls) == 2  # 初回 + 不足分の再要求1回のみ
    assert calls[0] == ["a", "b"]
    assert calls[1] == ["a"]
    assert mapping["b"]["posting_type"] == "newgrad"
    assert mapping["a"]["missing"] is True
    assert mapping["a"]["posting_type"] == "unknown"


def test_missing_id_never_returned_is_unknown():
    items = [{"id": "a", "company": "c", "category": "it", "url": "u", "text": "t"}]
    calls = []

    def fake_post(chunk):
        calls.append(1)
        return {"results": []}, {}

    mapping, _ = deepseek_classify_batch(items, {}, {"max_total_chars": 100000}, "2028", post_fn=fake_post)
    assert len(calls) == 2  # 初回 + 再要求1回、それ以上はしない
    assert mapping["a"]["posting_type"] == "unknown"


# --- 5. カテゴリ絞り込みとプロファイル ---
def _make_targets():
    return [
        {"id": "1", "category": "quant", "tier": "major", "enabled": True, "url": "https://x", "company": "c", "required_any": []},
        {"id": "2", "category": "actuary", "tier": "major", "enabled": True, "url": "https://x", "company": "c", "required_any": []},
        {"id": "3", "category": "it", "tier": "major", "enabled": True, "url": "https://x", "company": "c", "required_any": []},
        {"id": "4", "category": "it", "tier": "minor", "enabled": True, "url": "https://x", "company": "c", "required_any": []},
    ]


def test_category_filter_exact():
    selected = select_targets(_make_targets(), ["quant", "actuary"])
    assert {t["id"] for t in selected} == {"1", "2"}


def test_parse_categories_arg_comma_and_repeat():
    assert parse_categories_arg(["quant,actuary"]) == ["quant", "actuary"]
    assert parse_categories_arg(["quant", "actuary"]) == ["quant", "actuary"]
    assert parse_categories_arg(["quant, actuary", "quant"]) == ["quant", "actuary"]


def test_daily_profile_selects_six_categories():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[1]
    taxonomy = yaml.safe_load((root / "config" / "categories.yaml").read_text(encoding="utf-8"))
    targets = yaml.safe_load((root / "config" / "targets.yaml").read_text(encoding="utf-8"))["targets"]
    daily = taxonomy["profiles"]["daily"]["categories"]
    assert daily == ["actuary", "quant", "asset_management", "data_science", "trader", "research"]
    for cat in daily:
        assert any(t["category"] == cat for t in targets), f"カテゴリ {cat} にターゲットがない"


# --- 6. 投稿種別フィルタ ---
def test_posting_type_filter_notifies_internship_newgrad():
    assert posting_type_notifiable("internship", ["internship", "newgrad"], False)
    assert posting_type_notifiable("newgrad", ["internship", "newgrad"], False)
    assert not posting_type_notifiable("midcareer", ["internship", "newgrad"], False)


def test_posting_type_filter_unknown_toggle():
    assert posting_type_notifiable("unknown", ["internship", "newgrad"], True)
    assert not posting_type_notifiable("unknown", ["internship", "newgrad"], False)


def test_status_from_is_open():
    assert status_from_is_open(True) == "open"
    assert status_from_is_open(False) == "closed_or_unknown"
    assert status_from_is_open("unknown") == "possible"


def test_confidence_percent():
    assert confidence_percent({"confidence": 0.5}) == 50
    assert confidence_percent({"confidence": 80}) == 80
    assert confidence_percent({}) == 50


# --- 7. validate-config の各エラー種別 ---
def _taxonomy():
    return {
        "categories": {"quant": "クオンツ", "actuary": "アクチュアリー", "it": "IT/SIer"},
        "posting_types": {"internship": "x", "newgrad": "x"},
        "profiles": {
            "daily": {"categories": ["quant", "actuary"], "notify_posting_types": ["internship", "newgrad"]},
        },
    }


def _target(category="quant", url="https://x.com", tid="t1"):
    return {"id": tid, "company": "c", "category": category, "url": url, "required_any": []}


def test_validate_unknown_category():
    errors = validate_config([_target(category="nosuch")], _taxonomy())
    assert any("不明なcategory" in e for e in errors)


def test_validate_unknown_posting_type():
    taxonomy = _taxonomy()
    taxonomy["profiles"]["daily"]["notify_posting_types"] = ["internship", "nope"]
    errors = validate_config([_target()], taxonomy)
    assert any("不明なposting_type" in e for e in errors)


def test_validate_unknown_profile_name():
    errors = validate_config([_target()], _taxonomy(), profile_name="nope")
    assert any("不明なプロファイル名" in e for e in errors)


def test_validate_duplicate_id():
    errors = validate_config([_target(tid="dup"), _target(tid="dup")], _taxonomy())
    assert any("ID重複" in e for e in errors)


def test_validate_no_category():
    errors = validate_config([_target(category="")], _taxonomy())
    assert any("カテゴリに属していない" in e for e in errors)


def test_validate_profile_zero_target_category():
    taxonomy = _taxonomy()
    taxonomy["profiles"]["daily"]["categories"] = ["quant", "actuary", "research"]
    taxonomy["categories"]["research"] = "研究職"
    errors = validate_config([_target(category="quant")], taxonomy)
    assert any("対象が0件です" in e for e in errors)
