"""README용 그래프 2장을 그린다.

    python scripts/plots.py

출력:
    results/figures/rq1_conditions.png   조건별 거짓 성공 탐지 건수와 실제 성공 오판 건수
    results/figures/rq2_marker.png       표식을 지웠을 때 성공 판정이 바뀐 건수 (방향별)

건수가 작아서 비율이 아니라 건수로 그리고, 막대마다 "건수/분모"를 적는다.
조건 비교는 analyze.py와 같이 모든 조건의 판정이 있는 같은 기록 묶음 안에서만 한다.
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results/figures"

# 기본 팔레트 1·2번(파랑·주황). 두 색의 색각 이상 구분은 검증기로 확인했다
BLUE, ORANGE = "#2a78d6", "#eb6834"
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"

from matplotlib import font_manager

# 설치된 한글 글꼴 중 첫 번째를 쓴다 (Windows 맑은 고딕, macOS, 리눅스 순)
_installed = {f.name for f in font_manager.fontManager.ttflist}
KOREAN = next((f for f in ("Malgun Gothic", "AppleGothic", "NanumGothic", "Noto Sans CJK KR") if f in _installed), "sans-serif")

plt.rcParams.update({
    "font.family": KOREAN,
    "axes.unicode_minus": False,
    "font.size": 10,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK_2,
    "xtick.color": INK_2,
    "ytick.color": INK_2,
    "text.color": INK,
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
})


def load():
    items = {it["id"]: it for it in map(json.loads, (ROOT / "data/items.jsonl").open(encoding="utf8"))}
    # 거짓 성공 = 결과 실패 × 표식이 완료 주장. 표식 없는 기록에는 거짓 성공이 없다(README 참고)
    false_success = {i for i, it in items.items() if not it["state_success"] and it["claim_auto"] == "CLAIM_DONE"}
    judges = defaultdict(dict)
    for f in sorted((ROOT / "judge").glob("*.jsonl")):
        for r in map(json.loads, f.open(encoding="utf8")):
            judges[(r["judge"], r["condition"], bool(r["marker"]))][r["item_id"]] = r
    return items, false_success, judges


def style(ax):
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)


def bar_labels(ax, bars, texts):
    for b, t in zip(bars, texts):
        ax.annotate(t, (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3),
                    textcoords="offset points", ha="center", va="bottom", fontsize=9, color=INK)


def rq1(items, false_success, judges):
    names = sorted({k[0] for k in judges})
    fig, axes = plt.subplots(2, len(names), figsize=(4.2 * len(names), 6.2), squeeze=False)
    for col, judge in enumerate(names):
        conds = [c for c in "ABC" if (judge, c, True) in judges]
        ids = set.intersection(*(set(judges[(judge, c, True)]) for c in conds))
        fs = ids & false_success
        ok = {i for i in ids if items[i]["state_success"]}
        for row, (subset, color, title) in enumerate([
            (fs, BLUE, f"거짓 성공을 실패로 잡음 (분모 {len(fs)})"),
            (ok, ORANGE, f"실제 성공을 실패로 봄 (분모 {len(ok)})"),
        ]):
            ax = axes[row][col]
            counts = [sum(not judges[(judge, c, True)][i]["success"] for i in subset) for c in conds]
            bars = ax.bar(conds, counts, width=0.5, color=color, edgecolor=SURFACE, linewidth=2)
            bar_labels(ax, bars, [f"{n}/{len(subset)}" for n in counts])
            ax.set_ylim(0, max(len(subset) * 1.0, max(counts) * 1.25, 1))
            ax.set_title(title, fontsize=10, color=INK_2, loc="left")
            style(ax)
            if row == 0:
                ax.text(0, 1.22, f"{judge} · 공통 {len(ids)}건", transform=ax.transAxes,
                        fontsize=11, fontweight="bold", color=INK)
    fig.text(0.01, 0.005, "A 지시문+답변 · B A+조작 순서 · C B+최종 화면. 표식이 있는 입력 기준. "
             "GPT-OSS는 이미지를 읽지 못해 C가 없다.", fontsize=8.5, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 1), h_pad=2.5)
    fig.savefig(OUT / "rq1_conditions.png", dpi=160)
    plt.close(fig)


def rq2(items, false_success, judges):
    rows = []
    for (judge, cond, has_marker), with_m in sorted(judges.items()):
        if not has_marker or (judge, cond, False) not in judges:
            continue
        without = judges[(judge, cond, False)]
        ids = [i for i in with_m if i in without and items[i]["marker"]]
        pushed = sum(with_m[i]["success"] and not without[i]["success"] for i in ids)
        pulled = sum(not with_m[i]["success"] and without[i]["success"] for i in ids)
        rows.append((f"{judge} · {cond}", len(ids), pushed, pulled))

    fig, ax = plt.subplots(figsize=(8, 0.55 * len(rows) + 1.6))
    labels = [r[0] for r in rows][::-1]
    pushed = [r[2] for r in rows][::-1]
    pulled = [r[3] for r in rows][::-1]
    y = range(len(rows))
    b1 = ax.barh(y, pushed, height=0.55, color=BLUE, edgecolor=SURFACE, linewidth=2, label="표식이 있을 때만 성공")
    b2 = ax.barh(y, pulled, left=pushed, height=0.55, color=ORANGE, edgecolor=SURFACE, linewidth=2, label="표식이 없을 때만 성공")
    ax.set_yticks(list(y), labels)
    for k, r in enumerate(rows[::-1]):
        ax.annotate(f"{r[2] + r[3]}건 / 비교 {r[1]}건", (r[2] + r[3], k), xytext=(4, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK)
    ax.spines[["top", "right", "bottom"]].set_visible(False)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(length=0)
    ax.set_xlim(0, max((p + q) for p, q in zip(pushed, pulled)) * 1.35 + 1)
    ax.set_xlabel("성공 판정이 바뀐 기록 수")
    ax.legend(loc="lower right", frameon=False)
    ax.set_title("RESULT 표식을 지웠을 때 성공 판정이 바뀐 건수", loc="left", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(OUT / "rq2_marker.png", dpi=160)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = load()
    rq1(*data)
    rq2(*data)
    print(f"{OUT.relative_to(ROOT)}: rq1_conditions.png, rq2_marker.png")


if __name__ == "__main__":
    main()
