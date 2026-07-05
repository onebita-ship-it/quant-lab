"""몬테카를로(joint) — 벤치마크 8종 5년 분포(반토막확률 등). 상관 보존 동시 부트스트랩.

전 자산(QQQ/SPY/SOXX/TQQQ/UPRO/SOXL/TMF/TLT/GLD/EFA/AGG/SGOV)의 합성 일수익률을 같은
블록으로 동시 샘플 → 가격 복원 → 8종 전략 실행. 우리 80/20은 코어+위성 엔진으로 경로별 재현.

사용: python simulations/monte_carlo_benchmark.py --years 5 --paths 1500
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.benchmark_strategies as B  # noqa: E402
from backtests import tax_parking_backtest as TP  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from backtests import portfolio_backtest as PB  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402
from strategies import infinite_buying as v1  # noqa: E402
from simulations.monte_carlo_v9 import joint_blocks  # noqa: E402

DATA = ROOT / "data"
WARMUP = 250
COLS = ["QQQ", "SPY", "SOXX", "TQQQ", "UPRO", "SOXL", "TMF", "TLT", "GLD", "EFA", "AGG", "SGOV"]
META = {"TQQQ": {"class": "core", "index": "QQQ"}, "UPRO": {"class": "core", "index": "SPY"},
        "SOXL": {"class": "satellite", "index": "SOXX"}}


def our_8020(prices, full_dates, trade_dates):
    trend = v6.trend_signal_v6(prices["QQQ"], trade_dates, require_rising=True, confirm_days=5)
    tq = prices["TQQQ"].reindex(trade_dates)
    core = PB.norm(TP.run_engine(tq, trend, 1.0, (), opts=TP.Opts("none", False, False, False))[0])
    sat_prices = {"SGOV": prices["SGOV"].reindex(trade_dates), "QQQ": prices["QQQ"].reindex(trade_dates),
                  "TQQQ": tq}
    sig, mom = {}, {}
    for a in META:
        c = META[a]["index"]
        sat_prices[a] = prices[a].reindex(trade_dates)
        sig[a] = v6.trend_signal_v6(prices[c], trade_dates, require_rising=True, confirm_days=5)
        mom[a] = E9.blended_mom(sat_prices[a])
    dd = pd.concat([E9.trailing_dd(prices["QQQ"], trade_dates),
                    E9.trailing_dd(prices["SPY"], trade_dates)], axis=1).min(axis=1)
    sat = PB.norm(E9.run_2engine(trade_dates, sat_prices, sig, mom, META, dd, use_engineB=False)[0])
    return PB.blend(core, sat, 0.80)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--paths", type=int, default=1500)
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

    names = ["우리 최종안 80/20", "HFEA (UPRO55/TMF45)", "Gayed 로테이션", "듀얼모멘텀 GEM",
             "60/40", "영구 포트폴리오", "정석 무한매수법 v1", "QQQ 단순보유"]
    acc = {n: {"r": [], "m": []} for n in names}

    for i in range(args.paths):
        full = np.vstack([warm, sim[i]])
        P = {c: pd.Series(100 * np.cumprod(1 + full[:, k]), index=full_dates)
             for k, c in enumerate(COLS)}
        Pt = {c: P[c].reindex(trade_dates) for c in COLS}

        navs = {}
        navs["우리 최종안 80/20"] = our_8020(P, full_dates, trade_dates)
        navs["HFEA (UPRO55/TMF45)"] = B.static_portfolio({"UPRO": .55, "TMF": .45}, Pt, trade_dates, "Q")
        navs["Gayed 로테이션"] = B.gayed(P, trade_dates)
        navs["듀얼모멘텀 GEM"] = B.gem(P, trade_dates)
        navs["60/40"] = B.static_portfolio({"SPY": .6, "AGG": .4}, Pt, trade_dates, "Q")
        navs["영구 포트폴리오"] = B.static_portfolio(
            {"SPY": .25, "TLT": .25, "GLD": .25, "SGOV": .25}, Pt, trade_dates, "A")
        navs["정석 무한매수법 v1"] = PB.norm(v1.run(
            Pt["TQQQ"], v1.Params(divisions=40, take_profit_pct=0.10)).equity)
        navs["QQQ 단순보유"] = PB.norm(Pt["QQQ"])
        for n in names:
            nav = navs[n]
            acc[n]["r"].append(nav.iloc[-1] / nav.iloc[0] - 1)
            acc[n]["m"].append((nav / nav.cummax() - 1).min())
        if (i + 1) % 250 == 0:
            print(f"  ...{i+1}/{args.paths}", file=sys.stderr)

    print(f"\n# 몬테카를로(joint) 벤치마크 — {args.paths}경로 × {args.years}년\n")
    print("| 전략 | CAGR중앙 | 총수익중앙 | 하위5% | MDD중앙 | 손실확률 | 반토막확률 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for n in names:
        r = np.array(acc[n]["r"]); m = np.array(acc[n]["m"])
        med = float(np.percentile(r, 50))
        cagr = (1 + med) ** (1 / args.years) - 1
        print(f"| {n} | {cagr:.1%} | {med:.1%} | {np.percentile(r,5):.1%} | "
              f"{np.percentile(m,50):.1%} | {np.mean(r<0):.1%} | {np.mean(r<=-0.5):.1%} |")
    print("\n[주의] joint block은 다년 하락장 과소표현 → 헤지·추세필터 이점 과소평가. 합성 채권/금/해외.")


if __name__ == "__main__":
    main()
