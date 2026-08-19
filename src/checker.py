from __future__ import annotations

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import yaml
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config" / "targets.yaml"
STATE_PATH = ROOT / "data" / "state.json"
ENV_PATH = ROOT / ".env"
load_dotenv(ENV_PATH, override=False)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("recruit-watch")


@dataclass
class Result:
    target_id: str
    company: str
    category: str
    url: str
    status: str
    score: int
    reasons: list[str]
    excerpt: str
    content_hash: str
    checked_at: str
    deadline: str | None = None
    program_type: str | None = None
    ai_used: bool = False
    error: str | None = None


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"targets": {}}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        log.warning("state.jsonを読み込めないため初期化します")
        return {"targets": {}}


def save_state(results: list[Result]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    old = load_state().get("targets", {})
    for r in results:
        old[r.target_id] = asdict(r)
    payload = {"updated_at": datetime.now(timezone.utc).isoformat(), "targets": old}
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def contains_any(text_lower: str, words: list[str]) -> list[str]:
    return [w for w in words if w.lower() in text_lower]


def build_excerpt(text: str, matched: list[str], width: int = 900) -> str:
    positions = [text.lower().find(w.lower()) for w in matched]
    positions = [p for p in positions if p >= 0]
    start = max(0, (min(positions) if positions else 0) - 100)
    excerpt = text[start : start + width]
    return excerpt + ("…" if start + width < len(text) else "")


def heuristic_classify(text: str, target: dict[str, Any], settings: dict[str, Any]) -> tuple[str, int, list[str], str]:
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
    reasons: list[str] = []
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


def extract_json_object(raw: str) -> dict[str, Any]:
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


def deepseek_classify(text: str, target: dict[str, Any], settings: dict[str, Any], heuristic: tuple[str, int, list[str], str]) -> tuple[str, int, list[str], str, str | None, str | None]:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEYが未設定です")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip() or "deepseek-v4-pro"
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    grad_year = str(os.getenv("GRADUATION_YEAR", settings.get("graduation_year", 2028)))
    max_chars = int(os.getenv("DEEPSEEK_MAX_PAGE_CHARS", settings.get("deepseek_max_page_chars", 12000)))
    heuristic_status, heuristic_score, heuristic_reasons, heuristic_excerpt = heuristic
    page_text = text[:max_chars]

    system_prompt = "あなたは日本の新卒採用ページを判定する監査役です。本文だけを根拠にし、指定JSONのみ返してください。"
    user_prompt = f"""対象卒年: {grad_year}年卒（28卒）\n企業: {target['company']}\n対象分野: {target['category']}\n監視URL: {target['url']}\nルール仮判定: {heuristic_status} / {heuristic_score}\n根拠: {heuristic_reasons}\n\n現在応募可能なインターン、オープンカンパニー、本選考か判定してください。過年度、終了済み、会社説明のみはopenにしないでください。\nJSON: {{\"status\":\"open|possible|closed_or_unknown\",\"confidence\":0,\"program_type\":\"internship|selection|open_company|unknown\",\"deadline\":null,\"reasons\":[\"根拠\"],\"excerpt\":\"短い要約\"}}\n\nページ本文:\n{page_text}"""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        "response_format": {"type": "json_object"},
        # この用途では思考モードを切る。JSONだけ返す単純分類なので、料金と不安定要因を減らす。
        "thinking": {"type": "disabled"},
        "max_tokens": 900,
        "stream": False,
    }

    # DeepSeek公式ドキュメント上、JSON Outputはまれに空contentを返すことがある。
    # そのため空応答・壊れたJSONは最大3回まで再試行する。
    last_error: Exception | None = None
    parsed = None
    for attempt in range(1, 4):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=int(settings.get("deepseek_timeout_seconds", 90)),
            )
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
            break
        except (requests.RequestException, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt < 3:
                log.warning("%s: DeepSeek JSON応答エラー。%d/3回目を再試行します: %s", target["company"], attempt, exc)
                time.sleep(1.5 * attempt)
            else:
                raise RuntimeError(f"DeepSeek応答を3回処理できませんでした: {exc}") from exc

    if parsed is None:
        raise RuntimeError(f"DeepSeek判定結果が得られませんでした: {last_error}")
    status = parsed.get("status", "possible")
    if status not in {"open", "possible", "closed_or_unknown"}:
        status = "possible"
    confidence = max(0, min(100, int(parsed.get("confidence", 50))))
    reasons = [f"DeepSeek判定信頼度: {confidence}%"] + [str(x) for x in parsed.get("reasons", [])][:5]
    return status, confidence, reasons, str(parsed.get("excerpt") or heuristic_excerpt)[:1000], parsed.get("deadline"), parsed.get("program_type")


def check_target(target: dict[str, Any], settings: dict[str, Any], use_ai: bool) -> Result:
    now = datetime.now(timezone.utc).isoformat()
    try:
        text = fetch_text(target["url"], int(settings.get("request_timeout_seconds", 25)), settings.get("user_agent", "RecruitWatch/0.3"))
        heuristic = heuristic_classify(text, target, settings)
        ai_used = False
        deadline = program_type = None
        if use_ai:
            try:
                status, score, reasons, excerpt, deadline, program_type = deepseek_classify(text, target, settings, heuristic)
                ai_used = True
                time.sleep(float(settings.get("deepseek_delay_seconds", 0.3)))
            except Exception as ai_exc:
                if bool(settings.get("require_deepseek", True)):
                    raise RuntimeError(f"DeepSeek判定に失敗: {ai_exc}") from ai_exc
                log.warning("%s: DeepSeek失敗のためルール判定へフォールバック: %s", target["company"], ai_exc)
                status, score, reasons, excerpt = heuristic
                reasons.insert(0, "DeepSeek失敗のためルール判定")
        else:
            status, score, reasons, excerpt = heuristic
            reasons.insert(0, "ローカルテスト: DeepSeek未使用")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return Result(target["id"], target["company"], target["category"], target["url"], status, score, reasons, excerpt, digest, now, deadline, program_type, ai_used)
    except Exception as exc:
        return Result(target["id"], target["company"], target["category"], target["url"], "error", 0, [], "", "", now, error=str(exc))


def should_notify(current: Result, previous: dict[str, Any] | None, notify_first: bool) -> bool:
    if current.status not in {"open", "possible"}:
        return False
    if not previous:
        return notify_first
    return previous.get("status") not in {"open", "possible"} or (previous.get("status") == "possible" and current.status == "open") or previous.get("deadline") != current.deadline or previous.get("content_hash") != current.content_hash


def discord_post(results: list[Result], webhook_url: str, max_items: int) -> None:
    labels = {"quant": "クオンツ", "actuary": "アクチュアリー", "it": "IT/SIer", "consulting": "コンサル"}
    for chunk_start in range(0, min(len(results), max_items), 10):
        embeds = []
        for r in results[chunk_start:chunk_start + 10]:
            fields = [
                {"name": "分野", "value": labels.get(r.category, r.category), "inline": True},
                {"name": "判定値", "value": f"{r.score}%" if r.ai_used else str(r.score), "inline": True},
                {"name": "種別", "value": r.program_type or "不明", "inline": True},
            ]
            if r.deadline:
                fields.append({"name": "締切", "value": str(r.deadline), "inline": False})
            fields.append({"name": "根拠", "value": "\n".join(r.reasons)[:1000] or "なし", "inline": False})
            embeds.append({"title": f"{r.company}｜{'募集中' if r.status == 'open' else '要確認'}", "url": r.url, "description": r.excerpt[:1000] or "公式ページを確認してください。", "fields": fields, "footer": {"text": "自動判定です。応募前に公式情報を確認してください。"}})
        resp = requests.post(webhook_url, json={"content": "📣 28卒向け募集情報を検出しました", "embeds": embeds}, timeout=20)
        resp.raise_for_status()


def validate_targets(targets: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids: set[str] = set()
    for i, t in enumerate(targets, 1):
        for key in ("id", "company", "category", "url"):
            if not t.get(key): errors.append(f"target #{i}: {key} がありません")
        if t.get("id") in ids: errors.append(f"ID重複: {t.get('id')}")
        ids.add(t.get("id"))
        if t.get("category") not in {"quant", "actuary", "it", "consulting"}: errors.append(f"不正category: {t.get('id')}")
        if t.get("url") and not str(t["url"]).startswith(("http://", "https://")): errors.append(f"不正URL: {t.get('id')}")
    return errors


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="28卒採用監視")
    p.add_argument("--category", choices=["quant", "actuary", "it", "consulting"])
    p.add_argument("--target", help="target idを1件指定")
    p.add_argument("--limit", type=int, default=0, help="先頭N件のみ。Macローカルテスト向け")
    p.add_argument("--no-ai", action="store_true", help="DeepSeekを呼ばず無料で接続・判定テスト")
    p.add_argument("--no-discord", action="store_true", help="Discord送信を抑止")
    p.add_argument("--validate-config", action="store_true", help="設定だけ検証")
    p.add_argument("--list", action="store_true", help="対象一覧を表示")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    config = load_yaml(CONFIG_PATH)
    settings = config.get("settings", {})
    targets = [t for t in config.get("targets", []) if t.get("enabled", True) and t.get("tier", "major") == "major"]
    if args.category: targets = [t for t in targets if t.get("category") == args.category]
    if args.target: targets = [t for t in targets if t.get("id") == args.target]
    if args.limit > 0: targets = targets[:args.limit]

    errors = validate_targets(config.get("targets", []))
    if args.validate_config:
        if errors:
            for e in errors: log.error(e)
            return 1
        log.info("設定検証OK: %d targets", len(config.get("targets", [])))
        return 0
    if args.list:
        for t in targets: print(f"{t['id']}\t{t['category']}\t{t['company']}\t{t['url']}")
        return 0
    if not targets:
        log.warning("実行対象がありません")
        return 0

    previous_state = load_state().get("targets", {})
    use_ai = env_bool("DEEPSEEK_ENABLED", True) and not args.no_ai
    results = [check_target(t, settings, use_ai) for t in targets]
    notifications = [r for r in results if should_notify(r, previous_state.get(r.target_id), bool(settings.get("notify_on_first_detection", True)))]
    for r in results: log.info("%s [%s]: %s score=%s ai=%s %s", r.company, r.category, r.status, r.score, r.ai_used, r.error or "")
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if notifications and webhook and not args.no_discord:
        discord_post(notifications, webhook, int(settings.get("max_discord_items", 40)))
    elif notifications:
        log.info("通知候補%d件（Discord送信なし）", len(notifications))
    save_state(results)
    error_count = sum(1 for r in results if r.status == "error")

    if error_count:
    	log.warning(
            "%d件の取得・判定エラーがありましたが、他の監視対象は正常に処理されたため実行を継続します。",
            error_count,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
