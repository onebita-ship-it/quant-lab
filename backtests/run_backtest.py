"""무한매수법 백테스트 실행기.

사용:
  python backtests/run_backtest.py                       # 기본 파라미터, TQQQ
  python backtests/run_backtest.py --ticker TQQQ_SYNTH   # 합성 TQQQ (1999~)
  python backtests/run_backtest.py --grid                # 분할수·익절% 그리드서치 (walk-forward)

그리드서치는 데이터를 학습 70% / 검증 30%로 나눠, 학습구간 상위 파라미터가
검증구간에서도 유지되는지 함께 출력한다 (과최적화 확인용).
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtests.metrics import compute, fmt  # noqa: E402
from strategies.infinite_buying import Params, run  # noqa: E402

DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"


def load_close(ticker: str) -> pd.Series:
    df = pd.read_csv(DATA_DIR / f"{ticker}.csv", index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def single(close: pd.Series, p: Params, label: str) -> dict:
    res = run(close, p)
    m = compute(res.equity, res.cycles)
    print(f"\n=== {label} | 분할={p.divisions} 익절={p.take_profit_pct:.0%} "
          f"소진시={p.exhaust_action} ===")
    print(fmt(m))
    # Buy&Hold 비교
    bh = close / close.iloc[0] * p.initial_capital
    print("--- Buy & Hold 비교 ---")
    print(fmt(compute(bh)))
    REPORT_DIR.mkdir(exist_ok=True)
    res.equity.to_csv(REPORT_DIR / f"equity_{label}.csv", header=["Equity"])
    return m


def grid(close: pd.Series, ticker: str) -> None:
    split = int(len(close) * 0.7)
    train, test = close.iloc[:split], close.iloc[split:]
    print(f"학습: {train.index[0].date()}~{train.index[-1].date()} / "
          f"검증: {test.index[0].date()}~{test.index[-1].date()}")

    rows = []
    for div in [20, 40, 60, 80]:
        for tp in [0.05, 0.10, 0.15, 0.20]:
            for action in ["sell", "hold"]:
                p = Params(divisions=div, take_profit_pct=tp, exhaust_action=action)
                m_tr = compute(*_ec(run(train, p)))
                m_te = compute(*_ec(run(test, p)))
                rows.append({
                    "divisions": div, "take_profit": tp, "exhaust": action,
                    "train_CAGR": m_tr["CAGR"], "train_MDD": m_tr["MDD"],
                    "train_Sharpe": m_tr["Sharpe"],
                    "test_CAGR": m_te["CAGR"], "test_MDD": m_te["MDD"],
                    "test_Sharpe": m_te["Sharpe"],
                })
    df = pd.DataFrame(rows).sort_values("train_Sharpe", ascending=False)
    REPORT_DIR.mkdir(exist_ok=True)
    out = REPORT_DIR / f"grid_{ticker}.csv"
    df.to_csv(out, index=False)
    pd.set_option("display.width", 160)
    print("\n학습구간 샤프 상위 10개 (검증구간 성과 병기):")
    print(df.head(10).to_string(index=False,
          float_format=lambda x: f"{x:.3f}"))
    print(f"\n[저장] {out}")
    print("[해석] train 상위가 test에서도 상위권이면 견고, test에서 무너지면 과최적화 신호.")


def _ec(res):
    return res.equity, res.cycles


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="TQQQ")
    ap.add_argument("--divisions", type=int, default=40)
    ap.add_argument("--take-profit", type=float, default=0.10)
    ap.add_argument("--exhaust", choices=["sell", "hold"], default="sell")
    ap.add_argument("--grid", action="store_true")
    args = ap.parse_args()

    close = load_close(args.ticker)
    if args.grid:
        grid(close, args.ticker)
    else:
        p = Params(divisions=args.divisions, take_profit_pct=args.take_profit,
                   exhaust_action=args.exhaust)
        single(close, p, args.ticker)
