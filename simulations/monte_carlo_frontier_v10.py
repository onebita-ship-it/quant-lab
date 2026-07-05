"""공격형 프런티어 MC — 배분×금 8조합(카나리아 코어) 5년 분포·반토막확률.
경로별 core/sat/gold 1회 계산 후 8조합 블렌드(공유)."""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.benchmark_strategies as B  # noqa: E402
import backtests.candidate_strategies as C  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402
from simulations.monte_carlo_v9 import joint_blocks  # noqa: E402

WARMUP = 250
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "SGOV", "GLD", "EFA", "EEM", "AGG"]
META = {"TQQQ": {"class": "core", "index": "QQQ"}, "UPRO": {"class": "core", "index": "SPY"},
        "SOXL": {"class": "satellite", "index": "SOXX"}}
ALLOCS = [0.80, 0.70, 0.60, 0.50]
GOLDS = [0.0, 0.15]


def core_sat_gold(P, trade_dates):
    off = {a: P[a].reindex(trade_dates) for a in ["SPY", "EFA", "EEM", "AGG"]}
    me = sorted(B._periods(trade_dates, "M"))
    can = pd.Series(index=trade_dates, dtype="float64")
    for d in me:
        can[d] = 0.0 if any(C.w13612(off[a], d) < 0 for a in off) else 1.0
    can = can.reindex(trade_dates).ffill().fillna(1.0).astype(bool)
    trend = v6.trend_signal_v6(P["QQQ"], trade_dates, require_rising=True, confirm_days=5).astype(bool)
    tq = P["TQQQ"].reindex(trade_dates)
    core = PB.norm(TP.run_engine(tq, trend & can, 1.0, (), opts=TP.Opts("none", False, False, False))[0])
    sp = {"SGOV": P["SGOV"].reindex(trade_dates), "QQQ": P["QQQ"].reindex(trade_dates), "TQQQ": tq}
    sig, mom = {}, {}
    for a in META:
        c = META[a]["index"]; sp[a] = P[a].reindex(trade_dates)
        sig[a] = v6.trend_signal_v6(P[c], trade_dates, require_rising=True, confirm_days=5)
        mom[a] = E9.blended_mom(sp[a])
    dd = pd.concat([E9.trailing_dd(P["QQQ"], trade_dates), E9.trailing_dd(P["SPY"], trade_dates)],
                   axis=1).min(axis=1)
    sat = PB.norm(E9.run_2engine(trade_dates, sp, sig, mom, META, dd, use_engineB=False)[0])
    return core, sat, P["GLD"].reindex(trade_dates)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    series = {c: B.R(c) for c in COLS}
    df = pd.concat([series[c].pct_change() for c in COLS], axis=1, keys=COLS).dropna()
    mat = df.values; warm = mat[-WARMUP:]
    n_days = args.years * 252
    sim = joint_blocks(mat, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]

    combos = [(w, g) for w in ALLOCS for g in GOLDS]
    acc = {c: {"r": [], "m": []} for c in combos}
    for i in range(args.paths):
        full = np.vstack([warm, sim[i]])
        P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
             for k, c in enumerate(COLS)}
        core, sat, gld = core_sat_gold(P, trade_dates)
        for w in ALLOCS:
            risky = PB.blend(core, sat, w)
            for g in GOLDS:
                nav = risky if g == 0 else B.static_portfolio(
                    {"OUR": 1 - g, "GLD": g}, {"OUR": risky, "GLD": gld}, trade_dates, "A")
                nav = PB.norm(nav)
                acc[(w, g)]["r"].append(nav.iloc[-1] / nav.iloc[0] - 1)
                acc[(w, g)]["m"].append((nav / nav.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    print(f"\n### 부록 C-MC. 프런티어 몬테카를로 (1억, {args.paths}경로 × {args.years}년)\n")
    print("| 코어/위성 | 금 | CAGR중앙 | 중앙값 | 하위5% | MDD중앙 | 반토막확률 |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for w in ALLOCS:
        for g in GOLDS:
            r = np.array(acc[(w, g)]["r"]); m = np.array(acc[(w, g)]["m"])
            med = float(np.percentile(r, 50))
            print(f"| {int(w*100)}/{int((1-w)*100)} | {int(g*100)}% | "
                  f"{(1+med)**(1/args.years)-1:.1%} | {1+med:.2f}억 | {1+np.percentile(r,5):.2f}억 | "
                  f"{np.percentile(m,50):.1%} | {np.mean(r<=-0.5):.1%} |")
    print("\n[주의] joint block은 다년 하락장·상관붕괴 과소표현 → 방어 과소평가.")


if __name__ == "__main__":
    main()
