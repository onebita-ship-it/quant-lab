"""무한매수법 v4 — 쿨다운을 '하락 추세 국면에서만' 활성화.

[v3 문제] 손실 후 재진입 쿨다운(C)을 상시 적용하니, 상승장의 정상적 되돌림에서
소진 손실이 날 때도 20일씩 시장을 비워 복리를 놓침 → 전체구간 CAGR 14.0% → 4.6% 붕괴.

[v4 개선] 쿨다운을 **하락 국면(regime_down)일 때만** 발동:
  - 상승장(50MA >= 200MA): 손실이 나도 쿨다운 없음 → 추세 필터 규칙대로 곧바로 재참여(CAGR 보존).
  - 하락장(50MA < 200MA, 데드크로스 레짐): 손실 사이클 종료 후 N일 신규 진입 금지
    → 베어마켓 랠리 재진입 whipsaw 차단(스트레스 방어).

'하락 국면'은 진입 필터(가격>200MA·200MA 상승)보다 느린 신호(50/200 데드크로스)를 써서,
급락장 중 브리프 랠리로 진입 필터가 잠깐 켜져도 레짐은 여전히 '하락'으로 유지되게 한다.

(A)기울기 필터·(B)확정 스트릭·쿼터손절·현금비중은 v3 그대로 계승.
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
    cooldown_when_down_only: bool = True  # v4: 하락 국면에서만 쿨다운 발동


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


def trend_signal_v4(qqq: pd.Series, target_index, window: int = 200,
                    require_rising: bool = True, slope_lookback: int = 20,
                    confirm_days: int = 1) -> pd.Series:
    """신규 진입 허용(추세 필터) bool 시리즈. v3.trend_signal_v3와 동일 로직."""
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
    """하락 국면 플래그: 단기MA < 장기MA (데드크로스 레짐).

    진입 필터보다 느려서 베어마켓 랠리 구간에도 True를 유지 → 쿨다운이 그 구간을 덮는다.
    MA 미산출 구간은 False(하락 아님)로 처리해 초기엔 쿨다운을 켜지 않는다.
    """
    ma_f = qqq.rolling(fast).mean()
    ma_s = qqq.rolling(slow).mean()
    down = ma_f.lt(ma_s)
    down = down.where(ma_s.notna(), False)
    return down.reindex(target_index).ffill().fillna(False)


def run(close: pd.Series, p: Params, trend_ok: pd.Series | None = None,
        regime_down: pd.Series | None = None) -> Result:
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

    def eq(price):
        return idle_cash + cash + shares * price

    def cooldown_active(date) -> bool:
        """이 시점 손실이 쿨다운을 발동시킬 수 있는가."""
        if p.reentry_cooldown_days <= 0:
            return False
        if not p.cooldown_when_down_only:
            return True
        if regime_down is None:
            return False
        return bool(regime_down.get(date, False))

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

        # 3) 정규 매수. 추세 필터 통과 + 쿨다운 해제 시에만 신규 사이클 시작.
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                allowed = True
                if use_trend:
                    allowed = bool(trend_ok.get(date, True))
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
