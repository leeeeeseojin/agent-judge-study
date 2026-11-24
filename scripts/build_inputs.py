"""공개된 실행 기록을 심판 실험용 데이터로 변환한다.

사용법:
    python scripts/build_inputs.py <intentguard 경로> <agent-a11y-gap 경로>

출력:
    data/items.jsonl         실행 1건 = 1줄
    data/screenshots/*.png   실제 사이트 검사의 마지막 화면

두 축:
    결과 state_success  서버 감사 로그·브라우저 최종 상태로 채점한 값. 사람 라벨과 무관하다.
    주장 claim_auto     답변의 RESULT: 표식에서 정한 값. 표식이 없으면 None이고 사람이 라벨링한다.

기본은 공개 저장소에 올라간(경로를 가린) 요약 파일을 읽는다.
모델 교차 검증 80건은 공개 요약에 조작 순서·스크린샷이 없고 답변이 900자에서 잘려 있어서,
원본 로그(<intentguard>/.intentguard/bench80)가 있으면 거기서 채운다.
실제 사이트 21건도 원본(.intentguard/live)이 있으면 요소 이름이 보이는 조작 순서로 바꾼다. 로컬 경로는 가린다.
"""

import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"

# 통제 실험만 쓴다. demo-*, my-v1은 시연·개인 확인용 단발 실행이라 제외.
BENCH_DIRS = ["v1-baseline", "final-mcp-v1", "final-v2"]

# IntentGuard src/audit/report.ts와 같은 규칙: 마지막 표식을 쓴다.
MARKER = re.compile(r"RESULT[*_`\s]*:[*_`\s]*(confirmed|unconfirmed|failed)\b")
CLAIM_FROM_MARKER = {"confirmed": "CLAIM_DONE", "unconfirmed": "CLAIM_UNSURE", "failed": "CLAIM_NOT_DONE"}


def split_marker(answer: str) -> tuple[str, str | None]:
    """(표식 줄을 뺀 답변, 표식 값). 표식 조건 비교에서 '표식 없음' 입력으로 쓴다."""
    matches = list(MARKER.finditer(answer))
    if not matches:
        return answer.strip(), None
    m = matches[-1]
    line_start = answer.rfind("\n", 0, m.start()) + 1
    line_end = answer.find("\n", m.end())
    line_end = len(answer) if line_end == -1 else line_end
    return (answer[:line_start] + answer[line_end:]).strip(), m.group(1)


HOME = str(Path.home())
# 도구 입력을 JSON 문자열로 바꾼 뒤에는 역슬래시가 두 번 쓰이므로 [\\/]+로 받는다
LOCAL_PATH = re.compile(r"[A-Za-z]:[\\/]+(?:Users|FE Project)[\\/]+[^\s`\"')]+")


def redact(text: str) -> str:
    """IntentGuard publish.ts의 redact와 같은 규칙. 홈 폴더와 로컬 경로를 가린다."""
    text = text.replace(HOME, "~").replace(HOME.replace("\\", "/"), "~")
    return LOCAL_PATH.sub("(로컬 경로)", text)


PW_CODE = re.compile(r"Ran Playwright code\n```js\n(.*?)\n```", re.S)


def compact_action(call: dict, seen: str = "") -> dict:
    """도구 호출 1건을 (도구, 설명, 실패 여부)로 줄인다.

    요소 번호(e205, 1_9)만으로는 무엇을 눌렀는지 알 수 없으므로,
    Playwright MCP는 결과에 남은 실행 코드를, DevTools MCP는 앞선 스냅샷의 uid 줄을 설명으로 쓴다.
    seen은 이 호출 전까지의 도구 결과를 이어 붙인 문자열이다.
    """
    name = call["name"].split("<|")[0]
    name = re.sub(r"^mcp__[^_]+(?:[-_][^_]+)*?__", "", name)
    name = re.sub(r"^browser_", "", name)
    inp = call.get("input") or {}
    if not isinstance(inp, dict):
        inp = {"value": inp}
    result = call.get("resultPreview") or ""
    failed = bool(call.get("isError") or call.get("failed"))

    detail = ""
    code = PW_CODE.search(result)
    if "element" in inp:  # Claude Code는 요소 설명을 직접 적는다
        detail = str(inp["element"])
    elif code and name != "navigate":
        detail = code.group(1).strip().removeprefix("await page.").rstrip(";")
    elif "uid" in inp:
        m = re.search(r"uid=" + re.escape(str(inp["uid"])) + r" ([^\n]*)", seen)
        detail = m.group(1) if m else str(inp["uid"])
    if "element" in inp and "text" in inp:
        detail += f" ← {inp['text']}"
    elif "uid" in inp and "value" in inp:
        detail += f" ← {inp['value']}"
    if not detail:
        detail = next((str(inp[k]) for k in ("text", "url", "key", "ref", "values", "name", "id") if k in inp), "")
    if not detail and inp:
        detail = json.dumps(inp, ensure_ascii=False)

    # a11y 실행 주소의 run= 값에는 에이전트 모델 이름이 들어 있다. 심판이 모델을 알지 못하게 지운다
    detail = re.sub(r"([?&]run=)[^&\s\"']+", r"\1(가림)", detail)
    action = {"tool": name, "detail": redact(detail)[:140], "failed": failed}
    if failed and result:  # 오류 첫 줄. 에이전트가 오류를 보고도 성공이라 했는지 판단하는 근거
        err = next((l for l in result.splitlines() if l.strip() and not l.startswith("###")), "")
        action["error"] = redact(err.removeprefix("Error: "))[:120]
    return action


