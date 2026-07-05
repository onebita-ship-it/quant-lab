"""공격형 프런티어 2 (카나리아 코어) — 배분 {70/30..0/100} × 금 {0%,10%}, 100% 투입(리저브 없음).
세전/세후 CAGR·MDD·샤프·최악의 해·연도별. (MC는 monte_carlo_frontier2_v10.py)"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402
from backtests.v10_experiments import canary_gate, core_nav  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
ALLOCS = [0.70, 0.60, 0.50, 0.40, 0.30, 0.00]
GOLDS = [0.0, 0.10]
STRESS = [("2000-01-01", "2002-12-31", "닷컴"), ("2007-10-01", "2009-03-31", "GFC"),
          ("2020-02-01", "2020-06-30", "COVID"), ("2022-01-01", "2022-12-31", "2022")]


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def yearly(nav):
    s = PB.norm(nav); yl = s.groupby(s.index.year).last()
    out, prev = {}, s.iloc[0]
    for y in sorted(s.index.year.unique()):
        out[y] = yl[y] / prev - 1; prev = yl[y]
    return out


def aftertax_cagr(nav):
    at = PB.aftertax_overlay(nav)
    yrs = (nav.index[-1] - nav.index[0]).days / 365.25
    return PB.norm(at).iloc[-1] ** (1 / yrs) - 1


def build(spine):
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    core = core_nav(spine, trend & canary_gate(spine, k=4))
    _, _, sats = PB.build_sleeves(spine)
    sat = sats["위성(SOXL포함)"]
    gld = B.R("GLD").reindex(spine).ffill()
    navs = {}
    for w in ALLOCS:
        risky = PB.blend(core, sat, w)
        for g in GOLDS:
            nav = risky if g == 0 else B.static_portfolio(
                {"OUR": 1 - g, "GLD": g}, {"OUR": risky, "GLD": gld}, spine, "A")
            navs[(w, g)] = PB.norm(nav)
    return navs


def lab(w, g):
    return f"{int(w*100)}/{int((1-w)*100)} · 금{int(g*100)}%"


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    navs = build(spine)

    print("## 1. 종합 (카나리아 코어, 100% 투입, 리저브 없음)\n")
    print("| 코어/위성 · 금 | 세전 CAGR | 세후+파킹 CAGR | MDD | 샤프 | 최악의 해 |")
    print("|---|---:|---:|---:|---:|---:|")
    for w in ALLOCS:
        for g in GOLDS:
            nav = navs[(w, g)]; m = compute(nav)
            yy = yearly(nav); wy = min(yy, key=yy.get)
            print(f"| {lab(w,g)} | {pf(m['CAGR'])} | {pf(aftertax_cagr(nav))} | {pf(m['MDD'])} | "
                  f"{m['Sharpe']:.2f} | {pf(yy[wy])} ({wy}) |")
    print()

    for g in GOLDS:
        print(f"## 2. 연도별 수익률 — 금 {int(g*100)}% (굵게=2025·2026)\n")
        cols = [w for w in ALLOCS]
        ys = {w: yearly(navs[(w, g)]) for w in cols}
        print("| 연도 | " + " | ".join(f"{int(w*100)}/{int((1-w)*100)}" for w in cols) + " |")
        print("|---" * (len(cols) + 1) + "|")
        for y in sorted(spine.year.unique()):
            emph = "**" if y in (2025, 2026) else ""
            ylab = f"{y} YTD" if y == 2026 else str(y)
            cells = [f"{emph}{ylab}{emph}"]
            for w in cols:
                cells.append(f"{emph}{pf(ys[w].get(y, float('nan')))}{emph}")
            print("| " + " | ".join(cells) + " |")
        print()

    print("## 3. 스트레스 4구간 (총수익 / MDD) — 전 조합\n")
    print("| 코어/위성 · 금 | " + " | ".join(n for _, _, n in STRESS) + " |")
    print("|---" * (len(STRESS) + 1) + "|")
    for w in ALLOCS:
        for g in GOLDS:
            nav = navs[(w, g)]
            cells = [f"{pf(compute(nav.loc[s:e])['TotalReturn'])} / "
                     f"{pf(compute(nav.loc[s:e])['MDD'])}" for s, e, _ in STRESS]
            print(f"| {lab(w,g)} | " + " | ".join(cells) + " |")
    print()


if __name__ == "__main__":
    main()
