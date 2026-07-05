"""벤치마크 8종 비교 표 → result_benchmark.md 본문 (MC 제외)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402

ASOF = "2026-07-01"
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]
ORDER = ["우리 최종안 80/20", "HFEA (UPRO55/TMF45)", "Gayed 로테이션", "듀얼모멘텀 GEM",
         "60/40", "영구 포트폴리오", "정석 무한매수법 v1", "QQQ 단순보유"]


def yearly(nav):
    s = B.norm(nav); yl = s.groupby(s.index.year).last()
    out, prev = {}, s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = yl[y] / prev - 1; prev = yl[y]
    return out


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    navs = B.build_all(spine)

    print("# 공개 유명 전략 벤치마크 — 우리 최종안(80/20) vs 8종\n")
    print("> 동일 데이터(1999~2026 `*_SYNTH`)·동일 비용(편도 0.12%)·동일 스트레스 4구간·동일 MC. "
          "채권/금/해외는 합성(듀레이션·futures) 스플라이스.\n")

    print("## 종합 비교 (전체구간 1999~2026)\n")
    print("| 전략 | CAGR | MDD | 샤프 | 최악의 해 | 2022년 |")
    print("|---|---:|---:|---:|---:|---:|")
    ys = {n: yearly(navs[n]) for n in ORDER}
    for n in ORDER:
        m = compute(navs[n])
        yy = ys[n]; wy = min(yy, key=yy.get)
        print(f"| {n} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pf(yy[wy])} ({wy}) | {pf(yy.get(2022, float('nan')))} |")
    print()

    print("## 스트레스 4구간 (총수익 / MDD)\n")
    print("| 전략 | " + " | ".join(n for _, _, n in STRESS) + " |")
    print("|---" * (len(STRESS) + 1) + "|")
    for n in ORDER:
        cells = []
        for s, e, _ in STRESS:
            m = compute(navs[n].loc[s:e])
            cells.append(f"{pf(m['TotalReturn'])} / {pf(m['MDD'])}")
        print(f"| {n} | " + " | ".join(cells) + " |")
    print()

    print("## 2013~2026 (최근 국면)\n")
    print("| 전략 | CAGR | MDD | 샤프 |")
    print("|---|---:|---:|---:|")
    for n in ORDER:
        m = compute(navs[n].loc["2013-01-01":])
        print(f"| {n} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} |")
    print()

    # 2022 상세 (채권 헤지 붕괴 확인)
    print("## 2022년 상세 (채권 헤지 전략 점검)\n")
    print("| 전략 | 2022 수익 | 2022 MDD |")
    print("|---|---:|---:|")
    for n in ORDER:
        m = compute(navs[n].loc["2022-01-01":"2022-12-31"])
        print(f"| {n} | {pf(m['TotalReturn'])} | {pf(m['MDD'])} |")
    print()

    # 부품 실험: 우리 최종안에 금/장기채를 얹으면?
    print("## 부품 실험 — 우리 80/20에 금·장기채를 더하면? (연 리밸런스)\n")
    addon_prices = {"OUR": navs["우리 최종안 80/20"], "GLD": B.R("GLD"), "TLT": B.R("TLT")}
    addons = {"기본 (80/20)": {"OUR": 1.0},
              "+ 금 15%": {"OUR": 0.85, "GLD": 0.15},
              "+ 장기채 15%": {"OUR": 0.85, "TLT": 0.15},
              "+ 금7.5%+장기채7.5%": {"OUR": 0.85, "GLD": 0.075, "TLT": 0.075}}
    print("| 구성 | CAGR | MDD | 샤프 | 닷컴 | GFC | 2022 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for lab, w in addons.items():
        nav = B.static_portfolio(w, addon_prices, spine, "A")
        m = compute(nav)
        dot = compute(nav.loc["2000-01-01":"2002-12-31"])["TotalReturn"]
        gfc = compute(nav.loc["2007-10-01":"2009-03-31"])["TotalReturn"]
        y22 = compute(nav.loc["2022-01-01":"2022-12-31"])["TotalReturn"]
        print(f"| {lab} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pf(dot)} | {pf(gfc)} | {pf(y22)} |")
    print()


if __name__ == "__main__":
    main()
