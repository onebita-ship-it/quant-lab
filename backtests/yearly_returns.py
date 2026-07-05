"""연도별 수익률 표 — 최종 견고안(기울기+스트릭5·분할40·익절15%·쿼터손절).

각 해(1999~2026 YTD 7/1)마다:
  ① 최종안 100% 투입        ② 67%+리저브(-30/-50%)
  ③ 세후개인+파킹ON(100%)   ④ QQQ Buy&Hold           ⑤ TQQQ Buy&Hold
연도별 사이클 수·승률(①기준)도 함께.

세전 ①②는 tax_parking_backtest의 '세전 베이스'(환전·배당세·세금·파킹 모두 OFF),
③은 개인 양도세 + SGOV 파킹 ON. B&H는 종가 단순 보유(TQQQ는 SYNTH, 2010이전 합성).

사용: python backtests/yearly_returns.py   → result_yearly.md 표 출력
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests import tax_parking_backtest as TP  # noqa: E402

ASOF = "2026-07-01"


def yearly_from_equity(series):
    """연도별 수익률: 각 해 마지막값/직전 해 마지막값-1 (첫 해는 시점0 대비)."""
    s = series.sort_index()
    year_last = s.groupby(s.index.year).last()
    out = {}
    prev = s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = year_last[y] / prev - 1
        prev = year_last[y]
    return out


def cagr(series):
    yrs = (series.index[-1] - series.index[0]).days / 365.25
    return (series.iloc[-1] / series.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else 0.0


def cycles_by_year(cycles):
    """연도별 (종료 사이클수, 승률). 미청산(eof)은 제외."""
    from collections import defaultdict
    tot, win = defaultdict(int), defaultdict(int)
    for c in cycles:
        if c.reason == "eof":
            continue
        y = c.end.year
        tot[y] += 1
        if c.pnl_pct > 0:
            win[y] += 1
    return tot, win


def main():
    tqqq = TP.load_close("TQQQ_SYNTH").loc[:ASOF]
    qqq = TP.load_close("QQQ").loc[:ASOF]
    trend = TP.trend_of(qqq, tqqq.index)

    base = TP.Opts(tax_mode="none", parking=False, fx_fee=False, div_tax=False)
    aftertax = TP.Opts(tax_mode="individual", parking=True)

    eq100, cyc100, _ = TP.run_engine(tqqq, trend, 1.00, (), opts=base)
    eq67, _, _ = TP.run_engine(tqqq, trend, 0.67, (-0.30, -0.50), opts=base)
    eqtax, _, _ = TP.run_engine(tqqq, trend, 1.00, (), opts=aftertax)

    y100 = yearly_from_equity(eq100)
    y67 = yearly_from_equity(eq67)
    ytax = yearly_from_equity(eqtax)
    yqqq = yearly_from_equity(qqq)
    ytqqq = yearly_from_equity(tqqq)
    tot, win = cycles_by_year(cyc100)

    years = sorted(y100)

    def p(x):
        return f"{x:+.1%}"

    print("# 최종 견고안 — 연도별 수익률 (1999 ~ 2026 YTD 7/1)\n")
    print("> 전략=기울기+스트릭5·분할40·익절15%·쿼터손절 · 신호=QQQ 200일선 · "
          "데이터=`TQQQ_SYNTH`(2010이전 합성)\n")
    print("| 연도 | ① 최종안 100% | ② 67%+리저브 | ③ 세후개인+파킹ON | "
          "④ QQQ B&H | ⑤ TQQQ B&H | 사이클수 | 승률 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for y in years:
        n = tot.get(y, 0)
        wr = f"{win.get(y,0)/n:.0%}" if n else "—"
        emph = "**" if y in (2025, 2026) else ""
        ylabel = f"{y} YTD" if y == 2026 else str(y)
        cells = [f"{emph}{ylabel}{emph}",
                 f"{emph}{p(y100[y])}{emph}", f"{emph}{p(y67[y])}{emph}",
                 f"{emph}{p(ytax[y])}{emph}", f"{emph}{p(yqqq[y])}{emph}",
                 f"{emph}{p(ytqqq[y])}{emph}", f"{emph}{n}{emph}", f"{emph}{wr}{emph}"]
        print("| " + " | ".join(cells) + " |")

    # 누적 요약
    print("\n**누적 (1999~2026 YTD)**\n")
    print("| 지표 | ① 최종안 100% | ② 67%+리저브 | ③ 세후개인+파킹ON | "
          "④ QQQ B&H | ⑤ TQQQ B&H |")
    print("|---|---:|---:|---:|---:|---:|")
    print(f"| CAGR | {cagr(eq100):.1%} | {cagr(eq67):.1%} | {cagr(eqtax):.1%} | "
          f"{cagr(qqq):.1%} | {cagr(tqqq):.1%} |")
    tr = lambda s: s.iloc[-1] / s.iloc[0] - 1  # noqa: E731
    print(f"| 총수익 | {tr(eq100):.0%} | {tr(eq67):.0%} | {tr(eqtax):.0%} | "
          f"{tr(qqq):.0%} | {tr(tqqq):.0%} |")
    from backtests.metrics import compute
    m100, m67, mtax = compute(eq100, cyc100), compute(eq67), compute(eqtax)
    mq = compute(qqq); mt = compute(tqqq)
    print(f"| MDD | {m100['MDD']:.1%} | {m67['MDD']:.1%} | {mtax['MDD']:.1%} | "
          f"{mq['MDD']:.1%} | {mt['MDD']:.1%} |")
    print(f"| 샤프 | {m100['Sharpe']:.2f} | {m67['Sharpe']:.2f} | {mtax['Sharpe']:.2f} | "
          f"{mq['Sharpe']:.2f} | {mt['Sharpe']:.2f} |")
    closed = sum(tot.values()); wins = sum(win.values())
    print(f"\n총 종료 사이클 {closed}건, 전체 승률 {wins/closed:.0%} (①기준).")


if __name__ == "__main__":
    main()
