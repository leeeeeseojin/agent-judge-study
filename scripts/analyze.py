"""라벨·심판 판정을 모아 결과 표를 만든다.

입력 (있는 것만 쓴다):
    data/items.jsonl
    labels/sheet_round1.csv                     직접 붙인 주장 라벨 (1차)
    labels/sheet_other.csv, adjudication.csv    다른 라벨러의 라벨, 어긋난 기록의 조정 결과
    labels/regex.csv                            정규식 기준선
    labels/selection.json                       라벨링 대상 선정 이유
    judge/*.jsonl                               심판 판정. 줄마다
        {"item_id", "judge", "condition": "A"|"B"|"C", "marker": true|false,
         "success": true|false, "claim": "CLAIM_DONE"|"CLAIM_UNSURE"|"CLAIM_NOT_DONE"}

출력: results/summary.md
"""

import csv
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ["CLAIM_DONE", "CLAIM_UNSURE", "CLAIM_NOT_DONE"]
SHORT = {"DONE": "CLAIM_DONE", "UNSURE": "CLAIM_UNSURE", "NOT_DONE": "CLAIM_NOT_DONE"}


def kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in ca) / (n * n)
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def read_labels(name: str) -> dict[str, str]:
    path = ROOT / "labels" / name
    if not path.exists():
        return {}
    out = {}
    with path.open(encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            v = r["label"].strip().upper()
            if not v:
                continue
            v = SHORT.get(v, v)
            if v not in CLAIMS:
                raise SystemExit(f"{name} {r['item_id']}: 알 수 없는 라벨 {v!r}")
            out[r["item_id"]] = v
    return out


# 사람 승인을 요청하고 기다린다고 답한 기록. 답변을 직접 읽고 골랐다 (모두 운영 서버 정지 과제).
# "승인" 같은 단어로 찾으면 "승인 대기 요청은 없었습니다" 같은 답변까지 걸려서 목록으로 둔다
APPROVAL_WAIT = {
    "ig-multi/claude-sonnet-5-5-low/stop-newest-running/v2",
    "ig-multi/gemini-3.8-flash-low/stop-newest-running/v2",
    "ig-multi/gemini-4-argon-low/stop-newest-running/v2",
}


def mcnemar(b: int, c: int) -> float:
    """짝지은 두 판정의 McNemar 정확검정 (양측 p).
    b, c는 한쪽에서만 '예'인 기록 수. 같은 기록을 두 조건으로 판정했으니 독립 표본 검정을 쓰면 안 된다."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def kappa_ci(xa: list[str], xb: list[str], reps: int = 2000) -> tuple[float, float]:
    """기록을 복원 추출해 κ를 다시 재는 부트스트랩 95% 구간. 시드를 고정해 다시 돌려도 같은 값이 나온다."""
    rng = random.Random(0)
    n = len(xa)
    ks = []
    for _ in range(reps):
        idx = [rng.randrange(n) for _ in range(n)]
        ks.append(kappa([xa[i] for i in idx], [xb[i] for i in idx]))
    ks.sort()
    return ks[int(reps * 0.025)], ks[int(reps * 0.975) - 1]


def fmt_p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def compare(name: str, a: dict[str, str], b: dict[str, str], ids: set[str] | None = None) -> str | None:
    common = sorted((set(a) & set(b)) if ids is None else (set(a) & set(b) & ids))
    if not common:
        return None
    xa, xb = [a[i] for i in common], [b[i] for i in common]
    agree = sum(x == y for x, y in zip(xa, xb))
    lo, hi = kappa_ci(xa, xb)
    return f"| {name} | {len(common)} | {agree}/{len(common)} | {kappa(xa, xb):.2f} | {lo:.2f}–{hi:.2f} |"


def stability(items: dict, false_success: set, judges: dict) -> list[str]:
    """같은 입력을 다시 판정한 반복 실행(judge/repeats/)으로 판정이 얼마나 흔들리는지 본다.

    RQ2에서 표식 유무로 바뀐 건수가 이 흔들림보다 큰지가 핵심이다. 흔들림만큼은 표식이 없어도 바뀐다."""
    repeats: dict[tuple, list[dict]] = defaultdict(list)
    for f in sorted((ROOT / "judge/repeats").glob("*.jsonl")) if (ROOT / "judge/repeats").exists() else []:
        run: dict[tuple, dict] = defaultdict(dict)
        for r in map(json.loads, f.open(encoding="utf8")):
            run[(r["judge"], r["condition"], bool(r["marker"]))][r["item_id"]] = r
        for key, rows in run.items():
            repeats[key].append(rows)
    if not repeats:
        return []

    out = ["", "## 판정 흔들림 (같은 입력 반복)", "",
           "본 실행과 반복 실행의 성공 판정을 같은 입력끼리 비교한다. 표식 유무 비교(RQ2)와 같은 기록(표식이 있는 기록)만 센다.", "",
           "| 심판·조건 | 실행 수 | 비교 건수 | 반복 간 성공 판정이 바뀜 (실행 쌍 평균) | 모든 실행에서 같음 | 표식 유무로 바뀜 (RQ2) |",
           "| --- | --- | --- | --- | --- | --- |"]
    per_run = ["", "실행별로 다시 센 주요 수치. 실행마다 결론이 같으면 흔들림에 강한 결과다.", "",
               "| 심판·조건 | 실행 | 거짓 성공을 잡음 | 실제 성공을 실패로 봄 | 표식 있을 때만 성공 / 없을 때만 성공 | p |",
               "| --- | --- | --- | --- | --- | --- |"]
    for (judge, cond, has_marker), reps in sorted(repeats.items()):
        if not has_marker or (judge, cond, True) not in judges:
            continue
        runs = [judges[(judge, cond, True)]] + reps
        ids = [i for i in runs[0] if items[i]["marker"] and all(i in r for r in runs)]
        pairs = [(a, b) for a in range(len(runs)) for b in range(a + 1, len(runs))]
        flips = [sum(runs[a][i]["success"] != runs[b][i]["success"] for i in ids) for a, b in pairs]
        stable = sum(len({r[i]["success"] for r in runs}) == 1 for i in ids)
        without = judges.get((judge, cond, False), {})
        marker_flips = sum(runs[0][i]["success"] != without[i]["success"] for i in ids if i in without)
        out.append(f"| {judge}·{cond} | {len(runs)} | {len(ids)} | {sum(flips) / len(flips):.1f} ({', '.join(map(str, flips))}) "
                   f"| {stable}/{len(ids)} | {marker_flips} |")

        # 실행마다 RQ1·RQ2 수치를 다시 센다. 반복 실행에도 표식 없는 판정이 있어서 RQ2를 실행별로 잴 수 있다
        withouts = [without] + [r for r in repeats.get((judge, cond, False), [])]
        fs = [i for i in false_success if all(i in r for r in runs)]
        ok = [i for i in runs[0] if items[i]["state_success"] and all(i in r for r in runs)]
        for n, (w, wo) in enumerate(zip(runs, withouts), 1):
            pushed = sum(w[i]["success"] and not wo[i]["success"] for i in ids if i in wo)
            pulled = sum(not w[i]["success"] and wo[i]["success"] for i in ids if i in wo)
            per_run.append(f"| {judge}·{cond} | {'본 실행' if n == 1 else f'반복 {n - 1}'} | {sum(not w[i]['success'] for i in fs)}/{len(fs)} "
                           f"| {sum(not w[i]['success'] for i in ok)}/{len(ok)} | {pushed} / {pulled} | {fmt_p(mcnemar(pushed, pulled))} |")
    return out + per_run


def main() -> None:
    items = {it["id"]: it for it in map(json.loads, (ROOT / "data/items.jsonl").open(encoding="utf8"))}
    r1 = read_labels("sheet_round1.csv")
    other = read_labels("sheet_other.csv")  # 다른 라벨러. 애매한 기록을 많이 뽑아 실제 분포와 다르다
    # 최종 라벨 = 1차 라벨 + 다른 라벨러와 어긋난 기록의 조정 결과 (labels/adjudication.csv).
    # 다른 라벨러와의 일치도는 조정 전 r1로 재고, 주장 축과 정규식·표식·심판 비교는 최종 라벨로 한다
    final = dict(r1)
    adj_path = ROOT / "labels/adjudication.csv"
    if adj_path.exists():
        for r in csv.DictReader(adj_path.open(encoding="utf-8-sig")):
            final[r["item_id"]] = SHORT[r["final"]]
    adjusted = sum(final[i] != r1[i] for i in r1)
    regex = {}
    if (ROOT / "labels/regex.csv").exists():
        regex = {r["item_id"]: r["label3"] for r in csv.DictReader((ROOT / "labels/regex.csv").open(encoding="utf8"))}
    selection = json.loads((ROOT / "labels/selection.json").read_text(encoding="utf8")) if (ROOT / "labels/selection.json").exists() else {}

    # 주장 축: 표식 > 직접 라벨. 표식은 에이전트가 형식을 갖춰 선언한 주장이라 우선한다.
    # 표식과 본문이 어긋나는 기록은 주장 축을 바꾸지 않고 RQ3에서 따로 본다. 빈 답변은 주장 없음.
    claim = {i: it["claim_auto"] if it["claim_auto"] else final.get(i) for i, it in items.items()}
    unresolved = [i for i, c in claim.items() if c is None]
    false_success = {i for i, it in items.items() if not it["state_success"] and claim[i] == "CLAIM_DONE"}
    marker = {i: it["claim_auto"] for i, it in items.items() if it["claim_auto"] in CLAIMS}

    judges: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for f in sorted((ROOT / "judge").glob("*.jsonl")) if (ROOT / "judge").exists() else []:
        for r in map(json.loads, f.open(encoding="utf8")):
            judges[(r["judge"], r["condition"], bool(r["marker"]))][r["item_id"]] = r

    by_src = Counter(items[i]["source"] for i in sorted(false_success))
    out = [
        "# 결과 요약", "",
        f"- 실행 {len(items)}건, 실제 실패 {sum(not it['state_success'] for it in items.values())}건",
        f"- 거짓 성공 {len(false_success)}건 ({', '.join(f'{k} {v}' for k, v in by_src.items())})",
        f"- 직접 라벨 1차 {len(r1)}건 (조정으로 바뀐 기록 {adjusted}건), 다른 라벨러 {len(other)}건" + (f" · **주장이 정해지지 않은 기록 {len(unresolved)}건**" if unresolved else ""),
        "",
    ]

    # RQ1. 입력 조건별 성공 판정 — 같은 묶음에서 비교
    out += ["## RQ1. 입력 조건 (표식 있음)", ""]
    for judge in sorted({k[0] for k in judges}):
        conds = {c: judges[(judge, c, True)] for c in "ABC" if (judge, c, True) in judges}
        for group in (["A", "B"], ["A", "B", "C"]):
            if not all(c in conds for c in group):
                continue
            ids = set.intersection(*(set(conds[c]) for c in group))
            fs = ids & false_success
            real_ok = {i for i in ids if items[i]["state_success"]}
            out += [f"**{judge} · 조건 {'/'.join(group)} 공통 {len(ids)}건** (거짓 성공 {len(fs)}, 실제 성공 {len(real_ok)})", "",
                    "| 조건 | 거짓 성공을 실패로 잡음 | 실제 성공을 실패로 봄 |", "| --- | --- | --- |"]
            for c in group:
                caught = sum(not conds[c][i]["success"] for i in fs)
                wrong = sum(not conds[c][i]["success"] for i in real_ok)
                out.append(f"| {c} | {caught}/{len(fs)} | {wrong}/{len(real_ok)} |")
            out.append("")
            # 조건 쌍마다 McNemar 검정. 같은 기록에서 뒤 조건만 실패로 본 수(b)와 앞 조건만 실패로 본 수(c)를 비교한다
            pairs = [(group[k], group[m]) for k in range(len(group)) for m in range(k + 1, len(group))]
            out += ["| 비교 | 거짓 성공: 뒤 조건만 잡음 / 앞 조건만 잡음 | p | 실제 성공 오판: 뒤 조건만 / 앞 조건만 | p |",
                    "| --- | --- | --- | --- | --- |"]
            for x, y in pairs:
                fail = lambda c, i: not conds[c][i]["success"]
                b1 = sum(fail(y, i) and not fail(x, i) for i in fs)
                c1 = sum(fail(x, i) and not fail(y, i) for i in fs)
                b2 = sum(fail(y, i) and not fail(x, i) for i in real_ok)
                c2 = sum(fail(x, i) and not fail(y, i) for i in real_ok)
                out.append(f"| {x}→{y} | {b1} / {c1} | {fmt_p(mcnemar(b1, c1))} | {b2} / {c2} | {fmt_p(mcnemar(b2, c2))} |")
            out.append("")
    if not judges:
        out += ["(심판 판정 없음)", ""]
    out += ["p는 McNemar 정확검정(양측)이다. 비교가 여러 개라 0.05 근처의 p는 조심해서 읽는다.", ""]

    # RQ2. 표식 의존 — 같은 기록, 표식 있음/없음
    # "표식 있을 때만 성공"은 표식이 심판을 성공 쪽으로 민 경우다. 반대 방향과 나눠 센다
    out += ["## RQ2. 표식을 지웠을 때 판정이 바뀐 건수", "",
            "| 심판·조건 | 비교 건수 | 성공 판정이 바뀜 | 표식 있을 때만 성공 | p | 그중 거짓 성공 | 주장 판정이 바뀜 |",
            "| --- | --- | --- | --- | --- | --- | --- |"]
    rows = 0
    for (judge, cond, has_marker), with_m in sorted(judges.items()):
        if not has_marker or (judge, cond, False) not in judges:
            continue
        without = judges[(judge, cond, False)]
        ids = [i for i in with_m if i in without and items[i]["marker"]]
        flips = [i for i in ids if with_m[i]["success"] != without[i]["success"]]
        cflips = sum(with_m[i].get("claim") != without[i].get("claim") for i in ids)
        pushed = [i for i in flips if with_m[i]["success"]]
        # 표식이 성공 쪽으로 밀었는지: 바뀐 기록 중 두 방향의 수를 McNemar로 비교한다
        p = mcnemar(len(pushed), len(flips) - len(pushed))
        out.append(f"| {judge}·{cond} | {len(ids)} | {len(flips)} | {len(pushed)} | {fmt_p(p)} | {sum(i in false_success for i in flips)} | {cflips} |")
        rows += 1
    if not rows:
        out.append("| (판정 없음) | | | | | | |")

    # RQ3. 애매한 주장 — 직접 라벨한 기록에서 정규식·심판·표식·나 자신
    out += ["", "## RQ3. 주장 판정 일치도 (직접 라벨 기준)", "",
            "사람끼리의 일치도는 조정 전 1차 라벨, 나머지는 조정 후 최종 라벨로 잰다. 95% 구간은 부트스트랩(2,000번)이다.", "",
            "| 비교 | 건수 | 일치 | κ | 95% 구간 |", "| --- | --- | --- | --- | --- |"]
    marked_labeled = {i for i in final if i in marker}
    random_ids = {i for i, why in selection.items() if "random" in why}
    rows = [
        compare("나 vs 다른 라벨러 (애매한 기록 위주로 뽑음)", r1, other),
        compare("나 vs 정규식", final, regex),
        compare("나 vs 표식 (표식 있는 기록)", final, marker, marked_labeled),
        compare("나 vs 표식 (무작위 30건 → 놓친 불일치 추정)", final, marker, random_ids),
    ]
    # 승인 대기 답변: 라벨링 가이드는 문장대로 판단, 심판 프롬프트는 처음 규칙대로 CLAIM_NOT_DONE.
    # 규칙 차이로 생기는 불일치를 빼고 다시 센 행을 함께 둔다
    approval = APPROVAL_WAIT & set(final)
    for (judge, cond, has_marker), js in sorted(judges.items()):
        if not has_marker:
            jc = {i: r["claim"] for i, r in js.items() if r.get("claim")}
            rows.append(compare(f"나 vs {judge}·{cond} (표식 없이)", final, jc))
            if approval:
                rows.append(compare(f"나 vs {judge}·{cond} (표식 없이, 승인 대기 {len(approval)}건 제외)", final, jc, set(final) - approval))
    out += [r for r in rows if r] or ["| (라벨 없음) | | | | |"]

    out += stability(items, false_success, judges)

    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results/summary.md").write_text("\n".join(out) + "\n", encoding="utf8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
