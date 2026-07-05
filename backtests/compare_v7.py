"""v7 검증 — 토핑 한정 변동성 필터가 v5(폭락 방어)+v6(dead-cat 회피)의 합집합을 이루면서
COVID V자 반등 상방을 지키는가?

모든 변형: 분할40/익절15%/쿼터손절/기울기+스트릭5 추세필터. v7 엔진으로 vol_mode·임계값만 토글.
차단 조건: 고변동성 AND (토핑 OR 하락국면). 토핑 = 가격이 200MA 위로 ext 이상 확장.
스트레스: 닷컴/GFC/COVID/2022.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying_v7 as v7  # noqa: E402

DATA_DIR = ROOT / "data"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def base(**kw):
    return v7.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                     use_trend_filter=True, reentry_cooldown_days=0, **kw)


# 라벨, Params, vol_pct(None=필터끔), ext_threshold(토핑용)
def build_variants():
    return [
        ("v1.0 최종 (변동성 OFF)", base(), None, 0.10),
        ("v5 무조건 80%", base(use_vol_filter=True, vol_mode="uncond"), 0.80, 0.10),
        ("v6 조건부(하락만) 80%", base(use_vol_filter=True, vol_mode="down"), 0.80, 0.10),
        ("v7 토핑+하락 80% (ext10%)", base(use_vol_filter=True, vol_mode="topping"), 0.80, 0.10),
        ("v7 토핑+하락 80% (ext5%)", base(use_vol_filter=True, vol_mode="topping"), 0.80, 0.05),
        ("v7 토핑+하락 80% (ext10%) +현금75%",
         base(use_vol_filter=True, vol_mode="topping", allocation=0.75), 0.80, 0.10),
    ]


def run_variant(p, vol_pct, ext, close, qqq):
    trend = v7.trend_signal_v7(qqq, close.index, require_rising=True, confirm_days=5)
    down = v7.regime_down_signal(qqq, close.index, fast=50, slow=200)
    vol = None
    top = None
    if vol_pct is not None:
        vol = v7.vol_filter_signal(qqq, close.index, 20, 252, vol_pct)
        top = v7.topping_signal(qqq, close.index, ma_window=200, ext_threshold=ext)
    res = v7.run(close, p, trend_ok=trend, regime_down=down, vol_ok=vol, topping=top)
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def table(close, qqq, full=True):
    if full:
        print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 |")
        print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|")
    else:
        print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
        print("|-----------|-------:|----:|-----:|-----:|--------:|")
    for label, p, vol_pct, ext in build_variants():
        m = run_variant(p, vol_pct, ext, close, qqq)
        wr = pct(m.get("WinRate", float("nan")))
        if full:
            print(f"| {label} | {pct(m['CAGR'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
                  f"{pct(m['TotalReturn'])} | {wr} | {m.get('Cycles','-')} |")
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
