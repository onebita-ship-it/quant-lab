"""룰북 최종 검수 (2) — 최종 배분 A(코어68/위성17/금15, 카나리아 게이트) 몬테카를로.

joint block bootstrap(상관 보존) 5년 × 2000경로. 1억 기준 분포·반토막확률 재산출.
코어는 v10 게이트(기울기+스트릭 AND 13612W 카나리아)로 재현.
사용: python simulations/monte_carlo_final_v10.py --years 5 --paths 2000
"""
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
BASE = 1e8
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "SGOV", "GLD", "EFA", "EEM", "AGG"]
META = {"TQQQ": {"class": "core", "index": "QQQ"}, "UPRO": {"class": "core", "index": "SPY"},
        "SOXL": {"class": "satellite", "index": "SOXX"}}


def canary(P, trade_dates):
    off = ["SPY", "EFA", "EEM", "AGG"]
    me = sorted(B._periods(trade_dates, "M"))
    vals = pd.Series(index=trade_dates, dtype="float64")
    for d in me:
        block = any(C.w13612(P[a], d) < 0 for a in off)
        vals[d] = 0.0 if block else 1.0
    return vals.reindex(trade_dates).ffill().fillna(1.0).astype(bool)


def allocA(P, full_dates, trade_dates):
    trend = v6.trend_signal_v6(P["QQQ"], trade_dates, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary({a: P[a].reindex(trade_dates) for a in ["SPY", "EFA", "EEM", "AGG"]}, trade_dates)
    tq = P["TQQQ"].reindex(trade_dates)
    core = PB.norm(TP.run_engine(tq, gate, 1.0, (), opts=TP.Opts("none", False, False, False))[0])
    sp = {"SGOV": P["SGOV"].reindex(trade_dates), "QQQ": P["QQQ"].reindex(trade_dates), "TQQQ": tq}
    sig, mom = {}, {}
    for a in META:
        c = META[a]["index"]
        sp[a] = P[a].reindex(trade_dates)
        sig[a] = v6.trend_signal_v6(P[c], trade_dates, require_rising=True, confirm_days=5)
        mom[a] = E9.blended_mom(sp[a])
    dd = pd.concat([E9.trailing_dd(P["QQQ"], trade_dates), E9.trailing_dd(P["SPY"], trade_dates)],
                   axis=1).min(axis=1)
    sat = PB.norm(E9.run_2engine(trade_dates, sp, sig, mom, META, dd, use_engineB=False)[0])
    base = PB.blend(core, sat, 0.80)
    return B.static_portfolio({"OUR": .85, "GLD": .15},
                              {"OUR": base, "GLD": P["GLD"].reindex(trade_dates)}, trade_dates, "A")


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

    rets, mdds = [], []
    for i in range(args.paths):
        full = np.vstack([warm, sim[i]])
        P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
             for k, c in enumerate(COLS)}
        nav = PB.norm(allocA(P, full_dates, trade_dates))
        rets.append(nav.iloc[-1] / nav.iloc[0] - 1)
        mdds.append((nav / nav.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    r = np.array(rets); m = np.array(mdds)
    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    won = lambda x: f"{BASE*(1+x)/1e8:.2f}억"  # noqa: E731
    med = q(r, 50)
    print("### 부록 B. 최종 배분 A(코어68/위성17/금15) 몬테카를로 — 1억 기준 5년 "
          f"({args.paths}경로)\n")
    print("| 지표 | 값 |")
    print("|---|---:|")
    print(f"| CAGR 중앙값 | {(1+med)**(1/args.years)-1:.1%} |")
    print(f"| 5년 후 중앙값 | {won(med)} |")
    print(f"| 하위 5% | {won(q(r,5))} |")
    print(f"| 상위 5% | {won(q(r,95))} |")
    print(f"| MDD 중앙값 | {q(m,50):.1%} |")
    print(f"| MDD 5%ile(악화) | {q(m,5):.1%} |")
    print(f"| 손실확률(원금미만) | {np.mean(r<0):.1%} |")
    print(f"| **반토막확률(-50%이하)** | **{np.mean(r<=-0.5):.1%}** |")
    print("\n[주의] joint block은 다년 하락장 과소표현 → 카나리아·추세필터 방어를 과소평가(실제 더 견고).")


if __name__ == "__main__":
    main()
