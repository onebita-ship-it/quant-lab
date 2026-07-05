"""몬테카를로(joint) — 코어+위성 배분별 '1억 기준 5년 분포'.

joint block bootstrap(QQQ/SPY/SOXX 동시 샘플, 상관 보존)로 경로 생성 →
코어(최종 견고안, TQQQ) + 위성(v9 엔진A, SOXL 포함/제외) NAV → 배분 블렌드(연 리밸런스).
1억 원 기준 5년 후 가치 분포(중앙/하위5%)·반토막확률·MDD중앙.

사용: python simulations/monte_carlo_portfolio.py --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests import tax_parking_backtest as TP  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402
from simulations.monte_carlo_v9 import joint_blocks  # noqa: E402

DATA = ROOT / "data"
EXP, BORROW, LEV = 0.0095, 0.02, 3.0
DRAG = EXP / 252 + BORROW * (LEV - 1) / 252
WARMUP = 250
SGOV_ANNUAL = 0.020
BASE = 1e8  # 1억 원
COLS = ["QQQ", "SPY", "SOXX"]
META_FULL = {"TQQQ": {"class": "core", "index": "QQQ"},
             "UPRO": {"class": "core", "index": "SPY"},
             "SOXL": {"class": "satellite", "index": "SOXX"}}


def sat_nav(assets, meta, trade_dates, idx_prices, path, sgov):
    prices = {"SGOV": sgov, "QQQ": idx_prices["QQQ"].reindex(trade_dates)}
    signals, moms = {}, {}
    for a in assets:
        c = meta[a]["index"]
        lev = pd.Series(100 * np.cumprod(1 + (LEV * path[:, COLS.index(c)] - DRAG)),
                        index=trade_dates)
        prices[a] = lev
        signals[a] = v6.trend_signal_v6(idx_prices[c], trade_dates, require_rising=True,
                                        confirm_days=5)
        moms[a] = E9.blended_mom(lev)
    prices["TQQQ"] = prices["TQQQ"]
    core_dd = pd.concat([E9.trailing_dd(idx_prices["QQQ"], trade_dates),
                         E9.trailing_dd(idx_prices["SPY"], trade_dates)], axis=1).min(axis=1)
    eq, _ = E9.run_2engine(trade_dates, prices, signals, moms, meta, core_dd, use_engineB=False)
    return PB.norm(eq)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    idxdata = {c: E9.load(c) for c in COLS}
    df = pd.concat([idxdata[c].pct_change() for c in COLS], axis=1, keys=COLS).dropna()
    mat = df.values
    warm = mat[-WARMUP:]
    n_days = args.years * 252
    sim = joint_blocks(mat, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]
    sgov = pd.Series(100 * np.cumprod(np.full(len(trade_dates), 1 + SGOV_ANNUAL / 252)),
                     index=trade_dates)
    assets_i, meta_i = list(META_FULL), META_FULL
    assets_x = [a for a in META_FULL if a != "SOXL"]
    meta_x = {a: META_FULL[a] for a in assets_x}

    acc = {("포함", lab): {"r": [], "m": []} for _, lab in PB.ALLOCS}
    acc.update({("제외", lab): {"r": [], "m": []} for _, lab in PB.ALLOCS})
    for i in range(args.paths):
        path = sim[i]
        fullr = np.vstack([warm, path])
        idx_prices = {c: pd.Series(100 * np.cumprod(1 + fullr[:, k]), index=full_dates)
                      for k, c in enumerate(COLS)}
        tq = pd.Series(100 * np.cumprod(1 + (LEV * path[:, 0] - DRAG)), index=trade_dates)
        trend = v6.trend_signal_v6(idx_prices["QQQ"], trade_dates, require_rising=True, confirm_days=5)
        core = PB.norm(TP.run_engine(tq, trend, 1.0, (), opts=TP.Opts("none", False, False, False))[0])
        sat_i = sat_nav(assets_i, meta_i, trade_dates, idx_prices, path, sgov)
        sat_x = sat_nav(assets_x, meta_x, trade_dates, idx_prices, path, sgov)
        for tag, sat in [("포함", sat_i), ("제외", sat_x)]:
            for w, lab in PB.ALLOCS:
                nav = PB.blend(core, sat, w)
                acc[(tag, lab)]["r"].append(nav.iloc[-1] / nav.iloc[0] - 1)
                acc[(tag, lab)]["m"].append((nav / nav.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    def won(x):
        return f"{x/1e8:.2f}억"

    print(f"\n# 몬테카를로(joint) — 1억 기준 5년 분포 ({args.paths}경로)\n")
    for tag in ("포함", "제외"):
        print(f"## 위성 SOXL {tag}\n")
        print("| 배분(코어/위성) | 중앙값 | 하위5% | 상위5% | 반토막확률 | MDD중앙 |")
        print("|---|---:|---:|---:|---:|---:|")
        for w, lab in PB.ALLOCS:
            r = np.array(acc[(tag, lab)]["r"]); m = np.array(acc[(tag, lab)]["m"])
            med = BASE * (1 + np.percentile(r, 50))
            p5 = BASE * (1 + np.percentile(r, 5))
            p95 = BASE * (1 + np.percentile(r, 95))
            half = float(np.mean(r <= -0.5))
            print(f"| {lab} | {won(med)} | {won(p5)} | {won(p95)} | {half:.1%} | "
                  f"{np.percentile(m,50):.1%} |")
        print()
    print("[주의] joint block은 다년 하락장 과소표현 → 코어 추세필터 이점 과소평가. 현금 2.0% 가정.")


if __name__ == "__main__":
    main()
