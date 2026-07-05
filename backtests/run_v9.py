"""v9 비교 실행 → result_v9.md 본문. v8 상위후보 vs v9(통합/엔진A단독/저평가/유니버스교체)."""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtests.candidates_v8 as C8  # noqa: E402
import backtests.engine_v9 as E9  # noqa: E402
from backtests.run_v8 import load_prices as load_v8_prices  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

ASOF = "2026-07-01"


def any_on_mask(assets, signals, moms, spine):
    import numpy as np
    vals = []
    for d in spine:
        vals.append(any(bool(signals[a].get(d, False)) and not np.isnan(moms[a].get(d, np.nan))
                        for a in assets))
    return pd.Series(vals, index=spine)


def pf(x):
    return f"{x:+.1%}" if isinstance(x, float) and x == x else "n/a"


def metrics_row(name, eq, on_mask):
    m = C8.compute(eq)
    ann = C8.on_annualized(eq, on_mask)
    return (f"| {name} | {pf(m['CAGR'])} | {pf(m['MDD'])} | {m['Sharpe']:.2f} | "
            f"{pf(m['TotalReturn'])} | {pf(ann)} |")


def main():
    spine = C8.load("TQQQ_SYNTH").loc[:ASOF].index
    qqq = C8.load("QQQ").loc[:ASOF]
    trend_qqq = v6.trend_signal_v6(qqq, spine, require_rising=True, confirm_days=5)

    # v8 비교군
    v8p, _ = load_v8_prices()
    s1, _ = C8.s1_baseline(v8p, trend_qqq)
    s2, _ = C8.s2_trend_switch(v8p, trend_qqq)
    s3, _ = C8.s3_hybrid(v8p, trend_qqq)

    # v9 유니버스
    assets, meta = E9.load_universe()
    prices, signals, moms, core_dd = E9.build_inputs(assets, meta, spine)
    on_mask = any_on_mask(assets, signals, moms, spine)

    v9_full, i_full = E9.run_2engine(spine, prices, signals, moms, meta, core_dd, use_engineB=True)
    v9_aonly, _ = E9.run_2engine(spine, prices, signals, moms, meta, core_dd, use_engineB=False)
    v9_rev, _ = E9.run_2engine(spine, prices, signals, moms, meta, core_dd, use_engineB=True,
                               reverse_mom=True)

    # 유니버스 교체(SOXL 제외)
    assets_x, meta_x = E9.load_universe(exclude=("SOXL",))
    px, sgx, mx, ddx = E9.build_inputs(assets_x, meta_x, spine)
    on_x = any_on_mask(assets_x, sgx, mx, spine)
    v9_nosoxl, _ = E9.run_2engine(spine, px, sgx, mx, meta_x, ddx, use_engineB=True)

    ROWS = [("S1 베이스라인(v8)", s1, on_mask), ("S2 추세스위칭(v8)", s2, on_mask),
            ("S3 하이브리드(v8)", s3, on_mask),
            ("V9 2엔진 통합", v9_full, on_mask), ("V9 엔진A 단독", v9_aonly, on_mask),
            ("V9 저평가로테이션(반증)", v9_rev, on_mask),
            ("V9 통합·SOXL제외", v9_nosoxl, on_x)]

    uni_str = ", ".join(f"{a}({meta[a]['class']})" for a in assets)
    print("# v9 — 2엔진 통합 전략 비교\n")
    print(f"> 유니버스(`config/universe.txt`): {uni_str}"
          " · 신호: 각 원지수 200일선 기울기+스트릭5 · 비용 편도 0.12% · 현금=SGOV\n")

    print("## ① 전체구간 (1999~2026 YTD)\n")
    print("| 전략 | CAGR | MDD | 샤프 | 총수익 | ⑤ ON구간 연환산 |")
    print("|---|---:|---:|---:|---:|---:|")
    for name, eq, mask in ROWS:
        print(metrics_row(name, eq, mask))
    print()

    print("## ② 2013~2026\n")
    print("| 전략 | CAGR | MDD | 샤프 | 총수익 | ⑤ ON구간 연환산 |")
    print("|---|---:|---:|---:|---:|---:|")
    for name, eq, mask in ROWS:
        sub = eq.loc["2013-01-01":]
        msk = mask.loc["2013-01-01":]
        print(metrics_row(name, sub, msk))
    print()

    print("## ③ 스트레스 4구간 (총수익 / MDD)\n")
    print("| 전략 | " + " | ".join(n for _, _, n in C8.STRESS) + " |")
    print("|---" * (len(C8.STRESS) + 1) + "|")
    for name, eq, _ in ROWS:
        cells = []
        for s, e, _ in C8.STRESS:
            m = C8.compute(eq.loc[s:e])
            cells.append(f"{pf(m['TotalReturn'])} / {pf(m['MDD'])}")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print()

    print("## ④ 연도별 수익률\n")
    yrs = sorted(spine.year.unique())
    print("| 연도 | " + " | ".join(n for n, _, _ in ROWS) + " |")
    print("|---" * (len(ROWS) + 1) + "|")
    ys = {name: C8.yearly(eq) for name, eq, _ in ROWS}
    for y in yrs:
        emph = "**" if y in (2025, 2026) else ""
        lab = f"{y} YTD" if y == 2026 else str(y)
        cells = [f"{emph}{lab}{emph}"]
        for name, _, _ in ROWS:
            cells.append(f"{emph}{pf(ys[name].get(y, float('nan')))}{emph}")
        print("| " + " | ".join(cells) + " |")
    print()

    print("## 엔진B 기여 & 유니버스 민감도\n")
    print(f"- 엔진B 계단 발동: **{i_full['engineB_deploys']}회**")
    print(f"- V9 엔진A 픽 분포: {i_full['picks']}")
    b = C8.compute(v9_full); a = C8.compute(v9_aonly)
    print(f"- **엔진B 효과**(통합 vs A단독): CAGR {pf(a['CAGR'])}→{pf(b['CAGR'])}, "
          f"MDD {pf(a['MDD'])}→{pf(b['MDD'])}, 샤프 {a['Sharpe']:.2f}→{b['Sharpe']:.2f}")
    n = C8.compute(v9_nosoxl)
    print(f"- **유니버스 민감도**(SOXL 포함 vs 제외): CAGR {pf(b['CAGR'])}→{pf(n['CAGR'])}, "
          f"MDD {pf(b['MDD'])}→{pf(n['MDD'])}, 샤프 {b['Sharpe']:.2f}→{n['Sharpe']:.2f}")
    r = C8.compute(v9_rev)
    print(f"- **저평가 로테이션(반증)**: CAGR {pf(r['CAGR'])}, MDD {pf(r['MDD'])}, "
          f"샤프 {r['Sharpe']:.2f} — 모멘텀 1위 대신 꼴찌 매수")
    print()


if __name__ == "__main__":
    main()
