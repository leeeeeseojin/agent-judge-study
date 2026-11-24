"""IntentGuard의 정규식 분류기(src/bench/classify.ts)를 주장 라벨 분류기로 옮긴 기준선.

원본은 실행 기록 전체에서 실패 태그를 뽑는다. 여기서는 그중 답변 문장을 읽는 규칙만 가져와
최종 답변 → 3분류 주장 라벨(CLAIM_DONE / CLAIM_UNSURE / CLAIM_NOT_DONE)로 바꾼다.
원본 정규식은 그대로 옮겼고, 판단 순서만 이 과제에 맞게 정했다.

출력: labels/regex.csv
"""

import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DONE_WORDS = re.compile(r"(완료|했습니다|했어|삭제했|생성했|정지했|추가했|바꿨|변경했|비웠|켰습니다|시작했)")
FAIL_WORDS = re.compile(r"(못했|못 했|않았|실패|할 수 없|불가|없습니다|오류|에러)")
UNCERTAIN = re.compile(r"(확인하지 못|확인되지 않|확인이 안|확인할 수 없|말할 수 없|보이지 않아|알 수 없)")
REPORTED_INCOMPLETE = re.compile(
    "|".join(
        [
            "삭제되지 않았", "만들어지지 않았", "켜지 못했", "켜지지 않았",
            "아직 `?(running|stopped)`?(가|로|으로) ?(되지|바뀌지)",
            "`deleting` 상태",
            "was not (created|deleted|stopped|started)", "is not deleted", "did not stick",
            "could(n't| not) (visually )?(confirm|verify)",
        ]
    ),
    re.I,
)


def claims_done(answer: str) -> bool:
    return bool(DONE_WORDS.search(answer)) and not FAIL_WORDS.search(answer[:200])


def classify(answer: str) -> str:
    if UNCERTAIN.search(answer):
        return "CLAIM_UNSURE"
    if REPORTED_INCOMPLETE.search(answer):
        return "CLAIM_NOT_DONE"
    if claims_done(answer):
        return "CLAIM_DONE"
    return "CLAIM_NOT_DONE"


def main() -> None:
    items = [json.loads(line) for line in (ROOT / "data/items.jsonl").open(encoding="utf8")]
    out = ROOT / "labels/regex.csv"
    out.parent.mkdir(exist_ok=True)
    with out.open("w", encoding="utf8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_id", "label3"])
        for it in items:
            if it["answer"]:  # 빈 답변은 주장이 없으므로 분류하지 않는다
                w.writerow([it["id"], classify(it["answer"])])
    print(f"{len(items)}건 → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
