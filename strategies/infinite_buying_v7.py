"""무한매수법 v7 — '토핑 한정' 변동성 필터 (두 이득의 합집합 시도).

[배경] v6에서 밝혀낸 것: 변동성 필터의 두 이득은 정반대 국면에 산다.
  - 상승 국면 고변동성 차단 = 토핑/폭락 방어(v5) — 그러나 V자 반등 상방까지 죽인다.
  - 하락 국면 고변동성 차단 = dead-cat 회피(v6) — 순이득이나 폭락 방어는 없다.
단순 레짐 게이트(하락 한정)로는 둘 다 못 얻는다.

[v7 아이디어] 상승 국면 고변동성 차단을 '무조건'이 아니라 '토핑 국면 한정'으로:
  - 토핑 판별: 가격이 200MA 위로 크게 확장(가격/200MA - 1 > ext_threshold). 2000·2007·2021 고점.
  - V자 초기 반등(COVID): 가격이 200MA를 갓 회복 → 확장이 작음 → 토핑 아님 → 통과(상방 회복).
  - 차단 조건: 고변동성 AND (토핑 OR 하락국면). v5의 토핑 방어 + v6의 dead-cat 회피 합집합.

vol_mode로 세 모드 선택 (비교용):
  - "uncond" : 고변동성이면 무조건 차단 (= v5)
  - "down"   : 고변동성 AND 하락국면만 차단 (= v6)
  - "topping": 고변동성 AND (토핑 OR 하락국면) 차단 (= v7)

(A)기울기·(B)스트릭·쿼터손절·현금비중·쿨다운은 v6 계승.
"""
from __future__ import annotations  # py3.9 호환(어노테이션 지연평가, 동작 불변)

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Params:
    divisions: int = 40
    take_profit_pct: float = 0.15
    exhaust_action: str = "sell"   # "sell" | "hold" | "quarter"
    fee_pct: float = 0.0007
    slippage_pct: float = 0.0005
    initial_capital: float = 40_000.0
    allocation: float = 1.0
    use_trend_filter: bool = False
    quarter_days: int = 4
    reentry_cooldown_days: int = 0
    cooldown_when_down_only: bool = True
    use_vol_filter: bool = False
    vol_mode: str = "topping"      # "uncond" | "down" | "topping"


@dataclass
class Cycle:
    start: pd.Timestamp
    end: pd.Timestamp = None
    invested: float = 0.0
    proceeds: float = 0.0
    days: int = 0
    reason: str = ""

    @property
    def pnl_pct(self) -> float:
        return self.proceeds / self.invested - 1 if self.invested else 0.0


@dataclass
class Result:
    equity: pd.Series = None
    cycles: list = field(default_factory=list)


def trend_signal_v7(qqq: pd.Series, target_index, window: int = 200,
                    require_rising: bool = True, slope_lookback: int = 20,
                    confirm_days: int = 1) -> pd.Series:
    ma = qqq.rolling(window).mean()
    base = qqq.ge(ma)
    if require_rising:
        base = base & ma.ge(ma.shift(slope_lookback))
    if confirm_days > 1:
        base = base.rolling(confirm_days).sum().ge(confirm_days)
    base = base.where(ma.notna(), True)
    return base.reindex(target_index).ffill().fillna(True)


def regime_down_signal(qqq: pd.Series, target_index,
                       fast: int = 50, slow: int = 200) -> pd.Series:
    ma_f = qqq.rolling(fast).mean()
    ma_s = qqq.rolling(slow).mean()
    down = ma_f.lt(ma_s)
    down = down.where(ma_s.notna(), False)
    return down.reindex(target_index).ffill().fillna(False)


def vol_filter_signal(qqq: pd.Series, target_index, window: int = 20,
                      ref_window: int = 252, max_percentile: float = 0.80) -> pd.Series:
    vol = qqq.pct_change().rolling(window).std()
    thresh = vol.rolling(ref_window).quantile(max_percentile)
    ok = vol.le(thresh)
    ok = ok.where(thresh.notna(), True)
    return ok.reindex(target_index).ffill().fillna(True)


def topping_signal(qqq: pd.Series, target_index, ma_window: int = 200,
                   ext_threshold: float = 0.10) -> pd.Series:
    """토핑 플래그: 가격이 200MA 위로 ext_threshold 이상 확장된 상태.
    2000·2007·2021 고점처럼 오래 오른 뒤 과열된 국면을 잡되, 200MA를 갓 회복한
    V자 초기 반등(확장 작음)은 제외한다. MA 미산출 구간은 False(토핑 아님).
    """
    ma = qqq.rolling(ma_window).mean()
    ext = qqq / ma - 1.0
    top = ext.gt(ext_threshold)
    top = top.where(ma.notna(), False)
    return top.reindex(target_index).ffill().fillna(False)


