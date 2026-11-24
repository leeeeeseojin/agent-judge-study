"""내 1차 라벨과 다른 라벨러가 어긋난 기록을 한 화면에 모아 최종 라벨을 정한다.

    python scripts/adjudicate.py make              labels/adjudicate.html 생성
    python scripts/adjudicate.py merge <내보낸 csv>  labels/adjudication.csv에 적는다

원래 라벨(sheet_round1.csv, sheet_other.csv)은 고치지 않는다. 라벨러 간 일치도는 원래 라벨로 재고,
최종 라벨은 주장 축과 심판 비교에만 쓴다 (analyze.py).
"""

import csv
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "labels"
OUT = LABELS / "adjudication.csv"
FIELDS = ["item_id", "round1", "other", "final", "reason"]
CHOICES = ("DONE", "UNSURE", "NOT_DONE")


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def disagreements() -> list[dict]:
    mine = {r["item_id"]: r for r in read(LABELS / "sheet_round1.csv")}
    out = []
    for r in read(LABELS / "sheet_other.csv"):
        a, b = mine[r["item_id"]]["label"].strip().upper(), r["label"].strip().upper()
        if a and b and a != b:
            out.append({"id": r["item_id"], "task": r["task"], "answer": r["answer"],
                        "round1": a, "round1_note": mine[r["item_id"]]["note"], "other": b, "other_note": r["note"]})
    return out


def make() -> None:
    items = disagreements()
    if not items:
        sys.exit("어긋난 기록이 없습니다.")
    # 영어 답변은 번역을 원문 아래에 참고용으로 붙인다. 라벨은 원문 기준
    tr_path = LABELS / "translations.json"
    translations = json.loads(tr_path.read_text(encoding="utf8")) if tr_path.exists() else {}
    cards = []
    for n, it in enumerate(items, 1):
        radios = "".join(
            f'<label><input type="radio" name="f{n}" value="{c}"> {c}</label>' for c in CHOICES
        )
        notes = "".join(
            f'<p class="note">{who} note: {html.escape(t)}</p>'
            for who, t in (("내", it["round1_note"]), ("다른 라벨러", it["other_note"])) if t.strip()
        )
        tr = translations.get(it["id"])
        tr_html = f'<p class="tag">번역 (참고용)</p><pre class="tr">{html.escape(tr)}</pre>' if tr else ""
        cards.append(f"""
<article data-id="{html.escape(it['id'])}" data-r1="{it['round1']}" data-other="{it['other']}">
  <h2>{n}. {html.escape(it['task'])}</h2>
  <pre>{html.escape(it['answer'])}</pre>
  {tr_html}
  <p class="labels"><span>내 1차: <b>{it['round1']}</b></span><span>다른 라벨러: <b>{it['other']}</b></span></p>
  {notes}
  <div class="pick">최종: {radios}</div>
  <input class="reason" placeholder="이유 (한 줄)">
</article>""")
    page = TEMPLATE.replace("__CARDS__", "".join(cards)).replace("__N__", str(len(items)))
    out = LABELS / "adjudicate.html"
    out.write_text(page, encoding="utf8")
    print(f"{out.relative_to(ROOT)}: {len(items)}건")


def merge(exported: Path) -> None:
    rows = read(exported)
    for r in rows:
        if r["final"] not in CHOICES:
            sys.exit(f"최종 라벨이 비었거나 잘못됐습니다: {r['item_id']}")
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows({k: r[k] for k in FIELDS} for r in rows)
    changed = sum(r["final"] != r["round1"] for r in rows)
    print(f"{OUT.relative_to(ROOT)}: {len(rows)}건, 내 1차 라벨과 달라진 기록 {changed}건")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>라벨 조정</title>
<style>
  body { margin: 0 auto; max-width: 900px; padding: 20px; font: 15px/1.6 system-ui, "Malgun Gothic", sans-serif; color: #1d1d22; background: #f6f6f8; }
  header p { color: #6b6b76; font-size: 13px; margin: 4px 0; }
  article { background: #fff; border: 1px solid #d9d9de; border-radius: 8px; padding: 14px 18px; margin: 14px 0; }
  h2 { font-size: 15px; margin: 0 0 8px; }
  pre { white-space: pre-wrap; word-break: break-word; font: inherit; background: #fafafb; padding: 10px; border-radius: 6px; margin: 0 0 8px; }
  .tag { font-size: 12px; color: #6b6b76; margin: 0 0 2px; }
  pre.tr { background: #f1f5fd; }
  .labels { display: flex; gap: 24px; margin: 6px 0; }
  .note { font-size: 13px; color: #6b6b76; margin: 2px 0; }
  .pick { margin: 8px 0; display: flex; gap: 16px; }
  .reason { width: 100%; padding: 6px 8px; border: 1px solid #d9d9de; border-radius: 6px; font: inherit; }
  button { padding: 8px 16px; border: 0; border-radius: 6px; background: #2f5bd3; color: #fff; font: inherit; cursor: pointer; }
  textarea { width: 100%; height: 200px; margin-top: 10px; }
</style>
</head>
<body>
<header>
  <h1>라벨 조정 (__N__건)</h1>
  <p>내 1차 라벨과 다른 라벨러가 어긋난 기록입니다. 라벨링 가이드 기준으로 최종 라벨을 고르고 이유를 한 줄 적습니다.</p>
  <p>규칙: 결론을 본다 · "했습니다"만 있어도 DONE · 일부만 하면 NOT_DONE · 승인 대기는 문장대로 (안 됐다면 NOT_DONE, 확인 못 했다면 UNSURE) · 완료와 불확실이 섞이면 UNSURE · 했다고 볼 근거가 없으면 NOT_DONE</p>
</header>
__CARDS__
<button id="export">내보내기</button>
<script>
  document.getElementById("export").onclick = () => {
    const esc = (s) => /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    const lines = ["item_id,round1,other,final,reason"];
    let empty = 0;
    for (const a of document.querySelectorAll("article")) {
      const f = a.querySelector("input[type=radio]:checked")?.value || "";
      if (!f) empty++;
      lines.push([a.dataset.id, a.dataset.r1, a.dataset.other, f, a.querySelector(".reason").value.trim()].map(esc).join(","));
    }
    if (empty) { alert(`최종 라벨이 빈 기록이 ${empty}건 있습니다.`); return; }
    const text = lines.join("\n") + "\n";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" }));
    link.download = "adjudication.csv";
    link.click();
    // 다운로드가 막힌 창에서는 내용을 띄워 복사할 수 있게 한다
    let box = document.querySelector("textarea");
    if (!box) { box = document.createElement("textarea"); document.body.appendChild(box); }
    box.value = text;
    box.select();
  };
</script>
</body>
</html>
"""

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["make"]:
        make()
    elif args[:1] == ["merge"] and len(args) == 2:
        merge(Path(args[1]))
    else:
        sys.exit(__doc__)