def compact_actions(calls: list[dict]) -> list[dict]:
    out, seen = [], ""
    for c in calls:
        out.append(compact_action(c, seen))
        seen += "\n" + (c.get("resultPreview") or "")
    return out


def raw_multi_runs(repo: Path) -> dict[tuple[str, str, str], tuple[dict, Path]]:
    """원본 로그에서 공개 요약과 같은 실행을 고른다.

    multiModel.ts와 같은 규칙: 시작 시각 순으로 보고, 무효 실행은 건너뛰고,
    (과제, 화면, 모델)마다 첫 유효 실행만 쓴다.
    """
    raw_dir = repo / ".intentguard/bench80"
    if not raw_dir.exists():
        return {}
    records = []
    for f in raw_dir.rglob("*.json"):
        records.append((json.loads(f.read_text(encoding="utf8")), f))
    records.sort(key=lambda rf: rf[0]["startedAt"])
    runs: dict[tuple[str, str, str], tuple[dict, Path]] = {}
    for r, f in records:
        # report.ts judge()의 무효 규칙. 저장된 outcome은 이 규칙이 생기기 전 값일 수 있어 다시 계산한다
        a = r["agent"]
        browser_ok = any(c["name"].startswith("browser_") and not c.get("isError") for c in a["calls"])
        if r["outcome"] == "invalid" or a.get("outsideTools") or a["exit"] == "error" or not browser_ok or r["grade"].get("error"):
            continue
        ui = "v2" if "ui=v2" in r["target"] else "v1"
        runs.setdefault((r["id"], ui, r["model"]), (r, f))
    return runs


def raw_live_runs(repo: Path) -> dict[int, dict]:
    """실제 사이트 검사 원본. publish.ts와 같은 순서로 읽어 스크린샷 번호(n)로 짝짓는다."""
    src = repo / ".intentguard/live"
    if not src.exists():
        return {}
    runs, n = {}, 0
    for d in sorted(p for p in src.iterdir() if p.is_dir()):
        for f in sorted(d.glob("*.json")):
            n += 1
            runs[n] = json.loads(f.read_text(encoding="utf8"))
    return runs


def live_actions(r: dict, raw_live: dict[int, dict]) -> list[dict]:
    m = re.search(r"/(\d+)\.png$", r.get("screenshot") or "")
    rec = raw_live.get(int(m.group(1))) if m else None
    if rec is None:  # 원본이 없으면 공개 파일의 요약을 쓴다 (요소 번호만 남아 있을 수 있다)
        return [compact_action(a | {"name": a["tool"], "input": {"text": a["detail"]}}) for a in r["actions"]]
    # 같은 실행인지 확인: 모델과 답변 앞 900자가 공개 기록과 같아야 한다
    assert rec["model"] == r["model"] and redact(rec["agent"]["answer"])[:900] == r["answer"], f"원본 불일치: {r['screenshot']}"
    return compact_actions(rec["agent"]["calls"])


def item(**kw) -> dict:
    answer, marker = split_marker(kw.pop("raw_answer") or "")
    base = {
        "answer": answer,
        "marker": marker,
        "claim_auto": CLAIM_FROM_MARKER.get(marker) if marker else ("NO_ANSWER" if not answer else None),
        "answer_truncated": False,
        "actions": None,
        "screenshot": None,
        "reference_outcome": None,
    }
    base.update(kw)
    return base


def load_prompts(path: Path) -> dict[str, str]:
    src = path.read_text(encoding="utf8")
    return dict(re.findall(r'id:\s*"([^"]+)",.*?prompt:\s*"([^"]+)"', src, re.S))


