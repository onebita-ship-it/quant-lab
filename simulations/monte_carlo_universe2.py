"""유니버스 확장 감사 MC — 확정 B안(코어50/위성50+금15, 카나리아) {현행, +KORU} 비교.

monte_carlo_final_v10.py의 B안 버전. joint block bootstrap(상관 보존) 5년 × 2000경로.
KORU 편입이 반토막확률·하위 분위에 미치는 영향을 재산출 (result_universe2.md 입력).
사용: python simulations/monte_carlo_universe2.py --years 5 --paths 2000
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
W_CORE = 0.50   # B안
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "SGOV", "GLD", "EFA", "EEM", "AGG",
        "EWY", "KORU"]
META_BASE = {"TQQQ": {"class": "core", "index": "QQQ"},
             "UPRO": {"class": "core", "index": "SPY"},
             "SOXL": {"class": "satellite", "index": "SOXX"}}
META_KORU = dict(META_BASE, KORU={"class": "satellite", "index": "EWY"})


def canary(P, trade_dates):
    off = ["SPY", "EFA", "EEM", "AGG"]
    me = sorted(B._periods(trade_dates, "M"))
    vals = pd.Series(index=trade_dates, dtype="float64")
    for d in me:
        block = any(C.w13612(P[a], d) < 0 for a in off)
        vals[d] = 0.0 if block else 1.0
    return vals.reindex(trade_dates).ffill().fillna(1.0).astype(bool)


def allocB(P, trade_dates, meta):
    trend = v6.trend_signal_v6(P["QQQ"], trade_dates, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary({a: P[a].reindex(trade_dates) for a in ["SPY", "EFA", "EEM", "AGG"]}, trade_dates)
    tq = P["TQQQ"].reindex(trade_dates)
    core = PB.norm(TP.run_engine(tq, gate, 1.0, (), opts=TP.Opts("none", False, False, False))[0])
    sp = {"SGOV": P["SGOV"].reindex(trade_dates), "QQQ": P["QQQ"].reindex(trade_dates), "TQQQ": tq}
    sig, mom = {}, {}
    for a in meta:
        c = meta[a]["index"]
        sp[a] = P[a].reindex(trade_dates)
        sig[a] = v6.trend_signal_v6(P[c], trade_dates, require_rising=True, confirm_days=5)
        mom[a] = E9.blended_mom(sp[a])
    dd = pd.concat([E9.trailing_dd(P["QQQ"], trade_dates), E9.trailing_dd(P["SPY"], trade_dates)],
                   axis=1).min(axis=1)
    sat = PB.norm(E9.run_2engine(trade_dates, sp, sig, mom, meta, dd, use_engineB=False)[0])
    base = PB.blend(core, sat, W_CORE)
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
    print(f"> joint 수익률 행렬: {df.index[0].date()} ~ {df.index[-1].date()} "
          f"({len(df)}일 × {len(COLS)}자산)\n", file=sys.stderr)
    mat = df.values; warm = mat[-WARMUP:]
    n_days = args.years * 252
    sim = joint_blocks(mat, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]

    print(f"### B안(코어50/위성50+금15) 몬테카를로 — 1억 기준 {args.years}년 ({args.paths}경로)\n")
    print("| 지표 | 현행 유니버스 | +KORU |")
    print("|---|---:|---:|")

    results = {}
    for tag, meta in [("현행", META_BASE), ("+KORU", META_KORU)]:
        rets, mdds = [], []
        for i in range(args.paths):
            full = np.vstack([warm, sim[i]])
            P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
                 for k, c in enumerate(COLS)}
            nav = PB.norm(allocB(P, trade_dates, meta))
            rets.append(nav.iloc[-1] / nav.iloc[0] - 1)
            mdds.append((nav / nav.cummax() - 1).min())
            if (i + 1) % 250 == 0:
                print(f"  [{tag}] ...{i+1}/{args.paths}", file=sys.stderr)
        results[tag] = (np.array(rets), np.array(mdds))

    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    won = lambda x: f"{BASE*(1+x)/1e8:.2f}억"  # noqa: E731
    r0, m0 = results["현행"]; r1, m1 = results["+KORU"]
    yr = args.years
    rows = [
        ("CAGR 중앙값", lambda r, m: f"{(1+q(r,50))**(1/yr)-1:.1%}"),
        (f"{yr}년 후 중앙값", lambda r, m: won(q(r, 50))),
        ("하위 5%", lambda r, m: won(q(r, 5))),
        ("상위 5%", lambda r, m: won(q(r, 95))),
        ("MDD 중앙값", lambda r, m: f"{q(m,50):.1%}"),
        ("MDD 5%ile(악화)", lambda r, m: f"{q(m,5):.1%}"),
        ("손실확률(원금미만)", lambda r, m: f"{np.mean(r<0):.1%}"),
        ("**반토막확률(-50%이하)**", lambda r, m: f"**{np.mean(r<=-0.5):.1%}**"),
    ]
    for lab, fn in rows:
        print(f"| {lab} | {fn(r0, m0)} | {fn(r1, m1)} |")
    print("\n[주의] joint block은 다년 하락장 과소표현. joint 행렬이 SOXL 상장(2001-07)~로 짧아져"
          " 닷컴 초기는 미포함. 과거분포 가정은 KORU 합성 구간의 한계를 그대로 상속.")


if __name__ == "__main__":
    main()
