"""후보 전략 비교 표 → result_candidates.md 본문 (MC 제외)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.candidate_strategies as C  # noqa: E402

ASOF = "2026-07-01"
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]
ORDER = ["우리 최종안 80/20", "Gayed LRS (200MA)", "Gayed LRS (우리 기울기+스트릭5)",
         "HFEA (UPRO55/TMF45)", "VAA (13612W)", "ADM (원전)", "ADM (3배 치환)",
         "GEM", "영구 포트폴리오", "QQQ 단순보유"]
SYNTH_NOTE = {"VAA (13612W)": "EEM/EFA 합성(2003↓)", "ADM (원전)": "SCZ 합성(2007↓)",
              "ADM (3배 치환)": "SCZ 합성(2007↓)·TMF", "GEM": "VEU 합성(2007↓)",
              "HFEA (UPRO55/TMF45)": "TMF/TLT 합성(2009↓)", "영구 포트폴리오": "TLT/GLD 합성",
              "Gayed LRS (200MA)": "UPRO 합성(2009↓)", "Gayed LRS (우리 기울기+스트릭5)": "UPRO 합성",
              "우리 최종안 80/20": "3x/SOXL 합성", "QQQ 단순보유": "실데이터"}


def yearly(nav):
    s = C.norm(nav); yl = s.groupby(s.index.year).last()
    out, prev = {}, s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = yl[y] / prev - 1; prev = yl[y]
    return out


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def main():
    spine = C.B.load("QQQ").loc[:ASOF].index
    navs = C.build_all(spine)

    print("# 후보 전략 검증 — 우리 80/20 vs 6종(+변형)\n")
    print("> 동일 데이터(1999~2026 `*_SYNTH`)·비용(편도 0.12%)·스트레스4·MC. "
          "채권 듀레이션모델·해외 EFA프록시 합성(상장 이전).\n")

    print("## 종합 (전체구간 / 2013~) + 회전율 + 합성여부\n")
    print("| 전략 | CAGR | MDD | 샤프 | 2013~ CAGR | 2013~ 샤프 | 회전율(/년) | 합성 |")
    print("|---|---:|---:|---:|---:|---:|---:|---|")
    for n in ORDER:
        nav, turn = navs[n]
        m = compute(nav); m13 = compute(nav.loc["2013-01-01":])
        print(f"| {n} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pf(m13['CAGR'])} | {m13['Sharpe']:.2f} | {turn:.1f} | {SYNTH_NOTE.get(n,'')} |")
    print()

    print("## 스트레스 4구간 (총수익 / MDD) — HFEA 주목\n")
    print("| 전략 | " + " | ".join(n for _, _, n in STRESS) + " |")
    print("|---" * (len(STRESS) + 1) + "|")
    for n in ORDER:
        cells = []
        for s, e, _ in STRESS:
            m = compute(navs[n][0].loc[s:e])
            cells.append(f"{pf(m['TotalReturn'])} / {pf(m['MDD'])}")
        print(f"| {n} | " + " | ".join(cells) + " |")
    print()

    print("## 최악의 해 · 세후+파킹 CAGR\n")
    print("| 전략 | 최악의 해 | 세전 CAGR | 세후+파킹 CAGR | ΔCAGR |")
    print("|---|---:|---:|---:|---:|")
    for n in ORDER:
        nav = navs[n][0]
        yy = yearly(nav); wy = min(yy, key=yy.get)
        pre = compute(nav)["CAGR"]
        at = PB.aftertax_overlay(nav)
        post = (C.norm(at).iloc[-1]) ** (1 / ((nav.index[-1]-nav.index[0]).days/365.25)) - 1
        print(f"| {n} | {pf(yy[wy])} ({wy}) | {pf(pre)} | {pf(post)} | {(post-pre)*100:+.2f}%p |")
    print()


if __name__ == "__main__":
    main()
