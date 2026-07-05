"""v6 교차검증 — 실제 TQQQ 데이터(2010~)로 v6 결론이 유지되는가?

지금까지는 QQQ 합성 TQQQ(TQQQ_SYNTH)로 검증. 실제 3배 ETF는 진짜 일일 리밸런싱·변동성 잠식이
반영돼 있으므로, v6(조건부 변동성 필터) 결론이 실데이터에서도 성립하는지 확인한다.

- A. 실 TQQQ 전체(2010~): v6 조건부(70/80%) vs 베이스·v5무조건 + Buy&Hold
- B. 실 vs 합성 겹침(2010~): 동일 v6 조건부 70% 구성 — 합성이 실데이터를 충실히 재현했나
- C. 실 TQQQ 스트레스: 2018 조정 / COVID 2020 / 2022 약세장 (닷컴·GFC는 실 TQQQ에 없음)

신호(추세·변동성·레짐)는 파이프라인대로 QQQ에서 계산해 대상 종가 날짜에 정렬.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v6 as v6  # noqa: E402

DATA_DIR = ROOT / "data"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def base(**kw):
    return v6.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                     use_trend_filter=True, reentry_cooldown_days=0, **kw)


def make_signals(qqq, index, vol_pct):
    trend = v6.trend_signal_v6(qqq, index, require_rising=True, confirm_days=5)
    down = v6.regime_down_signal(qqq, index, fast=50, slow=200)
    vol = v6.vol_filter_signal(qqq, index, 20, 252, vol_pct) if vol_pct else None
    return trend, down, vol


def run_cfg(close, qqq, p, vol_pct):
    trend, down, vol = make_signals(qqq, close.index, vol_pct)
    res = v6.run(close, p, trend_ok=trend, regime_down=down, vol_ok=vol)
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def row_full(label, m):
    print(f"| {label} | {pct(m['CAGR'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
          f"{pct(m['TotalReturn'])} | {pct(m.get('WinRate', float('nan')))} | "
          f"{m.get('Cycles','-')} |")


def row_stress(label, m):
    print(f"| {label} | {pct(m['TotalReturn'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
          f"{pct(m.get('WinRate', float('nan')))} | {m.get('Cycles','-')} |")


VARIANTS = [
    ("v1.0 최종 (변동성 OFF)", base(), None),
    ("v5 무조건 80%", base(use_vol_filter=True, vol_when_down_only=False), 0.80),
    ("v6 조건부 70% (추천)", base(use_vol_filter=True, vol_when_down_only=True), 0.70),
    ("v6 조건부 80%", base(use_vol_filter=True, vol_when_down_only=True), 0.80),
]


def main():
    tqqq = load_close("TQQQ")
    synth = load_close("TQQQ_SYNTH")
    qqq = load_close("QQQ")
    start = tqqq.index[0]

    print(f"## A. 실 TQQQ 전체 ({tqqq.index[0].date()} ~ {tqqq.index[-1].date()}, {len(tqqq)}일)\n")
    print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 |")
    print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|")
    for label, p, vp in VARIANTS:
        row_full(label, run_cfg(tqqq, qqq, p, vp))
    bh = tqqq / tqqq.iloc[0] * 40_000.0
    row_full("Buy & Hold TQQQ", compute(bh))

    print(f"\n## B. 실 vs 합성 겹침 (2010~, 동일 v6 조건부 70%)\n")
    synth_ov = synth.loc[start:]
    p70 = base(use_vol_filter=True, vol_when_down_only=True)
    m_real = run_cfg(tqqq, qqq, p70, 0.70)
    m_syn = run_cfg(synth_ov, qqq, p70, 0.70)
    print("| 데이터 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 |")
    print("|--------|-----:|----:|-----:|-------:|-----:|--------:|")
    row_full("실 TQQQ", m_real)
    row_full("합성 TQQQ_SYNTH", m_syn)

    print("\n## C. 실 TQQQ 스트레스\n")
    for s, e, name in [("2018-01-01", "2018-12-31", "2018 조정"),
                       ("2020-01-01", "2020-06-30", "COVID 크래시"),
                       ("2022-01-01", "2022-12-31", "2022 약세장")]:
        sl = tqqq.loc[s:e]
        print(f"### {name} ({sl.index[0].date()} ~ {sl.index[-1].date()}, {len(sl)}일)")
        print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
        print("|-----------|-------:|----:|-----:|-----:|--------:|")
        for label, p, vp in VARIANTS:
            row_stress(label, run_cfg(sl, qqq, p, vp))
        bh_s = sl / sl.iloc[0] * 40_000.0
        row_stress("Buy & Hold TQQQ", compute(bh_s))
        print()


if __name__ == "__main__":
    main()
