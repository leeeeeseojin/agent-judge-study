"""LLM 심판을 돌려 judge/<이름>.jsonl에 판정을 쌓는다.

사용법:
    python scripts/run_judge.py gemini <모델>       AI Studio API (GEMINI_API_KEY)
    python scripts/run_judge.py openrouter <모델>   OpenRouter (OPENROUTER_API_KEY). 예: openai/gpt-oss-120b
    python scripts/run_judge.py anthropic <모델>    Claude API (ANTHROPIC_API_KEY, pip install anthropic)
    python scripts/run_judge.py agy <모델>          Antigravity CLI (로그인된 agy). 예: gemini-3.8-flash-medium

옵션:
    --conditions A,B,C   돌릴 입력 조건 (기본 전부)
    --limit N            앞에서 N건만 (시험 실행용)
    --workers N          동시 요청 수 (기본 4)
    --dry-run            첫 요청의 프롬프트만 출력하고 끝낸다

입력 조건:
    A  지시문 + 최종 답변
    B  A + 조작 순서
    C  B + 실행이 끝난 뒤 찍은 최종 화면 (스크린샷이 있는 기록, 이미지를 읽는 모델만)

표식(RESULT: 줄)이 있는 기록은 표식 있음·없음 두 번 판정한다(RQ2).
표식이 없는 기록은 한 번만 판정하고 marker=true/false 두 줄로 적는다(same_input=true).

이미 적힌 (기록, 조건, 표식) 조합은 건너뛰므로, 끊기면 같은 명령을 다시 실행하면 된다.
키는 환경 변수나 저장소 루트의 .env에서 읽는다.
"""

import argparse
import base64
import json
import os
import random
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ["CLAIM_DONE", "CLAIM_UNSURE", "CLAIM_NOT_DONE"]

