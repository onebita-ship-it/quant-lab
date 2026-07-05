"""v5 검증 — 변동성 필터가 v1.0 최종 견고안에 순이득을 주는가?

베이스: v1.0 최종(기울기+스트릭5·쿼터손절·쿨다운OFF). 여기에 변동성 필터(QQQ 20일 실현변동성이
최근 1년 80/70%ile 초과 시 진입 금지)를 얹어 비교.

스트레스 구간을 확장: 닷컴/GFC에 더해 COVID(2020)·2022 약세장(둘 다 실 TQQQ, 고변동성 이벤트).
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v5 as v5  # noqa: E402

DATA_DIR = ROOT / "data"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def base(**kw):
    return v5.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                     use_trend_filter=True, reentry_cooldown_days=0, **kw)


# 라벨, Params, vol_pct(None=변동성필터 끔)
def build_variants():
    return [
        ("v1.0 최종 (변동성필터 OFF, 100%)", base(), None),
        ("v5 +변동성필터 80%ile (100%)", base(use_vol_filter=True), 0.80),
        ("v5 +변동성필터 70%ile (100%)", base(use_vol_filter=True), 0.70),
        ("v5 +변동성필터 80%ile +현금75%", base(use_vol_filter=True, allocation=0.75), 0.80),
    ]


def run_variant(p, vol_pct, close, qqq):
    trend = v5.trend_signal_v5(qqq, close.index, require_rising=True, confirm_days=5)
    vol = None
    if vol_pct is not None:
        vol = v5.vol_filter_signal(qqq, close.index, window=20, ref_window=252,
                                   max_percentile=vol_pct)
    res = v5.run(close, p, trend_ok=trend, vol_ok=vol)
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def table(close, qqq, full=True):
    if full:
        print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 | 최장하락(일) |")
        print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|------------:|")
    else:
        print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
        print("|-----------|-------:|----:|-----:|-----:|--------:|")
    for label, p, vol_pct in build_variants():
        m = run_variant(p, vol_pct, close, qqq)
        wr = pct(m.get("WinRate", float("nan")))
        if full:
            print(f"| {label} | {pct(m['CAGR'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{pct(m['TotalReturn'])} | {wr} | {m.get('Cycles','-')} | "
                  f"{m['LongestUnderwaterDays']} |")
        else:
            print(f"| {label} | {pct(m['TotalReturn'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{wr} | {m.get('Cycles','-')} |")


def main():
    close = load_close("TQQQ_SYNTH")
    qqq = load_close("QQQ")
    print(f"## 전체구간 ({close.index[0].date()} ~ {close.index[-1].date()})\n")
    table(close, qqq, full=True)
    for start, end, name in [("2000-01-01", "2002-12-31", "닷컴버블"),
                             ("2007-10-01", "2009-03-31", "글로벌 금융위기"),
                             ("2020-01-01", "2020-06-30", "COVID 크래시"),
                             ("2022-01-01", "2022-12-31", "2022 약세장")]:
        sl = close.loc[start:end]
        print(f"\n## {name} ({sl.index[0].date()} ~ {sl.index[-1].date()}, {len(sl)}일)\n")
        table(sl, qqq, full=False)


if __name__ == "__main__":
    main()
