"""공격형 프런티어 (카나리아 코어) — 배분 {80/20,70/30,60/40,50/50} × 금 {0%,15%}.
CAGR/MDD/샤프/최악의 해/세후+파킹. (MC는 monte_carlo_frontier_v10.py)"""
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
ALLOCS = [0.80, 0.70, 0.60, 0.50]
GOLDS = [0.0, 0.15]


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


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary_gate(spine, k=4)
    core = core_nav(spine, gate)
    _, _, sats = PB.build_sleeves(spine)
    sat = sats["위성(SOXL포함)"]
    gld = B.R("GLD").reindex(spine).ffill()

    print("### 부록 C. 공격형 프런티어 (카나리아 코어) — 배분 × 금\n")
    print("| 코어/위성 | 금 | CAGR | MDD | 샤프 | 최악의 해 | 세후+파킹 CAGR |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for w in ALLOCS:
        risky = PB.blend(core, sat, w)
        for g in GOLDS:
            if g == 0:
                nav = risky
            else:
                nav = B.static_portfolio({"OUR": 1 - g, "GLD": g},
                                         {"OUR": risky, "GLD": gld}, spine, "A")
            m = compute(nav)
            yy = yearly(nav); wy = min(yy, key=yy.get)
            print(f"| {int(w*100)}/{int((1-w)*100)} | {int(g*100)}% | {pf(m['CAGR'])} | "
                  f"{pf(m['MDD'])} | {m['Sharpe']:.2f} | {pf(yy[wy])} ({wy}) | "
                  f"{pf(aftertax_cagr(nav))} |")
    print()


if __name__ == "__main__":
    main()
