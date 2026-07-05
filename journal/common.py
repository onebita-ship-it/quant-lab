"""매매일지 공용 모듈 — 확정 룰북(result_final.md §0)을 코드화한 공유 로직.

- 설정(config.json) / 상태(state.json) 로드·저장
- 가격 시리즈 로딩 (data/ 캐시, 선택적 yfinance 갱신)
- 추세 필터 신호 계산 (QQQ 200일선 상승 20일 판정 + 5일 스트릭)
- 비용(수수료+슬리피지) 헬퍼

state.json은 log_trade.py가 갱신하는 '현재 포트폴리오/사이클 상태'.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

JOURNAL_DIR = Path(__file__).resolve().parent
ROOT = JOURNAL_DIR.parent
DATA_DIR = ROOT / "data"
CONFIG_PATH = JOURNAL_DIR / "config.json"
STATE_PATH = JOURNAL_DIR / "state.json"
TRADES_PATH = JOURNAL_DIR / "trades.csv"

DEFAULT_CONFIG = {
    "trade_ticker": "TQQQ",
    "signal_ticker": "QQQ",
    "divisions": 40,
    "take_profit_pct": 0.15,
    "fee_pct": 0.0007,
    "slippage_pct": 0.0005,
    "ma_window": 200,
    "slope_lookback": 20,
    "confirm_days": 5,
    "quarter_days": 4,
    "total_capital": 40000.0,
    "deploy_frac": 1.0,          # 1.0=100% / 0.67=67%+리저브
    "reserve_triggers": [-0.30, -0.50],
}


def default_state(cfg):
    total = cfg["total_capital"]
    frac = cfg["deploy_frac"]
    return {
        "cash": round(total * frac, 2),
        "reserve": round(total * (1 - frac), 2),
        "shares": 0.0,
        "invested": 0.0,        # 현재 사이클 투입원가(비용 포함)
        "buys_done": 0,
        "one_buy": 0.0,         # 사이클 시작 시 고정된 1회분 금액
        "cycle_active": False,
        "cycle_start": None,
        "cycle_proceeds": 0.0,  # 현재 사이클 누적 매도대금(쿼터손절 다일 합산용)
        "cycle_seq": 0,         # 사이클 일련번호 카운터
        "current_cycle_id": None,
        "liquidating": False,
        "liq_left": 0,
        "liq_per_day": 0.0,
        "peak_equity": round(total, 2),
        "reserve_tiers_fired": [],
        "cycles_closed": [],    # {id,start,end,invested,proceeds,pnl_pct,reason}
    }


def _load(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def load_config():
    cfg = _load(CONFIG_PATH, None)
    if cfg is None:
        cfg = dict(DEFAULT_CONFIG)
        save_config(cfg)
    return cfg


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def load_state(cfg):
    st = _load(STATE_PATH, None)
    if st is None:
        st = default_state(cfg)
        save_state(st)
    return st


def save_state(st):
    STATE_PATH.write_text(json.dumps(st, indent=2, ensure_ascii=False), encoding="utf-8")


def buy_cost(cfg):
    return 1 + cfg["fee_pct"] + cfg["slippage_pct"]


def sell_cost(cfg):
    return 1 - cfg["fee_pct"] - cfg["slippage_pct"]


def load_price(ticker, refresh=False):
    """data/ 캐시에서 종가 시리즈 로드. refresh=True면 yfinance로 최신 갱신 시도."""
    path = DATA_DIR / f"{ticker}.csv"
    if refresh:
        try:
            import yfinance as yf
            df = yf.download(ticker, period="2y", progress=False, auto_adjust=True)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                # 기존 캐시와 병합(과거 구간 보존)
                if path.exists():
                    old = pd.read_csv(path, index_col="Date", parse_dates=True)
                    merged = pd.concat([old[["Close"]], df[["Close"]]])
                    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                    s = merged["Close"].dropna()
                else:
                    s = df["Close"].dropna()
                return s
        except Exception as e:
            print(f"[경고] {ticker} 갱신 실패({e}) → 캐시 사용")
    df = pd.read_csv(path, index_col="Date", parse_dates=True)
    return df["Close"].dropna()


def signal_series(qqq, cfg):
    """날짜별 진입허용 판정 요소를 담은 DataFrame 반환."""
    ma = qqq.rolling(cfg["ma_window"]).mean()
    above = qqq >= ma
    rising = ma >= ma.shift(cfg["slope_lookback"])
    ok = above & rising
    streak = ok.rolling(cfg["confirm_days"]).sum() >= cfg["confirm_days"]
    return pd.DataFrame({
        "close": qqq, "ma": ma, "above": above, "rising": rising,
        "ok_today": ok, "entry_ok": streak.fillna(False),
    })


def latest_signal(qqq, cfg, asof=None):
    sig = signal_series(qqq, cfg)
    if asof is not None:
        sig = sig.loc[:pd.Timestamp(asof)]
    row = sig.iloc[-1]
    date = sig.index[-1]
    # 스트릭 길이(연속 ok_today 참) 계산
    oks = sig["ok_today"].values
    streak = 0
    for v in oks[::-1]:
        if bool(v):
            streak += 1
        else:
            break
    return {
        "date": date, "close": float(row["close"]), "ma": float(row["ma"]),
        "above": bool(row["above"]), "rising": bool(row["rising"]),
        "streak": int(streak), "confirm_days": cfg["confirm_days"],
        "entry_ok": bool(row["entry_ok"]),
    }


def equity(st, price):
    return st["reserve"] + st["cash"] + st["shares"] * price


def fmt_won(x):
    return f"${x:,.2f}"