def run(close: pd.Series, p: Params, trend_ok: pd.Series | None = None,
        regime_down: pd.Series | None = None, vol_ok: pd.Series | None = None,
        topping: pd.Series | None = None) -> Result:
    buy_cost = 1 + p.fee_pct + p.slippage_pct
    sell_cost = 1 - p.fee_pct - p.slippage_pct

    idle_cash = p.initial_capital * (1.0 - p.allocation)
    cash = p.initial_capital * p.allocation
    shares = 0.0
    invested = 0.0
    buys_done = 0
    one_buy = 0.0
    cycle = None
    cycles: list[Cycle] = []
    equity = []

    liquidating = False
    liq_per_day = 0.0
    liq_days_left = 0

    block_until = 0
    use_trend = p.use_trend_filter and trend_ok is not None
    use_vol = p.use_vol_filter and vol_ok is not None

    def eq(price):
        return idle_cash + cash + shares * price

    def cooldown_active(date) -> bool:
        if p.reentry_cooldown_days <= 0:
            return False
        if not p.cooldown_when_down_only:
            return True
        if regime_down is None:
            return False
        return bool(regime_down.get(date, False))

    def vol_blocks(date) -> bool:
        if bool(vol_ok.get(date, True)):
            return False  # 변동성 낮음 → 허용
        if p.vol_mode == "uncond":
            return True
        down = bool(regime_down.get(date, False)) if regime_down is not None else False
        if p.vol_mode == "down":
            return down
        # "topping": 토핑 OR 하락국면
        top = bool(topping.get(date, False)) if topping is not None else False
        return top or down

    for day_i, (date, price) in enumerate(close.items()):
        # 1) 익절 판정
        if shares > 0 and cycle is not None and not liquidating:
            value = shares * price * sell_cost
            if value >= invested * (1 + p.take_profit_pct):
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "take_profit"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                equity.append((date, eq(price)))
                continue

        # 2) 회분 소진 처리
        if cycle and buys_done >= p.divisions:
            if p.exhaust_action == "sell":
                value = shares * price * sell_cost
                pnl = value / invested - 1 if invested else 0.0
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "exhausted"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                if pnl <= 0 and cooldown_active(date):
                    block_until = day_i + p.reentry_cooldown_days
                equity.append((date, eq(price)))
                continue

            if p.exhaust_action == "quarter":
                if not liquidating:
                    liquidating = True
                    liq_per_day = shares / p.quarter_days
                    liq_days_left = p.quarter_days
                sell_shares = min(liq_per_day, shares)
                proceeds = sell_shares * price * sell_cost
                cash += proceeds
                shares -= sell_shares
                cycle.proceeds += proceeds
                liq_days_left -= 1
                if liq_days_left <= 0 or shares <= 1e-9:
                    if shares > 1e-9:
                        rest = shares * price * sell_cost
                        cash += rest
                        cycle.proceeds += rest
                    shares = 0.0
                    cycle.end, cycle.reason = date, "exhausted"
                    pnl = cycle.pnl_pct
                    cycles.append(cycle)
                    invested, buys_done, cycle = 0.0, 0, None
                    liquidating, liq_per_day, liq_days_left = False, 0.0, 0
                    if pnl <= 0 and cooldown_active(date):
                        block_until = day_i + p.reentry_cooldown_days
                equity.append((date, eq(price)))
                continue
            # "hold"면 매수 없이 익절 대기

        # 3) 정규 매수. 신규 사이클은 추세+변동성(토핑한정) 필터 통과 & 쿨다운 해제 시에만 시작.
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                allowed = True
                if use_trend:
                    allowed = bool(trend_ok.get(date, True))
                if allowed and use_vol and vol_blocks(date):
                    allowed = False
                if allowed and day_i < block_until:
                    allowed = False
                if not allowed:
                    equity.append((date, eq(price)))
                    continue
                cycle = Cycle(start=date)
                one_buy = cash / p.divisions
            spend = min(one_buy, cash)
            qty = spend / (price * buy_cost)
            cash -= spend
            shares += qty
            invested += spend
            buys_done += 1
            cycle.invested = invested
            cycle.days += 1

        equity.append((date, eq(price)))

    if cycle is not None:
        value = shares * close.iloc[-1] * sell_cost
        cycle.end, cycle.reason = close.index[-1], "eof"
        cycle.proceeds += value
        cycles.append(cycle)

    eq_ser = pd.Series(dict(equity)).sort_index()
    eq_ser.index = pd.DatetimeIndex(eq_ser.index)
    return Result(equity=eq_ser, cycles=cycles)
