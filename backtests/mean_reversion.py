"""단기 평균회귀 전략 2종 — RSI-2, IBS. 매일 종가 판정, 롱온리 in/out.

RSI-2: 종가>200일선 AND RSI(2)<thr → 매수 / RSI(2)>60 OR 종가>5일선 → 매도.
IBS  : 종가>200일선 AND IBS<thr → 매수 / IBS>0.8 → 매도.  (IBS=(C-L)/(H-L))

비용 편도 0.12%(수수료+슬리피지). 미보유 현금은 SGOV 수익. frac=투입비중(1.0/0.5).
IBS는 실 고저가 필요 → TQQQ는 실데이터(2010~)만 유효(합성 pre-2010은 H=L).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
FEE, SLIP = 0.0007, 0.0005
BUY_C, SELL_C = 1 + FEE + SLIP, 1 - FEE - SLIP


def load_ohlc(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)


def sgov_ret():
    s = pd.read_csv(DATA / "SGOV_SYNTH.csv", index_col="Date", parse_dates=True)["Close"]
    return s.pct_change().fillna(0.0)


def rsi(close, period=2):
    d = close.diff()
    up = d.clip(lower=0.0); dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1 / period, adjust=False).mean()
    rd = dn.ewm(alpha=1 / period, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def ibs(df):
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    return ((df["Close"] - df["Low"]) / rng).fillna(0.5)


def signals_rsi2(df, thr):
    c = df["Close"]; ma200 = c.rolling(200).mean(); ma5 = c.rolling(5).mean()
    r = rsi(c, 2)
    entry = (c > ma200) & (r < thr)
    exit_ = (r > 60) | (c > ma5)
    return entry.fillna(False), exit_.fillna(False)


def signals_ibs(df, thr):
    c = df["Close"]; ma200 = c.rolling(200).mean(); ib = ibs(df)
    entry = (c > ma200) & (ib < thr)
    exit_ = ib > 0.8
    return entry.fillna(False), exit_.fillna(False)


def backtest(df, entry, exit_, frac=1.0, sret=None):
    """in/out 롱온리. 반환 (equity, trades[list of ret], hold_days[list])."""
    c = df["Close"]
    idx = c.index
    sret = sret if sret is not None else pd.Series(0.0, index=idx)
    cash = 1.0; shares = 0.0; in_pos = False
    entry_cost = 0.0; entry_i = 0
    eq, trades, holds = [], [], []
    for i, d in enumerate(idx):
        cash *= (1 + sret.get(d, 0.0))
        p = c.iloc[i]
        if in_pos and exit_.iloc[i]:
            proceeds = shares * p * SELL_C
            cash += proceeds
            trades.append(proceeds / entry_cost - 1)
            holds.append(i - entry_i)
            shares = 0.0; in_pos = False
        elif (not in_pos) and entry.iloc[i]:
            invest = frac * cash
            shares = invest / (p * BUY_C)
            cash -= invest
            entry_cost = invest; entry_i = i; in_pos = True
        eq.append((d, cash + shares * p))
    if in_pos:
        proceeds = shares * c.iloc[-1] * SELL_C
        trades.append(proceeds / entry_cost - 1); holds.append(len(idx) - entry_i)
    e = pd.Series(dict(eq)); e.index = pd.DatetimeIndex(e.index)
    return e, np.array(trades), np.array(holds)


def run_strategy(kind, ticker, thr, frac):
    """kind='rsi2'|'ibs'. 반환 (equity, info)."""
    df = load_ohlc(ticker)
    sig = signals_rsi2(df, thr) if kind == "rsi2" else signals_ibs(df, thr)
    eq, trades, holds = backtest(df, sig[0], sig[1], frac, sgov_ret())
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    info = {"n_trades": len(trades), "trades_per_yr": len(trades) / yrs if yrs else 0,
            "avg_hold": float(np.mean(holds)) if len(holds) else 0.0,
            "win_rate": float(np.mean(trades > 0)) if len(trades) else float("nan")}
    return eq, info