def intentguard_items(repo: Path) -> list[dict]:
    prompts = load_prompts(repo / "src/bench/scenarios.ts")
    items = []

    for d in BENCH_DIRS:
        for f in sorted((repo / "runs" / d).glob("*.json")):
            r = json.loads(f.read_text(encoding="utf8"))
            items.append(item(
                id=f"ig-bench/{d}/{r['runId']}", source="ig-bench", runner="claude-code",
                task_id=r["scenarioId"], task=prompts.get(r["scenarioId"], ""),
                surface=r["driver"] if r["driver"] == "mcp" else f"web-{r.get('ui')}", model=r["model"],
                raw_answer=r["finalAnswer"], actions=compact_actions(r["toolCalls"]),
                state_success=r["grade"]["success"], state_notes=r["grade"]["notes"],
            ))

    shots = OUT / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)

    multi = json.loads((repo / "docs/results/multi-model.json").read_text(encoding="utf8"))
    raw = raw_multi_runs(repo)
    for r in multi["runs"]:
        extra = {"raw_answer": r["answer"], "answer_truncated": len(r["answer"]) >= 900}  # 공개 파일은 900자에서 자른다
        match = raw.get((r["task"], r["ui"], r["model"]))
        if match:
            rec, f = match
            full = redact(rec["agent"]["answer"])
            # 같은 실행인지 확인: 결과 분류와 답변 앞 900자가 공개 요약과 같아야 한다
            assert rec["outcome"] == r["outcome"] and full[:900] == r["answer"], f"원본 불일치: {f}"
            extra = {"raw_answer": full, "answer_truncated": False,
                     "actions": compact_actions(rec["agent"]["calls"])}
            png = f.with_suffix(".png")
            if png.exists():
                shot = f"screenshots/multi-{r['model']}-{r['task']}-{r['ui']}.png"
                shutil.copyfile(png, OUT / shot)
                extra["screenshot"] = shot
        items.append(item(
            id=f"ig-multi/{r['model']}/{r['task']}/{r['ui']}", source="ig-multi", runner="antigravity",
            task_id=r["task"], task=prompts.get(r["task"], ""), surface=f"web-{r['ui']}", model=r["model"],
            state_success=r["outcome"] in ("confirmed-success", "unconfirmed-success", "false-failure"),
            state_notes=r["notes"], reference_outcome=r["outcome"], **extra,
        ))

    live = json.loads((repo / "web/src/demo/live-evidence.json").read_text(encoding="utf8"))
    raw_live = raw_live_runs(repo)
    for k, s in enumerate(live):
        host = re.sub(r"^https?://", "", s["url"]).split("/")[0]
        for r in s["runs"]:
            shot = None
            src = repo / "web/public" / (r.get("screenshot") or "").lstrip("/")
            if r.get("screenshot") and src.exists():
                shot = f"screenshots/live-{src.name}"
                shutil.copyfile(src, OUT / shot)
            items.append(item(
                id=f"ig-live/{k:02d}-{host}/{r['model']}", source="ig-live", runner="antigravity",
                task_id=host, task=s["task"], criteria=s["criteria"], surface="web-live", model=r["model"],
                raw_answer=r["answer"], actions=live_actions(r, raw_live),
                screenshot=shot,
                state_success=r["outcome"] in ("confirmed-success", "unconfirmed-success", "false-failure"),
                state_notes=r["notes"], reference_outcome=r["outcome"],
            ))
    return items


def a11y_items(repo: Path) -> list[dict]:
    prompts = load_prompts(repo / "bench/tasks.ts")
    items = []
    for f in sorted((repo / "runs/layer2").glob("*/*.json")):
        r = json.loads(f.read_text(encoding="utf8"))
        if r.get("invalid"):  # 사용량 한도·연결 오류 등 실행기 문제
            continue
        items.append(item(
            id=f"a11y/{r['browser']}/{r['runId']}", source="a11y-layer2", runner=r["agent"],
            task_id=r["task"], task=prompts.get(r["task"], ""), surface=f"modal-{r['impl']}-{r['browser']}",
            model=r["model"], raw_answer=r["finalAnswer"], actions=compact_actions(r["toolCalls"]),
            state_success=r["grade"]["success"], state_notes=r["grade"]["notes"], reference_outcome=r["outcome"],
        ))
    return items


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit("사용법: python scripts/build_inputs.py <intentguard 경로> <agent-a11y-gap 경로>")
    OUT.mkdir(exist_ok=True)
    items = intentguard_items(Path(sys.argv[1])) + a11y_items(Path(sys.argv[2]))

    ids = [it["id"] for it in items]
    assert len(ids) == len(set(ids)), "중복 id"
    with (OUT / "items.jsonl").open("w", encoding="utf8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

    print(f"총 {len(items)}건 → data/items.jsonl")
    print(f"  {'출처':12} {'건수':>4} {'실제 실패':>8} {'표식 있음':>8} {'거짓 성공(표식)':>14} {'조작 기록':>8} {'화면':>4}")
    for src in dict.fromkeys(it["source"] for it in items):
        its = [i for i in items if i["source"] == src]
        print(
            f"  {src:12} {len(its):>4} {sum(not i['state_success'] for i in its):>8}"
            f" {sum(i['marker'] is not None for i in its):>8}"
            f" {sum((not i['state_success']) and i['claim_auto'] == 'CLAIM_DONE' for i in its):>14}"
            f" {sum(i['actions'] is not None for i in its):>8} {sum(i['screenshot'] is not None for i in its):>4}"
        )


if __name__ == "__main__":
    main()
