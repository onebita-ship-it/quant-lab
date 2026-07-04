"""v1 vs v2 비교 + 스트레스 테스트 (TQQQ_SYNTH).

- v1: 분할40/익절15%/sell (기존 전략)
- v2 변형들: 추세필터·쿼터손절·현금비중 조합
- 전체구간 + 닷컴(2000-01~2002-12) + 금융위기(2007-10~2009-03) 스트레스 구간

마크다운 표를 stdout으로 출력한다.
"""
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute  # noqa: E402
from strategies import infinite_buying as v1  # noqa: E402
from strategies import infinite_buying_v2 as v2  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def trend_signal(qqq: pd.Series, close_index, window: int = 200) -> pd.Series:
    """QQQ 200일선 대비 종가. 신규 진입 허용(True/False)을 close 날짜에 맞춰 반환."""
    ma = qqq.rolling(window).mean()
    ok = qqq.ge(ma) | ma.isna()          # MA 계산 전(초기 200일)은 허용
    return ok.reindex(close_index).ffill().fillna(True)


# (라벨, 엔진, Params, 추세시그널 사용여부)
def build_variants():
    return [
        ("v1 기준 (sell, 100%)",
         v1, v1.Params(divisions=40, take_profit_pct=0.15, exhaust_action="sell"), False),
        ("v2 추세필터 (sell, 100%)",
         v2, v2.Params(divisions=40, take_profit_pct=0.15, exhaust_action="sell",
                       use_trend_filter=True), True),
        ("v2 추세+쿼터손절 (100%)",
         v2, v2.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                       use_trend_filter=True), True),
        ("v2 추세+쿼터 (75% 투입)",
         v2, v2.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                       use_trend_filter=True, allocation=0.75), True),
        ("v2 추세+쿼터 (50% 투입)",
         v2, v2.Params(divisions=40, take_profit_pct=0.15, exhaust_action="quarter",
                       use_trend_filter=True, allocation=0.50), True),
    ]


def run_variant(engine, p, close, tok, use_trend):
    if engine is v1:
        res = engine.run(close, p)
    else:
        res = engine.run(close, p, trend_ok=tok if use_trend else None)
    return compute(res.equity, res.cycles)


def pct(x):
    return f"{x:.1%}" if isinstance(x, (int, float)) and x == x else "n/a"


def table_full(close, qqq):
    tok = trend_signal(qqq, close.index)
    rows = []
    for label, engine, p, use_trend in build_variants():
        m = run_variant(engine, p, close, tok, use_trend)
        rows.append((label, m))
    print("| 전략 변형 | CAGR | MDD | 샤프 | 총수익 | 승률 | 사이클수 | 최장하락(일) |")
    print("|-----------|-----:|----:|-----:|-------:|-----:|--------:|------------:|")
    for label, m in rows:
        print(f"| {label} | {pct(m['CAGR'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pct(m['TotalReturn'])} | {pct(m.get('WinRate', float('nan')))} | "
              f"{m.get('Cycles','-')} | {m['LongestUnderwaterDays']} |")


def table_stress(close, qqq, start, end, title):
    sl = close.loc[start:end]
    tok = trend_signal(qqq, sl.index)
    print(f"\n### {title}  ({sl.index[0].date()} ~ {sl.index[-1].date()}, {len(sl)}일)")
    print("| 전략 변형 | 총수익 | MDD | 샤프 | 승률 | 사이클수 |")
    print("|-----------|-------:|----:|-----:|-----:|--------:|")
    for label, engine, p, use_trend in build_variants():
        m = run_variant(engine, p, sl, tok, use_trend)
        print(f"| {label} | {pct(m['TotalReturn'])} | {pct(m['MDD'])} | {m['Sharpe']:.2f} | "
              f"{pct(m.get('WinRate', float('nan')))} | {m.get('Cycles','-')} |")


def main():
    close = load_close("TQQQ_SYNTH")
    qqq = load_close("QQQ")

    print(f"## 전체구간 비교  ({close.index[0].date()} ~ {close.index[-1].date()})\n")
    table_full(close, qqq)

    print("\n## 스트레스 테스트")
    table_stress(close, qqq, "2000-01-01", "2002-12-31", "닷컴버블")
    table_stress(close, qqq, "2007-10-01", "2009-03-31", "글로벌 금융위기")


if __name__ == "__main__":
    main()
