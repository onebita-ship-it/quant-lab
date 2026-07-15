"""매매일지 공용 모듈 — 확정 룰북(result_final.md §0)을 코드화한 공유 로직.

- 설정(config.json) / 상태(state.json) 로드·저장
- 가격 시리즈 로딩 (data/ 캐시, 선택적 yfinance 갱신)
- 추세 필터 신호 계산 (QQQ 200일선 상승 20일 판정 + 5일 스트릭)
- 13612W 카나리아 게이트 (룰북 ① v10 — 월말 판정, 다음 달 유지)
- 위성(v9 엔진A) 신호 — 원지수 추세 ON 자산의 3·6개월 블렌드 모멘텀 순위 (룰북 ⑧)
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
UNIVERSE_PATH = ROOT / "config" / "universe.txt"

CANARY_TICKERS = ["SPY", "EFA", "EEM", "AGG"]

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
    "gold_ticker": "GLD",        # 룰북 ⑧ 금 슬리브(무상관 부품)
    # 계좌 전체 배분 = B안(공격형): 코어/위성 50/50 + 금 15% 오버레이 (룰북 ⑧)
    "allocation": {"core": 0.425, "satellite": 0.425, "gold": 0.15},
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
        "inception_date": None,  # 계좌 개시일(첫 매수 시 기록) — 연례 리밸런스·금 매수 기준
        "last_rebalance": None,  # 마지막 연 1회 계좌 리밸런스일(룰북 ⑧)
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


def w13612(h):
    """13612W 모멘텀 — 백테스트(`backtests/candidate_strategies.w13612`)와 동일한 거래일 오프셋.
    12×(1개월) + 4×(3개월) + 2×(6개월) + 1×(12개월). 데이터 253일 미만이면 None."""
    if len(h) < 253:
        return None
    r1 = h.iloc[-1] / h.iloc[-22] - 1
    r3 = h.iloc[-1] / h.iloc[-64] - 1
    r6 = h.iloc[-1] / h.iloc[-127] - 1
    r12 = h.iloc[-1] / h.iloc[-253] - 1
    return float(12 * r1 + 4 * r3 + 2 * r6 + r12)


def canary_status(asof, refresh=False, tickers=None):
    """13612W 카나리아 게이트 (룰북 ① v10) — 월말 판정 값이 다음 달 내내 유지.

    asof가 속한 달의 '직전 월말(마지막 거래일)' 종가 기준으로 4자산 모멘텀을 계산.
    백테스트(`final_audit_crossval.real_canary`)와 동일하게 데이터 253일 미만 자산은 판정에서
    제외하고, 판정 대상 중 하나라도 음수면 차단. 단 데이터 파일이 아예 없으면 fail-safe로 차단.
    """
    tickers = tickers or CANARY_TICKERS
    cutoff = pd.Timestamp(asof).replace(day=1) - pd.Timedelta(days=1)  # 지난달 말일
    rows, ok = [], True
    for t in tickers:
        try:
            h = load_price(t, refresh=refresh).loc[:cutoff]
        except FileNotFoundError:
            rows.append({"ticker": t, "mom": None, "date": None, "missing": True})
            ok = False  # 판정 불가 → 진입 차단 (scripts/download_data.py로 데이터 확보)
            continue
        v = w13612(h)
        rows.append({"ticker": t, "mom": v,
                     "date": h.index[-1].date() if len(h) else None, "missing": False})
        if v is not None and v < 0:
            ok = False
    return {"ok": ok, "assets": rows, "cutoff": cutoff.date()}


def load_universe():
    """config/universe.txt → [{'ticker','class','index'}] (분기 리뷰 재량 관리 파일)."""
    rows = []
    for line in UNIVERSE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 3:
            rows.append({"ticker": parts[0], "class": parts[1], "index": parts[2]})
    return rows


def blended_mom(h, short=63, long=126):
    """3·6개월 블렌드 모멘텀 — 백테스트(`engine_v9.blended_mom`)와 동일.
    0.5×(63거래일 수익) + 0.5×(126거래일 수익). 데이터 부족 시 None."""
    if len(h) < long + 1:
        return None
    return float(0.5 * (h.iloc[-1] / h.iloc[-1 - short] - 1)
                 + 0.5 * (h.iloc[-1] / h.iloc[-1 - long] - 1))


def satellite_status(cfg, asof=None, refresh=False):
    """위성(v9 엔진A) 오늘 신호 (룰북 ⑧) — 상태 없이 판정만.

    각 유니버스 자산의 원지수 추세(코어와 동일: 200MA 위+상승20일+5일 스트릭) ON/OFF와
    매매 티커의 3·6개월 블렌드 모멘텀을 계산해 오늘의 타깃(ON 중 모멘텀 1위, 없으면 SGOV) 반환.
    """
    idx_cache, rows = {}, []
    for u in load_universe():
        tk, ix = u["ticker"], u["index"]
        try:
            if ix not in idx_cache:
                idx_cache[ix] = load_price(ix, refresh=refresh)
            idx_s = idx_cache[ix]
            px = load_price(tk, refresh=refresh)
        except FileNotFoundError:
            rows.append({"ticker": tk, "index": ix, "on": None, "streak": 0,
                         "mom": None, "missing": True})
            continue
        if asof is not None:
            idx_s = idx_s.loc[:pd.Timestamp(asof)]
            px = px.loc[:pd.Timestamp(asof)]
        sig = latest_signal(idx_s, cfg)
        rows.append({"ticker": tk, "index": ix, "on": sig["entry_ok"],
                     "streak": sig["streak"], "mom": blended_mom(px), "missing": False})
    on = [r for r in rows if r["on"] and r["mom"] is not None]
    target = max(on, key=lambda r: r["mom"])["ticker"] if on else "SGOV"
    return {"assets": rows, "target": target}


def annual_rebalance_status(st, today):
    """연 1회 계좌 리밸런스 상태 (룰북 ⑧) — 조언용, 상태 불변.

    기준일(anchor) = 마지막 리밸런스일(없으면 개시일). 개시 전이면 started=False.
    기준일로부터 1년 지나면 due=True(리밸런스일 도래).
    """
    anchor = st.get("last_rebalance") or st.get("inception_date")
    if not anchor:
        return {"started": False, "anchor": None, "next_due": None,
                "due": False, "days_left": None}
    a = pd.Timestamp(anchor)
    nd = a + pd.DateOffset(years=1)
    t = pd.Timestamp(today)
    return {"started": True, "anchor": a.date(), "next_due": nd.date(),
            "due": t >= nd, "days_left": (nd - t).days}


def is_month_end(date):
    """다음 영업일이 다른 달이면 월말로 간주 (미국 휴장일은 근사 — 브리핑 용도)."""
    d = pd.Timestamp(date)
    return (d + pd.tseries.offsets.BDay(1)).month != d.month


def equity(st, price):
    return st["reserve"] + st["cash"] + st["shares"] * price


def fmt_won(x):
    return f"${x:,.2f}"
