from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "targets.yaml"
CATEGORIES_PATH = ROOT / "config" / "categories.yaml"
STATE_PATH = ROOT / "data" / "state.json"
STATE_BAK_PATH = ROOT / "data" / "state.json.bak"
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH, override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recruit-watch")

# ページ本文から募集文脈を抽出する際に優先するキーワード
RECRUIT_KEYWORDS = [
    "募集", "採用", "インターン", "インターンシップ", "エントリー", "応募",
    "新卒", "26卒", "27卒", "28卒", "締切",
]


@dataclass
class Result:
    target_id: str
    company: str
    category: str
    url: str
    status: str
    score: int
    reasons: List[str]
    excerpt: str
    content_hash: str
    checked_at: str
    deadline: Optional[str] = None
    posting_type: Optional[str] = None
    ai_used: bool = False
    error: Optional[str] = None


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_yaml(path: Path) -> Dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_taxonomy() -> Dict[str, Any]:
    return load_yaml(CATEGORIES_PATH)


def valid_category_ids(taxonomy: Dict[str, Any]) -> List[str]:
    return list((taxonomy.get("categories") or {}).keys())


def category_labels(taxonomy: Dict[str, Any]) -> Dict[str, str]:
    return taxonomy.get("categories") or {}


def valid_posting_types(taxonomy: Dict[str, Any]) -> List[str]:
    return list((taxonomy.get("posting_types") or {}).keys())


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"targets": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.warning("state.jsonを読み込めないため初期化します")
        return {"targets": {}}


def save_state(results: List[Result]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    old = load_state().get("targets", {})
    for r in results:
        old[r.target_id] = asdict(r)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "targets": old}
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def map_program_type_to_posting_type(program_type: Optional[str]) -> str:
    mapping = {
        "internship": "internship",
        "selection": "newgrad",
        "open_company": "other",
        "unknown": "unknown",
    }
    return mapping.get(program_type or "", "unknown")


