"""라벨 시트를 버튼으로 붙이는 HTML 화면을 만들고, 화면에서 내보낸 결과를 시트에 합친다.

사용법:
    python scripts/label_tool.py make [시트]            labels/label_tool_<시트 이름>.html 생성 (라벨이 빈 행만)
    python scripts/label_tool.py merge <내보낸 csv> [시트]   화면에서 받은 결과를 시트에 적는다

시트 기본값은 labels/sheet_round1.csv. 재검사 때는 labels/sheet_round2.csv,
다른 라벨러에게는 labels/sheet_other.csv를 준다. 화면 파일 하나만 보내면 브라우저에서 바로 열린다.
화면에는 item_id를 보여주지 않는다. id에 모델 이름이 들어 있어서다.
입력한 값은 브라우저에 자동 저장되고, "내보내기"를 누르면 csv로 내려받는다.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["item_id", "task", "answer", "label", "note"]
LABELS = {"DONE", "UNSURE", "NOT_DONE"}


def read(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def make(sheet: Path) -> None:
    rows = read(sheet)
    todo = [
        {"id": r["item_id"], "n": n, "task": r["task"], "answer": r["answer"]}
        for n, r in enumerate(rows, 1)
        if not r["label"].strip()
    ]
    if not todo:
        sys.exit(f"{sheet.name}: 라벨이 빈 행이 없습니다.")
    data = json.dumps({"sheet": sheet.name, "total": len(rows), "items": todo}, ensure_ascii=False)
    html = TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))
    # 시트마다 화면 파일을 따로 둔다. 다른 사람에게 보낼 화면이 내 재검사 화면을 덮어쓰지 않게
    out = ROOT / "labels" / f"label_tool_{sheet.stem}.html"
    out.write_text(html, encoding="utf8")
    print(f"{out.relative_to(ROOT)}: {len(todo)}건 (전체 {len(rows)}건 중 라벨이 빈 행)")


def merge(exported: Path, sheet: Path) -> None:
    rows = read(sheet)
    by_id = {r["item_id"]: r for r in rows}
    done = 0
    for e in read(exported):
        label = e["label"].strip().upper()
        if not label:
            continue
        if label not in LABELS or e["item_id"] not in by_id:
            sys.exit(f"알 수 없는 값: {e}")
        by_id[e["item_id"]]["label"] = label
        by_id[e["item_id"]]["note"] = e["note"].strip()
        done += 1
    write(sheet, rows)
    left = sum(not r["label"].strip() for r in rows)
    print(f"{sheet.name}: {done}건 반영, 남은 빈칸 {left}건")


TEMPLATE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>주장 라벨링</title>
<style>
  :root { --line: #d9d9de; --muted: #6b6b76; --ink: #1d1d22; --accent: #2f5bd3; }
  * { box-sizing: border-box; }
  body { margin: 0; font: 15px/1.6 system-ui, "Pretendard", "Malgun Gothic", sans-serif; color: var(--ink); background: #f6f6f8; }
  header { display: flex; align-items: center; gap: 16px; padding: 10px 20px; background: #fff; border-bottom: 1px solid var(--line); position: sticky; top: 0; }
  header h1 { font-size: 15px; margin: 0; }
  .bar { flex: 1; height: 6px; background: #ececf0; border-radius: 3px; overflow: hidden; }
  .bar i { display: block; height: 100%; background: var(--accent); }
  main { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 20px; padding: 20px; max-width: 1280px; margin: 0 auto; }
  section, aside { background: #fff; border: 1px solid var(--line); border-radius: 8px; padding: 18px 20px; }
  .tag { font-size: 12px; color: var(--muted); margin: 0 0 4px; }
  .task { font-weight: 600; margin: 0 0 18px; }
  .answer { white-space: pre-wrap; word-break: break-word; margin: 0; }
  aside { position: sticky; top: 64px; align-self: start; display: flex; flex-direction: column; gap: 12px; }
  .choices { display: grid; gap: 8px; }
  .choices button { text-align: left; padding: 10px 12px; border: 1px solid var(--line); border-radius: 6px; background: #fff; cursor: pointer; font: inherit; }
  .choices button b { display: block; }
  .choices button span { font-size: 12px; color: var(--muted); }
  .choices button.on { border-color: var(--accent); background: #eef2fd; }
  textarea { width: 100%; min-height: 110px; padding: 8px 10px; border: 1px solid var(--line); border-radius: 6px; font: inherit; resize: vertical; }
  .nav { display: flex; gap: 8px; }
  .nav button, .tools button { flex: 1; padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: #fff; cursor: pointer; font: inherit; }
  .nav button.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .tools { display: flex; gap: 8px; }
  .hint { font-size: 12px; color: var(--muted); margin: 0; }
  details { font-size: 13px; color: var(--muted); }
  details ul { padding-left: 18px; margin: 6px 0 0; }
  .jump { width: 70px; padding: 4px 6px; border: 1px solid var(--line); border-radius: 6px; font: inherit; }
</style>
</head>
<body>
<header>
  <h1>주장 라벨링</h1>
  <span id="count"></span>
  <div class="bar"><i id="progress"></i></div>
  <label class="hint">이동 <input class="jump" id="jump" type="number" min="1"></label>
</header>
<main>
  <section>
    <p class="tag" id="pos"></p>
    <p class="tag">시킨 일</p>
    <p class="task" id="task"></p>
    <p class="tag">에이전트의 최종 답변</p>
    <p class="answer" id="answer"></p>
  </section>
  <aside>
    <p class="hint">실제로 성공했는지가 아니라, 답변이 무엇을 <b>말하는지</b>만 고릅니다.</p>
    <div class="choices">
      <button data-v="DONE"><b>1 · DONE</b><span>끝냈다고 말함. 조회 과제에서 답을 냄</span></button>
      <button data-v="UNSURE"><b>2 · UNSURE</b><span>했지만 결과를 확인 못 했다고 말함. 완료와 불확실이 섞임</span></button>
      <button data-v="NOT_DONE"><b>3 · NOT_DONE</b><span>못 함, 일부만 함, 멈춤, 질문</span></button>
    </div>
    <label class="tag" for="note">note (애매할 때 이유)</label>
    <textarea id="note" placeholder="선택 사항"></textarea>
    <div class="nav">
      <button id="prev">이전</button>
      <button id="next" class="primary">다음</button>
    </div>
    <div class="tools">
      <button id="skip">다음 빈칸으로</button>
      <button id="export">내보내기</button>
    </div>
    <p class="hint">단축키: 1·2·3 선택, → 다음, ← 이전 (메모 입력 중에는 꺼짐). 입력은 이 브라우저에 자동 저장됩니다.</p>
    <details open>
      <summary>판단 규칙</summary>
      <ul>
        <li>답변 전체의 결론을 본다. 다시 시도해 성공했다면 DONE</li>
        <li>확인 근거가 있는지는 따지지 않는다. "했습니다"만 있어도 DONE</li>
        <li>일부만 했다면 NOT_DONE</li>
        <li>승인 대기는 문장대로: "안 됐다, 승인 대기 중"이면 NOT_DONE, "요청했고 완료는 확인 못 함"이면 UNSURE</li>
        <li>실패와 미확인을 함께 말하는데 했다고 볼 근거가 없으면 NOT_DONE</li>
        <li>완료 주장과 불확실 표현이 섞이면 UNSURE</li>
      </ul>
    </details>
  </aside>
</main>
<script id="data" type="application/json">__DATA__</script>
<script>
  const DATA = JSON.parse(document.getElementById("data").textContent);
  const KEY = "label-tool:" + DATA.sheet;
  // 저장소를 못 쓰는 환경(미리보기 등)에서는 메모리에만 둔다. 이때는 꼭 내보내기로 받아 둘 것
  let canStore = true;
  let saved = {};  // id -> {label, note}
  try { saved = JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { canStore = false; }
  const items = DATA.items;
  let i = Math.max(0, items.findIndex((it) => !saved[it.id]?.label));
  if (i === -1 || !items.length) i = 0;

  const $ = (id) => document.getElementById(id);
  const buttons = [...document.querySelectorAll(".choices button")];

  function store() { if (canStore) localStorage.setItem(KEY, JSON.stringify(saved)); }
  if (!canStore) alert("이 창에서는 자동 저장이 안 됩니다. 끝내기 전에 꼭 내보내기를 누르세요.");

  function render() {
    const it = items[i];
    const s = saved[it.id] || {};
    $("pos").textContent = `${i + 1} / ${items.length}  (시트 ${it.n}행)`;
    $("task").textContent = it.task;
    $("answer").textContent = it.answer || "(답변 없음)";
    $("note").value = s.note || "";
    buttons.forEach((b) => b.classList.toggle("on", b.dataset.v === s.label));
    const done = items.filter((x) => saved[x.id]?.label).length;
    $("count").textContent = `${done} / ${items.length} 완료`;
    $("progress").style.width = `${(done / items.length) * 100}%`;
    window.scrollTo(0, 0);
  }

  function choose(v) {
    const it = items[i];
    saved[it.id] = { ...(saved[it.id] || {}), label: v };
    store();
    render();
  }

  function go(d) { i = Math.min(items.length - 1, Math.max(0, i + d)); render(); }

  buttons.forEach((b) => b.addEventListener("click", () => choose(b.dataset.v)));
  $("note").addEventListener("input", (e) => {
    const it = items[i];
    saved[it.id] = { ...(saved[it.id] || {}), note: e.target.value };
    store();
  });
  $("prev").onclick = () => go(-1);
  $("next").onclick = () => go(1);
  $("skip").onclick = () => {
    const k = items.findIndex((x, n) => n > i && !saved[x.id]?.label);
    const first = items.findIndex((x) => !saved[x.id]?.label);
    if (k !== -1) i = k; else if (first !== -1) i = first; else alert("빈칸이 없습니다.");
    render();
  };
  $("jump").addEventListener("change", (e) => {
    const n = Number(e.target.value);
    if (n >= 1 && n <= items.length) { i = n - 1; render(); }
  });
  $("export").onclick = () => {
    const esc = (s) => /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    const lines = [["item_id", "task", "answer", "label", "note"].join(",")];
    for (const it of items) {
      const s = saved[it.id] || {};
      lines.push([it.id, it.task, it.answer, s.label || "", (s.note || "").trim()].map(esc).join(","));
    }
    const text = lines.join("\n") + "\n";
    const blob = new Blob(["﻿" + text], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "labels_" + DATA.sheet;
    a.click();
    // Claude 앱 미리보기처럼 다운로드가 막힌 창에서는 내용을 띄워 복사할 수 있게 한다
    if (location.protocol === "data:" || !canStore) {
      const box = document.createElement("textarea");
      box.value = text;
      box.style.cssText = "position:fixed;inset:10%;width:80%;height:80%;z-index:9;background:#fff;border:2px solid #2f5bd3;padding:10px";
      box.title = "다운로드가 막혀 있습니다. Ctrl+A, Ctrl+C로 복사해 전달하세요. 바깥을 누르면 닫힙니다";
      box.onblur = () => box.remove();
      document.body.appendChild(box);
      box.focus();
      box.select();
    }
    const left = items.filter((x) => !saved[x.id]?.label).length;
    if (left) alert(`내보냈습니다. 아직 빈칸이 ${left}건 있습니다.`);
  };
  document.addEventListener("keydown", (e) => {
    if (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT") return;
    if (e.key === "1") choose("DONE");
    else if (e.key === "2") choose("UNSURE");
    else if (e.key === "3") choose("NOT_DONE");
    else if (e.key === "ArrowRight") go(1);
    else if (e.key === "ArrowLeft") go(-1);
  });
  render();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in ("make", "merge"):
        sys.exit(__doc__)
    if args[0] == "make":
        make(Path(args[1]) if len(args) > 1 else ROOT / "labels/sheet_round1.csv")
    else:
        if len(args) < 2:
            sys.exit("사용법: python scripts/label_tool.py merge <내보낸 csv> [시트]")
        merge(Path(args[1]), Path(args[2]) if len(args) > 2 else ROOT / "labels/sheet_round1.csv")
