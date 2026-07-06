"""SOXL 왜곡 감사 MC — B안(50/50)·A안(80/20) × {SOXL 포함/제외}, 금15%, 카나리아 코어.

--asof 로 데이터 절단(예: 2025-12-31 → 2026 상반기 SOXL 급등 제외). 5년 분포·반토막.
2회 실행으로 4조건 완성:
  전체 데이터        → ①(SOXL포함) ③(SOXL제외)
  --asof 2025-12-31  → ②(SOXL포함) ④(SOXL제외)
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
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "SGOV", "GLD", "EFA", "EEM", "AGG"]
META_FULL = {"TQQQ": {"class": "core", "index": "QQQ"}, "UPRO": {"class": "core", "index": "SPY"},
             "SOXL": {"class": "satellite", "index": "SOXX"}}
META_NOSOXL = {"TQQQ": {"class": "core", "index": "QQQ"}, "UPRO": {"class": "core", "index": "SPY"}}
ALLOCS = [0.80, 0.50]
GOLD = 0.15


def canary(P, td):
    off = {a: P[a].reindex(td) for a in ["SPY", "EFA", "EEM", "AGG"]}
    me = sorted(B._periods(td, "M"))
    can = pd.Series(index=td, dtype="float64")
    for d in me:
        can[d] = 0.0 if any(C.w13612(off[a], d) < 0 for a in off) else 1.0
    return can.reindex(td).ffill().fillna(1.0).astype(bool)


def core_of(P, td):
    can = canary(P, td)
    trend = v6.trend_signal_v6(P["QQQ"], td, require_rising=True, confirm_days=5).astype(bool)
    tq = P["TQQQ"].reindex(td)
    return PB.norm(TP.run_engine(tq, trend & can, 1.0, (), opts=TP.Opts("none", False, False, False))[0])


def sat_of(P, td, META):
    sp = {"SGOV": P["SGOV"].reindex(td), "QQQ": P["QQQ"].reindex(td), "TQQQ": P["TQQQ"].reindex(td)}
    sig, mom = {}, {}
    for a in META:
        c = META[a]["index"]; sp[a] = P[a].reindex(td)
        sig[a] = v6.trend_signal_v6(P[c], td, require_rising=True, confirm_days=5)
        mom[a] = E9.blended_mom(sp[a])
    dd = pd.concat([E9.trailing_dd(P["QQQ"], td), E9.trailing_dd(P["SPY"], td)], axis=1).min(axis=1)
    return PB.norm(E9.run_2engine(td, sp, sig, mom, META, dd, use_engineB=False)[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--asof", default=None)
    args = ap.parse_args()

    series = {c: (B.R(c).loc[:args.asof] if args.asof else B.R(c)) for c in COLS}
    df = pd.concat([series[c].pct_change() for c in COLS], axis=1, keys=COLS).dropna()
    mat = df.values; warm = mat[-WARMUP:]
    n_days = args.years * 252
    sim = joint_blocks(mat, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    td = full_dates[WARMUP:]

    configs = [(w, soxl) for w in ALLOCS for soxl in (True, False)]
    acc = {c: {"r": [], "m": []} for c in configs}
    for i in range(args.paths):
        full = np.vstack([warm, sim[i]])
        P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
             for k, c in enumerate(COLS)}
        core = core_of(P, td)
        sat_full = sat_of(P, td, META_FULL)
        sat_no = sat_of(P, td, META_NOSOXL)
        gld = P["GLD"].reindex(td)
        for w in ALLOCS:
            for soxl in (True, False):
                sat = sat_full if soxl else sat_no
                risky = PB.blend(core, sat, w)
                nav = PB.norm(B.static_portfolio(
                    {"OUR": 1 - GOLD, "GLD": GOLD}, {"OUR": risky, "GLD": gld}, td, "A"))
                acc[(w, soxl)]["r"].append(nav.iloc[-1] / nav.iloc[0] - 1)
                acc[(w, soxl)]["m"].append((nav / nav.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    tag = f"--asof {args.asof} (2026 상반기 제외)" if args.asof else "전체 데이터 (현행)"
    print(f"\n### MC — {tag} ({args.paths}경로 × {args.years}년)\n")
    print("| 안 | SOXL | CAGR중앙 | 5년 중앙값 | 하위5% | MDD중앙 | 반토막확률 |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for w in ALLOCS:
        for soxl in (True, False):
            r = np.array(acc[(w, soxl)]["r"]); m = np.array(acc[(w, soxl)]["m"])
            med = float(np.percentile(r, 50))
            nm = "80/20" if w == 0.80 else "50/50"
            print(f"| {nm} | {'포함' if soxl else '제외'} | {(1+med)**(1/args.years)-1:.1%} | "
                  f"{1+med:.2f}억 | {1+np.percentile(r,5):.2f}억 | "
                  f"{np.percentile(m,50):.1%} | {np.mean(r<=-0.5):.1%} |")
    print("\n[주의] joint block은 다년 하락장·상관붕괴 과소표현 → 방어 과소평가(실제 꼬리 더 두꺼울 수).")


if __name__ == "__main__":
    main()