def migrate_state() -> None:
    """旧キー program_type を posting_type に移行し、初回のみバックアップを保存する。"""
    if not STATE_PATH.exists():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return
    targets = data.get("targets")
    if not isinstance(targets, dict):
        return
    changed = False
    for value in targets.values():
        if isinstance(value, dict) and "posting_type" not in value and "program_type" in value:
            value["posting_type"] = map_program_type_to_posting_type(value.get("program_type"))
            changed = True
    if not changed:
        return
    if not STATE_BAK_PATH.exists():
        shutil.copyfile(STATE_PATH, STATE_BAK_PATH)
        log.info("state.jsonを data/state.json.bak にバックアップしました")
    STATE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def normalize_text(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", html.unescape(soup.get_text(" ", strip=True)))


def fetch_text(url: str, timeout: int, user_agent: str) -> str:
    response = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": user_agent, "Accept-Language": "ja,en-US;q=0.8,en;q=0.6"},
        allow_redirects=True,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or response.encoding
    return normalize_text(response.text)


def contains_any(text_lower: str, words: List[str]) -> List[str]:
    return [w for w in words if w.lower() in text_lower]


def build_excerpt(text: str, matched: List[str], width: int = 900) -> str:
    positions = [text.lower().find(w.lower()) for w in matched]
    positions = [p for p in positions if p >= 0]
    start = max(0, (min(positions) if positions else 0) - 100)
    excerpt = text[start : start + width]
    return excerpt + ("…" if start + width < len(text) else "")


def heuristic_classify(text: str, target: Dict[str, Any], settings: Dict[str, Any]) -> Tuple[str, int, List[str], str]:
    lower = text.lower()
    grad_year = str(os.getenv("GRADUATION_YEAR", settings.get("graduation_year", 2028)))
    year_terms = [f"{grad_year}卒", f"{grad_year}年卒", f"{grad_year}年度", "28卒"]
    role_terms = settings.get("role_keywords", {}).get(target["category"], [])
    positive_terms = settings.get("positive_keywords", [])
    negative_terms = settings.get("negative_keywords", [])
    required_any = target.get("required_any", [])

    year_hits = contains_any(lower, year_terms)
    role_hits = contains_any(lower, role_terms)
    positive_hits = contains_any(lower, positive_terms)
    negative_hits = contains_any(lower, negative_terms)
    required_hits = contains_any(lower, required_any)

    score = 0
    reasons: List[str] = []
    if year_hits:
        score += 4; reasons.append("対象卒年: " + ", ".join(year_hits[:3]))
    if role_hits:
        score += 3; reasons.append("対象職種: " + ", ".join(role_hits[:3]))
    if positive_hits:
        score += 2; reasons.append("募集語句: " + ", ".join(positive_hits[:4]))
    if required_any:
        if required_hits:
            score += 2; reasons.append("個別条件: " + ", ".join(required_hits[:3]))
        else:
            score -= 4; reasons.append("個別条件が未検出")
    if negative_hits:
        score -= 5; reasons.append("終了語句: " + ", ".join(negative_hits[:3]))

    status = "open" if score >= 7 and not negative_hits else "possible" if score >= 3 else "closed_or_unknown"
    matched = year_hits + role_hits + positive_hits + required_hits + negative_hits
    return status, score, reasons, build_excerpt(text, matched)


def extract_json_object(raw: str) -> Dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def reduce_text(text: str, max_chars: int) -> str:
    """募集キーワード周辺を優先しつつ max_chars に切り詰める。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    lower = text.lower()
    first: Optional[int] = None
    for keyword in RECRUIT_KEYWORDS:
        idx = lower.find(keyword.lower())
        if idx >= 0 and (first is None or idx < first):
            first = idx
    start = 0
    if first is not None:
        start = max(0, first - 200)
    return text[start : start + max_chars]


def build_batch_items(fetched: List[Tuple[Dict[str, Any], str]], max_chars_per_target: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for target, text in fetched:
        items.append({
            "id": target["id"],
            "company": target["company"],
            "category": target["category"],
            "url": target["url"],
            "text": reduce_text(text, max_chars_per_target),
        })
    return items


def chunk_chars(chunk: List[Dict[str, Any]]) -> int:
    return sum(len(json.dumps(item, ensure_ascii=False)) for item in chunk)


def build_batch_chunks(items: List[Dict[str, Any]], max_total_chars: int) -> List[List[Dict[str, Any]]]:
    """合計サイズが max_total_chars を超えないよう最小個数のチャンクに分割する。"""
    chunks: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    current_chars = 0
    for item in items:
        item_chars = len(json.dumps(item, ensure_ascii=False))
        if current and current_chars + item_chars > max_total_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        chunks.append(current)
    return chunks


def deepseek_config(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "api_key": os.getenv("DEEPSEEK_API_KEY", "").strip(),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro",
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        "timeout": int(settings.get("deepseek_timeout_seconds", 90)),
    }


def build_payload(items: List[Dict[str, Any]], settings: Dict[str, Any], ai_settings: Dict[str, Any], grad_year: str) -> Dict[str, Any]:
    cfg = deepseek_config(settings)
    system_prompt = (
        f"あなたは日本の新卒採用ページを判定する監査役です。対象卒年は{grad_year}年卒です。"
        "与えられたJSON配列の各候補について、ページ本文だけを根拠に判定してください。"
        "説明文やMarkdownコードブロックは付けず、JSONオブジェクトを1つだけ返してください。\n"
        "判定基準:\n"
        "- is_open: 現在応募可能なインターン・オープンカンパニー・本選考（新卒）が掲載されていれば true、"
        "過年度・終了済み・会社説明のみなら false。\n"
        "- posting_type: internship（インターン）/ newgrad（新卒本選考）/ midcareer（中途）/ other（その他）/ unknown（判定不能）のいずれか。\n"
        "- deadline: 応募締切が不明なら null。\n"
        "- confidence: 判定の確信度を 0.0〜1.0 の小数で。\n"
        "\n出力形式:\n"
        '{"results": [{"id": "...", "is_open": true, "posting_type": "internship", "title": "...", "deadline": "...", "confidence": 0.0, "reason": "..."}]}\n'
        "各入力idに対して結果を必ず1件だけ返し、idは入力値をそのまま出力してください。"
    )
    user_content = json.dumps(items, ensure_ascii=False)
    return {
        "model": cfg["model"],
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
        "response_format": {"type": "json_object"},
        # この用途では思考モードを切る。JSONだけ返す単純分類なので、料金と不安定要因を減らす。
        "thinking": {"type": "disabled"},
        "max_tokens": int(ai_settings.get("max_tokens", 8192)),
        "stream": False,
    }


def deepseek_post_chunk(
    items: List[Dict[str, Any]],
    settings: Dict[str, Any],
    ai_settings: Dict[str, Any],
    grad_year: str,
    attempts_counter: Optional[List[int]] = None,
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    cfg = deepseek_config(settings)
    if not cfg["api_key"]:
        raise RuntimeError("DEEPSEEK_API_KEYが未設定です")
    payload = build_payload(items, settings, ai_settings, grad_year)

    # DeepSeek公式ドキュメント上、JSON Outputはまれに空contentを返すことがある。
    # そのため空応答・壊れたJSONは最大3回まで再試行する。
    last_error: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            response = requests.post(
                f"{cfg['base_url']}/chat/completions",
                headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json"},
                json=payload,
                timeout=cfg["timeout"],
            )
            if attempts_counter is not None:
                attempts_counter[0] += 1
            if not response.ok:
                body = response.text[:1000].replace("\n", " ")
                raise RuntimeError(f"DeepSeek HTTP {response.status_code}: {body}")

            data = response.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError(f"DeepSeek応答にchoicesがありません: {str(data)[:700]}")
            content = (choices[0].get("message") or {}).get("content")
            if not content or not str(content).strip():
                finish_reason = choices[0].get("finish_reason")
                raise RuntimeError(f"DeepSeekが空のcontentを返しました (finish_reason={finish_reason})")

            parsed = extract_json_object(str(content))
            return parsed, data.get("usage")
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 3:
                log.warning("DeepSeek JSON応答エラー。%d/3回目を再試行します: %s", attempt, exc)
                time.sleep(1.5 * attempt)
            else:
                raise RuntimeError(f"DeepSeek応答を3回処理できませんでした: {exc}") from exc

    raise RuntimeError(f"DeepSeek判定結果が得られませんでした: {last_error}")


def parse_batch_results(parsed: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    mapping: Dict[str, Dict[str, Any]] = {}
    if not isinstance(parsed, dict):
        return mapping
    results = parsed.get("results") or []
    if isinstance(results, dict):
        results = []
    for entry in results:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if entry_id is not None:
            mapping[str(entry_id)] = entry
    return mapping


def merge_usage(total: Dict[str, int], usage: Optional[Dict[str, Any]]) -> None:
    if not isinstance(usage, dict):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


def deepseek_classify_batch(
    items: List[Dict[str, Any]],
    settings: Dict[str, Any],
    ai_settings: Dict[str, Any],
    grad_year: str,
    post_fn: Optional[Any] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """全候補を1回（必要なら最小チャンク数）のPOSTで判定する。

    post_fn はテスト用の注入ポイントで、chunk(項目リスト)を受け取り
    (parsed_dict, usage_dict) を返す。None なら実APIを呼ぶ。
    """
    attempts_counter: List[int] = [0]
    if post_fn is None:
        post_fn = lambda chunk: deepseek_post_chunk(chunk, settings, ai_settings, grad_year, attempts_counter)

    max_total = int(ai_settings.get("max_total_chars", 200000))
    chunks = build_batch_chunks(items, max_total)
    stats: Dict[str, Any] = {"posts": 0, "total_chars": 0, "latency_seconds": 0.0, "usage": {}}
    start = time.time()

    mapping: Dict[str, Dict[str, Any]] = {}
    missing: List[Dict[str, Any]] = []
    for chunk in chunks:
        stats["total_chars"] += chunk_chars(chunk)
        try:
            parsed, usage = post_fn(chunk)
        except Exception as exc:
            for item in chunk:
                mapping[str(item["id"])] = {"id": item["id"], "error": str(exc)}
            continue
        merge_usage(stats["usage"], usage)
        chunk_map = parse_batch_results(parsed)
        mapping.update(chunk_map)
        for item in chunk:
            if str(item["id"]) not in chunk_map:
                missing.append(item)

    # 成功応答にidが欠落していた場合、不足分だけ一度だけ再要求する。
    if missing:
        log.warning("DeepSeek応答に %d 件のidが欠落していたため、不足分のみ再要求します", len(missing))
        stats["total_chars"] += chunk_chars(missing)
        try:
            parsed, usage = post_fn(missing)
            merge_usage(stats["usage"], usage)
            chunk_map = parse_batch_results(parsed)
            mapping.update(chunk_map)
            for item in missing:
                if str(item["id"]) not in chunk_map:
                    mapping[str(item["id"])] = {"id": item["id"], "is_open": "unknown", "posting_type": "unknown", "missing": True}
        except Exception as exc:
            for item in missing:
                mapping[str(item["id"])] = {"id": item["id"], "error": str(exc)}

    stats["posts"] = attempts_counter[0] if attempts_counter[0] else (len(chunks) + (1 if missing else 0))
    stats["latency_seconds"] = round(time.time() - start, 3)
    return mapping, stats


def status_from_is_open(is_open: Any) -> str:
    if is_open is True:
        return "open"
    if is_open is False:
        return "closed_or_unknown"
    return "possible"


def confidence_percent(entry: Dict[str, Any]) -> int:
    raw = entry.get("confidence")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 50
    if value <= 1.0:
        value = value * 100
    return max(0, min(100, int(round(value))))


def ai_result_from_entry(
    target: Dict[str, Any],
    text: str,
    entry: Dict[str, Any],
    heuristic: Tuple[str, int, List[str], str],
    allowed_posting_types: List[str],
) -> Result:
    _, _, _, heuristic_excerpt = heuristic
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    if entry.get("missing"):
        return Result(
            target["id"], target["company"], target["category"], target["url"],
            "possible", 0, ["DeepSeek応答にidが欠落していたため不明"], heuristic_excerpt,
            digest, now, posting_type="unknown", ai_used=True,
        )

    status = status_from_is_open(entry.get("is_open"))
    posting_type = str(entry.get("posting_type") or "unknown")
    if posting_type not in allowed_posting_types:
        posting_type = "unknown"
    confidence = confidence_percent(entry)
    reason = str(entry.get("reason") or "").strip()
    title = str(entry.get("title") or "").strip()
    reasons = [f"DeepSeek判定信頼度: {confidence}%"]
    if reason:
        reasons.append(reason)
    excerpt = (title or heuristic_excerpt)[:1000]
    return Result(
        target["id"], target["company"], target["category"], target["url"],
        status, confidence, reasons, excerpt, digest, now,
        deadline=entry.get("deadline"), posting_type=posting_type, ai_used=True,
    )


def heuristic_result(
    target: Dict[str, Any],
    text: str,
    heuristic: Tuple[str, int, List[str], str],
    note: str,
) -> Result:
    status, score, reasons, excerpt = heuristic
    reasons = [note] + reasons
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    return Result(
        target["id"], target["company"], target["category"], target["url"],
        status, score, reasons, excerpt, digest, now, posting_type="unknown", ai_used=False,
    )


def fetch_targets(targets: List[Dict[str, Any]], settings: Dict[str, Any]) -> Tuple[List[Tuple[Dict[str, Any], str]], List[Result]]:
    fetched: List[Tuple[Dict[str, Any], str]] = []
    errors: List[Result] = []
    timeout = int(settings.get("request_timeout_seconds", 25))
    user_agent = settings.get("user_agent", "RecruitWatch/0.3")
    for target in targets:
        try:
            text = fetch_text(target["url"], timeout, user_agent)
            fetched.append((target, text))
        except Exception as exc:
            now = datetime.now(timezone.utc).isoformat()
            errors.append(Result(
                target["id"], target["company"], target["category"], target["url"],
                "error", 0, [], "", "", now, error=str(exc),
            ))
    return fetched, errors


def run_batch(
    targets: List[Dict[str, Any]],
    settings: Dict[str, Any],
    ai_settings: Dict[str, Any],
    use_ai: bool,
    taxonomy: Dict[str, Any],
) -> Tuple[List[Result], Dict[str, Any]]:
    grad_year = str(os.getenv("GRADUATION_YEAR", settings.get("graduation_year", 2028)))
    max_chars = int(ai_settings.get("max_chars_per_target", 6000))
    allowed_posting_types = valid_posting_types(taxonomy)

    fetched, results = fetch_targets(targets, settings)
    stats: Dict[str, Any] = {
        "posts": 0, "total_chars": 0, "latency_seconds": 0.0, "usage": {},
        "fetched": len(fetched), "batched": 0,
    }

    if use_ai and fetched:
        items = build_batch_items(fetched, max_chars)
        stats["batched"] = len(items)
        mapping, ai_stats = deepseek_classify_batch(items, settings, ai_settings, grad_year)
        stats.update(ai_stats)
        for target, text in fetched:
            entry = mapping.get(str(target["id"]))
            heuristic = heuristic_classify(text, target, settings)
            if entry is None:
                entry = {"id": target["id"], "is_open": "unknown", "posting_type": "unknown", "missing": True}
            if entry.get("error"):
                if bool(settings.get("require_deepseek", True)):
                    now = datetime.now(timezone.utc).isoformat()
                    results.append(Result(
                        target["id"], target["company"], target["category"], target["url"],
                        "error", 0, [], "", "", now, error=entry["error"],
                    ))
                else:
                    log.warning("%s: DeepSeek失敗のためルール判定へフォールバック: %s", target["company"], entry["error"])
                    results.append(heuristic_result(target, text, heuristic, "DeepSeek失敗のためルール判定"))
            else:
                results.append(ai_result_from_entry(target, text, entry, heuristic, allowed_posting_types))
    else:
        for target, text in fetched:
            results.append(heuristic_result(target, text, heuristic_classify(text, target, settings), "ローカルテスト: DeepSeek未使用"))

    return results, stats


def should_notify(current: Result, previous: Optional[Dict[str, Any]], notify_first: bool) -> bool:
    if current.status not in {"open", "possible"}:
        return False
    if not previous:
        return notify_first
    return (
        previous.get("status") not in {"open", "possible"}
        or (previous.get("status") == "possible" and current.status == "open")
        or previous.get("deadline") != current.deadline
        or previous.get("content_hash") != current.content_hash
    )


def posting_type_notifiable(posting_type: Optional[str], notify_posting_types: List[str], notify_unknown: bool) -> bool:
    if posting_type in notify_posting_types:
        return True
    if posting_type == "unknown" and notify_unknown:
        return True
    return False


def discord_post(results: List[Result], webhook_url: str, max_items: int, labels: Dict[str, str]) -> None:
    posting_labels = {"internship": "インターン", "newgrad": "本選考", "midcareer": "中途", "other": "その他", "unknown": "不明"}
    for chunk_start in range(0, min(len(results), max_items), 10):
        embeds = []
        for r in results[chunk_start:chunk_start + 10]:
            fields = [
                {"name": "分野", "value": labels.get(r.category, r.category), "inline": True},
                {"name": "判定値", "value": f"{r.score}%" if r.ai_used else str(r.score), "inline": True},
                {"name": "種別", "value": posting_labels.get(r.posting_type, r.posting_type or "不明"), "inline": True},
            ]
            if r.deadline:
                fields.append({"name": "締切", "value": str(r.deadline), "inline": False})
            fields.append({"name": "根拠", "value": "\n".join(r.reasons)[:1000] or "なし", "inline": False})
            embeds.append({"title": f"{r.company}｜{'募集中' if r.status == 'open' else '要確認'}", "url": r.url, "description": r.excerpt[:1000] or "公式ページを確認してください。", "fields": fields, "footer": {"text": "自動判定です。応募前に公式情報を確認してください。"}})
        resp = requests.post(webhook_url, json={"content": "📣 28卒向け募集情報を検出しました", "embeds": embeds}, timeout=20)
        resp.raise_for_status()


def validate_config(targets: List[Dict[str, Any]], taxonomy: Dict[str, Any], profile_name: Optional[str] = None) -> List[str]:
    errors: List[str] = []
    category_ids = set(valid_category_ids(taxonomy))
    posting_type_ids = set(valid_posting_types(taxonomy))
    profiles = taxonomy.get("profiles") or {}

    ids: set = set()
    for i, t in enumerate(targets, 1):
        for key in ("id", "company", "url"):
            if not t.get(key):
                errors.append(f"target #{i}: {key} がありません")
        target_id = t.get("id")
        if target_id in ids:
            errors.append(f"ID重複: {target_id}")
        ids.add(target_id)
        category = t.get("category")
        if category:
            if category not in category_ids:
                errors.append(f"不明なcategory: {target_id} (category={category})")
        else:
            errors.append(f"カテゴリに属していないtarget: {target_id}")
        if t.get("url") and not str(t["url"]).startswith(("http://", "https://")):
            errors.append(f"不正URL: {target_id}")

    for profile_key, profile in profiles.items():
        for category in profile.get("categories", []):
            if category not in category_ids:
                errors.append(f"プロファイル {profile_key}: 不明なcategory {category}")
        for posting_type in profile.get("notify_posting_types", []):
            if posting_type not in posting_type_ids:
                errors.append(f"プロファイル {profile_key}: 不明なposting_type {posting_type}")
        for category in profile.get("categories", []):
            if category in category_ids and not any(t.get("category") == category for t in targets):
                errors.append(f"プロファイル {profile_key} はカテゴリ {category} を参照していますが対象が0件です")

    if profile_name and profile_name not in profiles:
        errors.append(f"不明なプロファイル名: {profile_name}")
    return errors


def parse_categories_arg(raw: Optional[List[str]]) -> List[str]:
    result: List[str] = []
    for value in raw or []:
        for part in value.split(","):
            part = part.strip()
            if part and part not in result:
                result.append(part)
    return result


def select_targets(
    all_targets: List[Dict[str, Any]],
    categories: Optional[List[str]] = None,
    target_id: Optional[str] = None,
    limit: int = 0,
) -> List[Dict[str, Any]]:
    targets = [t for t in all_targets if t.get("enabled", True) and t.get("tier", "major") == "major"]
    if categories is not None:
        category_set = set(categories)
        targets = [t for t in targets if t.get("category") in category_set]
    if target_id:
        targets = [t for t in targets if t.get("id") == target_id]
    if limit > 0:
        targets = targets[:limit]
    return targets


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="28卒採用監視")
    p.add_argument("--category", action="append", help="対象カテゴリ。繰り返し指定とカンマ区切りに対応（例: --category quant,actuary）")
    p.add_argument("--profile", help="実行プロファイル名（例: daily）")
    p.add_argument("--target", help="target idを1件指定")
    p.add_argument("--limit", type=int, default=0, help="先頭N件のみ。Macローカルテスト向け")
    p.add_argument("--no-ai", action="store_true", help="DeepSeekを呼ばず無料で接続・判定テスト")
    p.add_argument("--no-discord", action="store_true", help="Discord送信を抑止")
    p.add_argument("--dry-run", action="store_true", help="バッチ組み立てのみ確認（DeepSeek・Discordへは送信しない）")
    p.add_argument("--validate-config", action="store_true", help="設定だけ検証")
    p.add_argument("--list", action="store_true", help="対象一覧を表示")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(CONFIG_PATH)
    taxonomy = load_taxonomy()
    settings = config.get("settings", {})
    ai_settings = config.get("ai", {})

    all_targets = config.get("targets", [])
    errors = validate_config(all_targets, taxonomy, args.profile)
    if args.validate_config:
        if errors:
            for e in errors:
                log.error(e)
            return 1
        log.info(
            "設定検証OK: %d targets / %d categories / %d profiles",
            len(all_targets), len(valid_category_ids(taxonomy)), len(taxonomy.get("profiles", {})),
        )
        return 0

    categories = parse_categories_arg(args.category) if args.category else None
    notify_posting_types: Optional[List[str]] = None
    if args.profile:
        profile = taxonomy.get("profiles", {}).get(args.profile)
        if not profile:
            log.error("不明なプロファイル名: %s", args.profile)
            return 1
        if not categories:
            categories = list(profile.get("categories", []))
        notify_posting_types = profile.get("notify_posting_types")
    if notify_posting_types is None:
        notify_posting_types = settings.get("notify_posting_types", ["internship", "newgrad"])
    notify_unknown = bool(settings.get("notify_unknown_posting_type", True))

    targets = select_targets(all_targets, categories, args.target, args.limit)

    if args.list:
        for t in targets:
            print(f"{t['id']}\t{t['category']}\t{t['company']}\t{t['url']}")
        return 0
    if not targets:
        log.warning("実行対象がありません")
        return 0

    if args.dry_run:
        fetched, _ = fetch_targets(targets, settings)
        items = build_batch_items(fetched, int(ai_settings.get("max_chars_per_target", 6000)))
        chunks = build_batch_chunks(items, int(ai_settings.get("max_total_chars", 200000)))
        log.info(
            "dry-run: 対象=%d / 取得=%d / バッチ項目=%d / 送信文字数=%d / チャンク数=%d（=POST数）",
            len(targets), len(fetched), len(items), sum(chunk_chars(c) for c in chunks), len(chunks),
        )
        return 0

    migrate_state()
    previous_state = load_state().get("targets", {})
    use_ai = env_bool("DEEPSEEK_ENABLED", True) and not args.no_ai

    results, stats = run_batch(targets, settings, ai_settings, use_ai, taxonomy)

    notify_first = bool(settings.get("notify_on_first_detection", False))
    notifications = [
        r for r in results
        if should_notify(r, previous_state.get(r.target_id), notify_first)
        and posting_type_notifiable(r.posting_type, notify_posting_types, notify_unknown)
    ]

    for r in results:
        log.info(
            "%s [%s]: %s score=%s ai=%s posting_type=%s %s",
            r.company, r.category, r.status, r.score, r.ai_used, r.posting_type or "-", r.error or "",
        )

    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if notifications and webhook and not args.no_discord:
        discord_post(notifications, webhook, int(settings.get("max_discord_items", 40)), category_labels(taxonomy))
    elif notifications:
        log.info("通知候補%d件（Discord送信なし）", len(notifications))

    save_state(results)

    log.info(
        "取得:%d件 バッチ投入:%d件 送信文字数:%d DeepSeekPOST:%d回 AI所要:%.2fs tokens:%s",
        stats.get("fetched", 0), stats.get("batched", 0), stats.get("total_chars", 0),
        stats.get("posts", 0), stats.get("latency_seconds", 0.0), stats.get("usage", {}),
    )

    error_count = sum(1 for r in results if r.status == "error")
    if error_count:
        log.warning(
            "%d件の取得・判定エラーがありましたが、他の監視対象は正常に処理されたため実行を継続します。",
            error_count,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
