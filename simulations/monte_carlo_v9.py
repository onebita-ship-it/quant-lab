"""몬테카를로 — v9 2엔진 통합(및 엔진A 단독) 5년 분포.

멀티자산 상관을 보존하기 위해 QQQ/SPY/SOXX 원지수 일수익률을 **같은 날짜 블록으로 동시 샘플링**
(joint block bootstrap)한다. 경로별로 원지수 → 추세신호 + 3x 파생(TQQQ/UPRO/SOXL) → v9 실행.
현금(SGOV)은 상수 연 2.0%.

[한계] 과거분포 유지 + 20일 블록은 다년 하락장 과소표현 → 추세·모멘텀 이점 과소평가.
      SOXX 공통구간(2001-07~)만 부트스트랩(SOXL 존재 구간).
사용: python simulations/monte_carlo_v9.py --years 5 --paths 2000
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.engine_v9 as E9  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA = ROOT / "data"
EXP, BORROW, LEV = 0.0095, 0.02, 3.0
DRAG = EXP / 252 + BORROW * (LEV - 1) / 252
WARMUP = 250
SGOV_ANNUAL = 0.020
META = {"TQQQ": {"class": "core", "index": "QQQ"},
        "UPRO": {"class": "core", "index": "SPY"},
        "SOXL": {"class": "satellite", "index": "SOXX"}}
IDX = {"QQQ": "QQQ", "SPY": "SPY", "SOXX": "SOXX"}


def joint_blocks(mat, n_days, n_paths, block, seed):
    """mat: (T, k) 수익률 행렬. 같은 블록 시작으로 k열 동시 샘플 → (n_paths, n_days, k)."""
    rng = np.random.default_rng(seed)
    T = mat.shape[0]
    n_blocks = n_days // block + 1
    starts = rng.integers(0, T - block, size=(n_paths, n_blocks))
    out = np.empty((n_paths, n_blocks * block, mat.shape[1]))
    for b in range(n_blocks):
        for j in range(block):
            out[:, b * block + j, :] = mat[starts[:, b] + j, :]
    return out[:, :n_days, :]


def summ(finals, mdds, years):
    q = lambda a, x: float(np.percentile(a, x))  # noqa: E731
    med = q(finals, 50)
    return {"cagr50": (1 + med) ** (1 / years) - 1, "ret50": med,
            "ret5": q(finals, 5), "ret95": q(finals, 95), "mdd50": q(mdds, 50),
            "mdd5": q(mdds, 5), "loss": float(np.mean(finals < 0)),
            "half": float(np.mean(finals <= -0.5))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--block", type=int, default=20)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    # 공통구간 원지수 수익률 행렬
    cols = ["QQQ", "SPY", "SOXX"]
    idxdata = {c: E9.load(c) for c in cols}
    df = pd.concat([idxdata[c].pct_change() for c in cols], axis=1, keys=cols).dropna()
    mat = df.values
    warm = mat[-WARMUP:]

    n_days = args.years * 252
    sim = joint_blocks(mat, n_days, args.paths, args.block, args.seed)
    full_dates = pd.bdate_range("2100-01-01", periods=WARMUP + n_days)
    trade_dates = full_dates[WARMUP:]
    sgov = pd.Series(100 * np.cumprod(np.full(len(trade_dates), 1 + SGOV_ANNUAL / 252)),
                     index=trade_dates)

    res = {"통합": {"r": [], "m": []}, "A단독": {"r": [], "m": []}}
    assets = list(META)
    for i in range(args.paths):
        path = sim[i]                       # (n_days, 3)
        fullr = np.vstack([warm, path])     # (WARMUP+n_days, 3)
        idx_prices, lev_prices, signals, moms = {}, {}, {}, {}
        for k, c in enumerate(cols):
            ip = pd.Series(100 * np.cumprod(1 + fullr[:, k]), index=full_dates)
            idx_prices[c] = ip
        prices = {"SGOV": sgov, "QQQ": idx_prices["QQQ"].reindex(trade_dates)}
        for a in assets:
            c = META[a]["index"]
            lev = pd.Series(100 * np.cumprod(1 + (LEV * path[:, cols.index(c)] - DRAG)),
                            index=trade_dates)
            prices[a] = lev
            signals[a] = v6.trend_signal_v6(idx_prices[c], trade_dates,
                                            require_rising=True, confirm_days=5)
            moms[a] = E9.blended_mom(lev)
        prices["TQQQ"] = prices["TQQQ"]
        core_dd = pd.concat([E9.trailing_dd(idx_prices["QQQ"], trade_dates),
                             E9.trailing_dd(idx_prices["SPY"], trade_dates)], axis=1).min(axis=1)
        for tag, useB in [("통합", True), ("A단독", False)]:
            eq, _ = E9.run_2engine(trade_dates, prices, signals, moms, META, core_dd,
                                   use_engineB=useB)
            res[tag]["r"].append(eq.iloc[-1] / eq.iloc[0] - 1)
            res[tag]["m"].append((eq / eq.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    a = summ(np.array(res["통합"]["r"]), np.array(res["통합"]["m"]), args.years)
    b = summ(np.array(res["A단독"]["r"]), np.array(res["A단독"]["m"]), args.years)
    print(f"\n# 몬테카를로 v9  |  {args.paths}경로 × {args.years}년 (joint block, 현금 2.0%)\n")
    print("| 지표 | V9 2엔진 통합 | V9 엔진A 단독 |")
    print("|---|---:|---:|")
    pf = lambda x: f"{x:.1%}"  # noqa: E731
    print(f"| CAGR 중앙값 | {pf(a['cagr50'])} | {pf(b['cagr50'])} |")
    print(f"| 총수익 중앙값 | {pf(a['ret50'])} | {pf(b['ret50'])} |")
    print(f"| 총수익 5%ile | {pf(a['ret5'])} | {pf(b['ret5'])} |")
    print(f"| 총수익 95%ile | {pf(a['ret95'])} | {pf(b['ret95'])} |")
    print(f"| MDD 중앙값 | {pf(a['mdd50'])} | {pf(b['mdd50'])} |")
    print(f"| MDD 5%ile(악화) | {pf(a['mdd5'])} | {pf(b['mdd5'])} |")
    print(f"| 손실확률 | {pf(a['loss'])} | {pf(b['loss'])} |")
    print(f"| 반토막확률 | {pf(a['half'])} | {pf(b['half'])} |")
    print("\n[주의] 과거분포 유지 + 블록부트스트랩은 다년 하락장 과소표현 → 추세·모멘텀 이점 과소평가.")


if __name__ == "__main__":
    main()
