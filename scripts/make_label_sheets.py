"""직접 라벨링할 기록을 골라 시트로 만든다.

라벨링 대상 (주장 축):
    no-marker   RESULT: 표식이 없는 답변 → 주장을 표식으로 정할 수 없다
    regex       정규식 분류와 표식이 어긋난 답변
    llm         LLM 심판의 주장 판정과 표식이 어긋난 답변 (judge/*.jsonl이 있을 때)
    random      정규식과 표식이 일치한 답변 중 무작위 30건 → 후보 선정이 놓친 불일치를 추정

정규식만으로 후보를 고르면 정규식이 자기가 고른 데이터로 평가받으므로 LLM 불일치와 무작위 표본을 합친다.

사용법:
    python scripts/make_label_sheets.py           labels/sheet_round1.csv 생성. 다시 실행하면 새 후보만 아래에 덧붙인다
    python scripts/make_label_sheets.py --other   labels/sheet_other.csv 생성. 다른 라벨러용 30건 (내 라벨별 10건씩 무작위)
                                                  시트가 있으면 30건을 덧붙인다 (UNSURE·NOT_DONE 남은 것 전부 + DONE 무작위)

시트에는 지시문과 표식을 뺀 답변만 넣는다. 실제 성공 여부·모델·표식은 보지 않고 붙인다.
"""

import csv
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels"
RANDOM_N = 30
OTHER_PER_LABEL = 10
OTHER_BATCH = 30
FIELDS = ["item_id", "task", "answer", "label", "note"]

items = {it["id"]: it for it in map(json.loads, (ROOT / "data/items.jsonl").open(encoding="utf8"))}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict]) -> None:
    LABELS.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:  # 엑셀에서 한글이 깨지지 않게
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def row(item_id: str) -> dict:
    it = items[item_id]
    return {"item_id": item_id, "task": it["task"], "answer": it["answer"], "label": "", "note": ""}


def select() -> dict[str, list[str]]:
    reasons: dict[str, list[str]] = {}
    add = lambda i, why: reasons.setdefault(i, []).append(why)
    marked = {i: it for i, it in items.items() if it["claim_auto"] in ("CLAIM_DONE", "CLAIM_UNSURE", "CLAIM_NOT_DONE")}

    for i, it in items.items():
        if it["claim_auto"] is None:
            add(i, "no-marker")

    regex = {r["item_id"]: r["label3"] for r in read_csv(LABELS / "regex.csv")}
    if not regex:
        sys.exit("labels/regex.csv가 없습니다. scripts/regex_baseline.py를 먼저 실행하세요.")
    regex_agree = []
    for i, it in marked.items():
        (add(i, "regex") if regex.get(i) != it["claim_auto"] else regex_agree.append(i))

    for f in sorted((ROOT / "judge").glob("*.jsonl")) if (ROOT / "judge").exists() else []:
        for r in map(json.loads, f.open(encoding="utf8")):
            i = r["item_id"]
            if r.get("marker"):  # 표식을 본 판정은 표식을 따라 쓸 수 있으므로, 표식을 지운 판정만 비교한다
                continue
            if i in marked and r.get("claim") and r["claim"] != marked[i]["claim_auto"]:
                if "llm" not in reasons.get(i, []):
                    add(i, "llm")

    # 무작위 표본은 심판 결과와 무관하게 고정 시드로 뽑는다
    for i in random.Random(1124).sample(sorted(regex_agree), RANDOM_N):
        add(i, "random")
    return reasons


def main() -> None:
    reasons = select()
    with (LABELS / "selection.json").open("w", encoding="utf8") as f:
        json.dump(reasons, f, ensure_ascii=False, indent=1)
    counts: dict[str, int] = {}
    for rs in reasons.values():
        for r in rs:
            counts[r] = counts.get(r, 0) + 1

    r1 = LABELS / "sheet_round1.csv"
    if "--other" in sys.argv:
        done = [r for r in read_csv(r1) if r["label"].strip()]
        other = LABELS / "sheet_other.csv"
        if other.exists():
            # 두 번째 묶음: 이미 붙인 라벨은 그대로 두고 30건을 덧붙인다.
            # 판단이 갈리기 쉬운 UNSURE·NOT_DONE 남은 기록을 모두 넣고, 모자란 만큼 DONE에서 무작위로 채운다
            existing = read_csv(other)
            have = {r["item_id"] for r in existing}
            rng = random.Random(20261125)
            left = lambda label: sorted(r["item_id"] for r in done if r["label"].strip().upper() == label and r["item_id"] not in have)
            ids = (left("UNSURE") + left("NOT_DONE"))[:OTHER_BATCH]
            ids += rng.sample(left("DONE"), OTHER_BATCH - len(ids))
            rng.shuffle(ids)
            write_csv(other, existing + [row(i) for i in ids])
            print(f"sheet_other.csv: 기존 {len(existing)}건 + 새로 {len(ids)}건")
            return
        # 첫 묶음: 내 라벨별로 같은 수를 무작위로 뽑는다. 그냥 뽑으면 대부분 DONE이라 κ가 불안정하다
        rng = random.Random(20261124)
        ids = []
        for label in ("DONE", "UNSURE", "NOT_DONE"):
            pool = sorted(r["item_id"] for r in done if r["label"].strip().upper() == label)
            ids += rng.sample(pool, min(OTHER_PER_LABEL, len(pool)))
        rng.shuffle(ids)
        write_csv(LABELS / "sheet_other.csv", [row(i) for i in ids])
        print(f"sheet_other.csv {len(ids)}건 (내 라벨별 {OTHER_PER_LABEL}건씩 무작위, 순서 섞음)")
        return

    existing = read_csv(r1)
    have = {r["item_id"] for r in existing}
    new = [i for i in reasons if i not in have]
    random.Random(1124 + len(existing)).shuffle(new)
    write_csv(r1, existing + [row(i) for i in new])
    print(f"sheet_round1.csv: 기존 {len(existing)}건 + 새로 {len(new)}건 · 선정 이유별 {counts}")


if __name__ == "__main__":
    main()
