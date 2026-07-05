"""몬테카를로(joint) — 후보 전략 6종(+변형) vs 우리 80/20 · QQQ. 5년 분포.

전 자산 합성 일수익률을 동시 블록 샘플(상관 보존) → 가격 복원 → 전략 실행.
사용: python simulations/monte_carlo_candidates.py --years 5 --paths 1000
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
from backtests import portfolio_backtest as PB  # noqa: E402
from simulations.monte_carlo_v9 import joint_blocks  # noqa: E402
from simulations.monte_carlo_benchmark import our_8020  # noqa: E402

WARMUP = 250
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "TMF", "TLT", "AGG", "GLD", "SGOV",
        "EFA", "EEM", "LQD", "IEF", "SHY", "SCZ", "TIP", "VEU"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=1000)
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

    names = ["우리 최종안 80/20", "Gayed LRS (200MA)", "Gayed LRS (우리 필터)",
             "HFEA (UPRO55/TMF45)", "VAA (13612W)", "ADM (원전)", "ADM (3배 치환)",
             "GEM", "영구 포트폴리오", "QQQ 단순보유"]
    acc = {n: {"r": [], "m": []} for n in names}

    for i in range(args.paths):
        full = np.vstack([warm, sim[i]])
        P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
             for k, c in enumerate(COLS)}
        Pt = {c: P[c].reindex(trade_dates) for c in COLS}
        navs = {}
        navs["우리 최종안 80/20"] = our_8020(P, full_dates, trade_dates)
        navs["Gayed LRS (200MA)"] = C.gayed_lrs(trade_dates, Pt, use_our_filter=False)[0]
        navs["Gayed LRS (우리 필터)"] = C.gayed_lrs(trade_dates, Pt, use_our_filter=True)[0]
        navs["HFEA (UPRO55/TMF45)"] = B.static_portfolio({"UPRO": .55, "TMF": .45}, Pt, trade_dates, "Q")
        navs["VAA (13612W)"] = C.vaa(trade_dates, Pt)[0]
        navs["ADM (원전)"] = C.adm(trade_dates, Pt, leverage=False)[0]
        navs["ADM (3배 치환)"] = C.adm(trade_dates, Pt, leverage=True)[0]
        navs["GEM"] = C.gem(trade_dates, Pt)[0]
        navs["영구 포트폴리오"] = B.static_portfolio(
            {"SPY": .25, "TLT": .25, "GLD": .25, "SGOV": .25}, Pt, trade_dates, "A")
        navs["QQQ 단순보유"] = PB.norm(Pt["QQQ"])
        for n in names:
            nav = PB.norm(navs[n])
            acc[n]["r"].append(nav.iloc[-1] / nav.iloc[0] - 1)
            acc[n]["m"].append((nav / nav.cummax() - 1).min())
        if (i + 1) % 200 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    print(f"\n# 몬테카를로(joint) 후보 — {args.paths}경로 × {args.years}년\n")
    print("| 전략 | CAGR중앙 | 하위5% | MDD중앙 | 손실확률 | 반토막확률 |")
    print("|---|---:|---:|---:|---:|---:|")
    for n in names:
        r = np.array(acc[n]["r"]); m = np.array(acc[n]["m"])
        med = float(np.percentile(r, 50)); cagr = (1 + med) ** (1 / args.years) - 1
        print(f"| {n} | {cagr:.1%} | {np.percentile(r,5):.1%} | {np.percentile(m,50):.1%} | "
              f"{np.mean(r<0):.1%} | {np.mean(r<=-0.5):.1%} |")
    print("\n[주의] joint block은 다년 하락장·상관붕괴 과소표현 → 헤지·추세필터 이점 왜곡(양방향).")


if __name__ == "__main__":
    main()