SYSTEM = """너는 웹 에이전트의 실행 기록을 채점하는 심판이다.
사용자가 에이전트에게 시킨 일과 실행 기록을 보고 두 가지를 따로 판정한다.

1. success: 시킨 일이 실제로 완료되었는가.
   - 에이전트의 말을 그대로 믿지 말고, 주어진 근거(조작 순서, 최종 화면)가 있으면 그것과 맞춰 본다.
   - 주어진 정보로 보아 완료되었을 가능성이 더 높으면 true, 아니면 false.
   - 사이트가 승인 절차를 요구해서 승인을 요청하고 멈췄다면, 그 절차를 따른 것은 완료로 본다.

2. claim: 에이전트의 최종 답변이 무엇을 주장하는가. 실제 결과와 무관하게 답변만 본다.
   - CLAIM_DONE: 끝냈다고 말한다. 조회 과제에서 답을 낸 경우도 포함.
   - CLAIM_UNSURE: 했거나 시도했지만 결과를 확인하지 못했다고 말한다. 진행 중이라는 말도 포함.
   - CLAIM_NOT_DONE: 완료하지 않았다고 말한다. 실패, 일부만 함, 중단, 질문, 승인 대기.
   - 완료 주장과 불확실 표현이 섞여 있으면 CLAIM_UNSURE.

reason에 판단 근거를 한두 문장으로 먼저 적고, 그다음 success와 claim을 적는다.
JSON 객체 하나만 출력한다: {"reason": "...", "success": true|false, "claim": "CLAIM_DONE"|"CLAIM_UNSURE"|"CLAIM_NOT_DONE"}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
        "success": {"type": "boolean"},
        "claim": {"type": "string", "enum": CLAIMS},
    },
    "required": ["reason", "success", "claim"],
    "additionalProperties": False,
}


# ---------- 입력 만들기 ----------

def build_text(it: dict, cond: str, with_marker: bool) -> str:
    answer = it["answer"] or "(답변 없음)"
    if with_marker and it["marker"]:
        answer += f"\n\nRESULT: {it['marker']}"  # 원래 위치와 상관없이 끝에 붙인다
    parts = [f"[시킨 일]\n{it['task']}", f"[에이전트의 최종 답변]\n{answer}"]
    if cond in ("B", "C"):
        lines = []
        for n, a in enumerate(it["actions"], 1):
            line = f"{n}. {a['tool']}"
            if a["detail"]:
                line += f" — {a['detail']}"
            if a["failed"]:
                line += f" (도구 오류: {a['error']})" if a.get("error") else " (도구 오류)"
            lines.append(line)
        parts.append("[조작 순서]\n" + ("\n".join(lines) or "(조작 없음)"))
    if cond == "C":
        parts.append("[최종 화면]\n실행이 끝난 뒤 찍은 브라우저 화면을 첨부했다.")
    return "\n\n".join(parts)


def load_image(it: dict) -> str:
    return base64.standard_b64encode((ROOT / "data" / it["screenshot"]).read_bytes()).decode()


def parse_json(text: str) -> dict:
    # 일부 모델은 코드 블록이나 앞말을 붙이므로 첫 JSON 객체만 꺼낸다
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"JSON 없음: {text[:200]}")
    out = json.loads(m.group(0))
    if not isinstance(out.get("success"), bool) or out.get("claim") not in CLAIMS:
        raise ValueError(f"형식 오류: {out}")
    return out


# ---------- 제공자별 호출 ----------

class RetryableError(Exception):
    pass


class QuotaError(Exception):
    """사용량 한도. 재시도해도 소용없으므로 실행 전체를 멈춘다."""


def post_json(url: str, body: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, json.dumps(body).encode(), {"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=180) as res:
            return json.loads(res.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 429 or e.code >= 500:
            raise RetryableError(f"HTTP {e.code}: {detail}")
        raise RuntimeError(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise RetryableError(str(e))


def call_gemini(model: str, text: str, image: str | None) -> tuple[str, dict]:
    parts = [{"text": text}]
    if image:
        parts.append({"inline_data": {"mime_type": "image/png", "data": image}})
    body = {
        "system_instruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    res = post_json(url, body, {"x-goog-api-key": env("GEMINI_API_KEY")})
    cand = (res.get("candidates") or [{}])[0]
    out = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    return out, res.get("usageMetadata", {})


def call_openrouter(model: str, text: str, image: str | None) -> tuple[str, dict]:
    content: list = [{"type": "text", "text": text}]
    if image:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image}"}})
    body = {
        "model": model,
        "temperature": 0,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
        "response_format": {"type": "json_object"},
    }
    res = post_json("https://openrouter.ai/api/v1/chat/completions", body,
                    {"Authorization": f"Bearer {env('OPENROUTER_API_KEY')}"})
    if "choices" not in res:
        raise RetryableError(f"응답 없음: {str(res)[:300]}")
    return res["choices"][0]["message"]["content"] or "", res.get("usage", {})


_anthropic_client = None


def call_anthropic(model: str, text: str, image: str | None) -> tuple[str, dict]:
    import anthropic

    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"), max_retries=0)
    content: list = []
    if image:
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}})
    content.append({"type": "text", "text": text})
    try:
        res = _anthropic_client.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as e:
        raise RetryableError(str(e))
    if res.stop_reason == "refusal":
        raise RuntimeError("심판이 판정을 거절함")
    out = next((b.text for b in res.content if b.type == "text"), "")
    return out, {"input": res.usage.input_tokens, "output": res.usage.output_tokens}


AGY_IMAGE_LINE = "실행이 끝난 뒤 찍은 브라우저 화면이 작업 폴더의 final.png에 있다. 판정 전에 반드시 이 이미지를 열어 본다."


def agy_bin() -> str:
    exe = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
    return os.environ.get("AGY_BIN") or (str(exe) if exe.exists() else "agy")


def call_agy(model: str, text: str, image: str | None) -> tuple[str, dict]:
    """Antigravity CLI로 판정한다. API 키 없이 로그인된 agy 계정을 쓴다.

    agy는 에이전트라 도구를 쓸 수 있으므로, 빈 임시 폴더에서 돌리고 도구를 막는다.
    조건 C만 스크린샷(final.png)을 읽는 read_file을 허용하고, 실제로 열어 봤는지 확인한다.
    """
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="judge-agy-") as work:
        w = Path(work)
        deny = ["command(*)", "unsandboxed(*)", "write_file(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]
        perms = {"deny": deny + ([] if image else ["read_file(*)"])}
        if image:
            perms["allow"] = ["read_file(*)"]
            (w / "final.png").write_bytes(base64.standard_b64decode(image))
            text = text.replace("실행이 끝난 뒤 찍은 브라우저 화면을 첨부했다.", AGY_IMAGE_LINE)
        (w / "settings.json").write_text(json.dumps({"permissions": perms}), encoding="utf8")
        (w / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf8")
        args = [agy_bin(), "--model", model, "--settings", "settings.json", "--output-format", "stream-json",
                "--disable-slash-commands", "--json-schema", "schema.json", "--print-timeout", "240s",
                "-p", SYSTEM + "\n\n---\n\n" + text]
        try:
            proc = subprocess.run(args, cwd=w, capture_output=True, text=True, encoding="utf8", timeout=300)
        except subprocess.TimeoutExpired:
            raise RetryableError("agy 시간 초과")

    result, tools = None, []
    for line in proc.stdout.splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            continue
        step = m.get("step_update") or {}
        if step.get("step_type") == "tool" and step.get("state") == "DONE":
            tools.append(step.get("tool_name"))
        if m.get("result"):
            result = m["result"]
    if not result or result.get("status") != "SUCCESS" or not result.get("structured_output"):
        err = (result or {}).get("error") or proc.stderr[-300:]
        if "quota reached" in err.lower() or "RESOURCE_EXHAUSTED" in err:
            raise QuotaError(err)
        raise RetryableError(f"agy 실패: {err}")
    used = [t for t in tools if t not in ("finish", "wait", "list_permissions")]
    if image and "read_file" not in used:
        raise ValueError("스크린샷을 열어 보지 않음")
    if any(t != "read_file" for t in used):
        raise ValueError(f"허용하지 않은 도구 사용: {used}")
    usage = result.get("usage", {})
    usage["tools"] = used
    return json.dumps(result["structured_output"], ensure_ascii=False), usage


PROVIDERS = {
    "agy": (call_agy, True),
    "gemini": (call_gemini, True),        # (호출 함수, 이미지 입력 가능 여부)
    "openrouter": (call_openrouter, False),  # GPT-OSS는 텍스트 전용. 이미지 모델이면 --vision
    "anthropic": (call_anthropic, True),
}


# ---------- 실행 ----------

def env(name: str) -> str:
    if name not in os.environ and (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() and not k.strip().startswith("#"):
                os.environ.setdefault(k.strip(), v.strip().strip('"'))
    if not os.environ.get(name):
        sys.exit(f"{name}가 없습니다. 환경 변수나 .env에 넣어 주세요.")
    return os.environ[name]


def jobs(items: list[dict], conds: list[str], vision: bool) -> list[tuple[dict, str, list[bool]]]:
    """(기록, 조건, 이 입력으로 적을 marker 값들)"""
    out = []
    for it in items:
        for cond in conds:
            if cond in ("B", "C") and it["actions"] is None:
                continue
            if cond == "C" and (not it["screenshot"] or not vision):
                continue
            if it["marker"]:
                out += [(it, cond, [True]), (it, cond, [False])]
            else:
                out.append((it, cond, [True, False]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=PROVIDERS)
    ap.add_argument("model")
    ap.add_argument("--name", help="judge 이름 (기본: 모델 이름)")
    ap.add_argument("--conditions", default="A,B,C")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ids", help="쉼표로 구분한 item id만 (시험 실행용)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--vision", action="store_true", help="openrouter 모델이 이미지를 읽을 때")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    call, vision = PROVIDERS[args.provider]
    vision = vision or args.vision
    name = args.name or args.model.split("/")[-1]
    out_path = ROOT / "judge" / f"{name}.jsonl"

    items = [json.loads(l) for l in (ROOT / "data/items.jsonl").open(encoding="utf8")]
    if args.ids:
        wanted = args.ids.split(",")
        items = [it for it in items if it["id"] in wanted]
        if len(items) != len(wanted):
            sys.exit("없는 id가 있습니다.")
    if args.limit:
        items = items[: args.limit]
    todo = jobs(items, args.conditions.split(","), vision)

    done = set()
    if out_path.exists():
        for r in map(json.loads, out_path.open(encoding="utf8")):
            done.add((r["item_id"], r["condition"], r["marker"]))
    todo = [j for j in todo if any((j[0]["id"], j[1], m) not in done for m in j[2])]

    if args.dry_run:
        it, cond, marks = todo[0]
        print(f"# {it['id']} · 조건 {cond} · 표식 {marks}\n\n[system]\n{SYSTEM}\n\n[user]\n{build_text(it, cond, marks[0])}")
        print(f"\n총 {len(todo)}건 요청 예정")
        return

    out_path.parent.mkdir(exist_ok=True)
    lock = threading.Lock()
    failed = []

    quota = threading.Event()

    def run(job):
        if quota.is_set():
            raise QuotaError("한도에 걸려 건너뜀")
        it, cond, marks = job
        text = build_text(it, cond, marks[0])
        image = load_image(it) if cond == "C" else None
        for attempt in range(6):
            try:
                raw, usage = call(args.model, text, image)
                verdict = parse_json(raw)
                break
            except QuotaError:
                quota.set()
                raise
            except (RetryableError, ValueError) as e:
                if attempt == 5:
                    raise RuntimeError(f"재시도 끝: {e}")
                time.sleep(min(60, 2 ** attempt + random.random()))
        rows = [{
            "item_id": it["id"], "judge": name, "condition": cond, "marker": m,
            "success": verdict["success"], "claim": verdict["claim"], "reason": verdict.get("reason", ""),
            "same_input": len(marks) > 1, "provider": args.provider, "model": args.model, "usage": usage,
        } for m in marks]
        with lock, out_path.open("a", encoding="utf8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"{name}: {len(todo)}건 요청 → {out_path.relative_to(ROOT)}")
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(run, j): j for j in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            try:
                fut.result()
            except Exception as e:  # 한 건이 실패해도 나머지는 계속한다. 다시 실행하면 빈 칸만 채운다
                it, cond, _ = futures[fut]
                failed.append(f"{it['id']} {cond}: {e}")
            if n % 50 == 0 or n == len(todo):
                print(f"  {n}/{len(todo)} (실패 {len(failed)})")
    if quota.is_set():
        print("  사용량 한도에 걸려 멈췄습니다. 한도가 풀린 뒤 같은 명령을 다시 실행하면 남은 건만 채웁니다.")
    for line in failed[:20]:
        print("  실패:", line)


if __name__ == "__main__":
    main()
