"""시작 시점 민감도 — "대폭락 중에 개시하면 룰/종목이 달라져야 하나?" (result_start.md 입력).

연구 트랙. 확정 B안(코어50/위성50+금15, 카나리아 코어, 현행 유니버스) NAV에서
모든 거래일을 '개시일'로 보고 이후 3년 CAGR을 계산, 개시일의 QQQ 낙폭(고점대비)으로 버킷.
+ 각 버킷에서 코어 진입 게이트가 켜질 때까지의 대기일(개시 직후 시스템이 시키는 것 = SGOV 대기).

근사 주의: 연속 NAV라 고점권 개시는 '이미 사이클 진행 중' 상태를 물려받는다(신규 개시의
40분할 램프와 소폭 다름). 폭락권 개시는 전략이 어차피 SGOV 파킹 상태라 근사 오차가 작다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests import portfolio_backtest as PB  # noqa: E402
import backtests.benchmark_strategies as B  # noqa: E402
from backtests import engine_v9 as E9  # noqa: E402
from backtests.v10_experiments import canary_gate, core_nav  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"
H = 756  # 3년(거래일)
UNI = [("TQQQ", "core", "QQQ"), ("UPRO", "core", "SPY"), ("SOXL", "satellite", "SOXX")]
BUCKETS = [("고점권 (낙폭 0~-5%)", -0.05, 0.01),
           ("조정 (-5~-20%)", -0.20, -0.05),
           ("약세장 (-20~-35%)", -0.35, -0.20),
           ("대폭락 (-35% 이하)", -1.00, -0.35)]
CASES = ["2002-10-09", "2009-03-09", "2020-03-23", "2022-12-28"]


def main():
    spine = B.load("QQQ").loc[:ASOF].index
    qqq = B.load("QQQ").loc[:ASOF]
    trend = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5).astype(bool)
    gate = trend & canary_gate(spine, k=4)
    core = core_nav(spine, gate)
    gld = B.R("GLD").reindex(spine).ffill()

    assets = [t for t, _, _ in UNI]
    meta = {t: {"class": c, "index": i} for t, c, i in UNI}
    prices, sig, mom, dd_c = E9.build_inputs(assets, meta, spine)
    sat = PB.norm(E9.run_2engine(spine, prices, sig, mom, meta, dd_c, use_engineB=False)[0])
    risky = PB.blend(core, sat, 0.50)
    nav = PB.norm(B.static_portfolio({"OUR": 0.85, "GLD": 0.15},
                                     {"OUR": risky, "GLD": gld}, spine, "A"))

    qq = qqq.reindex(nav.index).ffill()
    dd = qq / qq.cummax() - 1
    fwd = (nav.shift(-H) / nav) ** (252 / H) - 1

    gate_arr = gate.reindex(nav.index).fillna(False).values
    wait = np.full(len(gate_arr), np.nan)
    next_on = None
    for i in range(len(gate_arr) - 1, -1, -1):
        if gate_arr[i]:
            next_on = i
        wait[i] = (next_on - i) if next_on is not None else np.nan
    wait = pd.Series(wait, index=nav.index)

    print("# 시작 시점 민감도 — B안, 개시일 QQQ 낙폭별 이후 3년 성과\n")
    print("| 개시일의 QQQ 낙폭 | 표본일 | 3년 CAGR 중앙 | 하위 5% | 상위 5% | 3년 손실확률 | 게이트 대기(중앙) |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for lab, lo, hi in BUCKETS:
        m = (dd > lo) & (dd <= hi) & fwd.notna()
        f, w = fwd[m], wait[m]
        if len(f) == 0:
            continue
        print(f"| {lab} | {len(f)} | {np.median(f):+.1%} | {np.percentile(f, 5):+.1%} | "
              f"{np.percentile(f, 95):+.1%} | {float((f < 0).mean()):.0%} | "
              f"{np.median(w.dropna()):.0f}일 |")

    print("\n**폭락 바닥 개시 사례**\n")
    for d0 in CASES:
        d = nav.index[nav.index.searchsorted(pd.Timestamp(d0))]
        print(f"- {d.date()} 개시 (QQQ 낙폭 {dd[d]:+.0%}): 게이트까지 {wait[d]:.0f}일 대기 → "
              f"이후 3년 CAGR {fwd.get(d, float('nan')):+.1%}")


if __name__ == "__main__":
    main()
