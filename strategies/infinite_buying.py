"""라우어 무한매수법 v1 (단순화 버전).

규칙:
- 원금을 `divisions`등분(기본 40)하고, 매 거래일 종가에 1회분씩 매수
- 보유 포지션 평가액이 평단 대비 `take_profit_pct`(기본 +10%) 도달 시 전량 매도 → 사이클 종료, 다음 날 새 사이클 시작
- `divisions`회분을 모두 소진할 때까지 익절가에 못 닿으면 `exhaust_action`:
    - "sell": 전량 매도하고 새 사이클 (원조 방식에 가까운 쿼터손절의 단순화)
    - "hold": 익절가 도달까지 추가 매수 없이 보유
- 수수료/슬리피지는 매수·매도 양쪽에 반영

사이클 단위 기록을 남겨서 승률·사이클 수익률 분포를 계산할 수 있게 한다.
"""
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Params:
    divisions: int = 40
    take_profit_pct: float = 0.10
    exhaust_action: str = "sell"  # "sell" | "hold"
    fee_pct: float = 0.0007       # 편도 수수료 0.07%
    slippage_pct: float = 0.0005  # 편도 슬리피지 0.05%
    initial_capital: float = 40_000.0


@dataclass
class Cycle:
    start: pd.Timestamp
    end: pd.Timestamp = None
    invested: float = 0.0
    proceeds: float = 0.0
    days: int = 0
    reason: str = ""  # "take_profit" | "exhausted" | "eof"

    @property
    def pnl_pct(self) -> float:
        return self.proceeds / self.invested - 1 if self.invested else 0.0


@dataclass
class Result:
    equity: pd.Series = None
    cycles: list = field(default_factory=list)


def run(close: pd.Series, p: Params) -> Result:
    """close: 일별 종가 시리즈. 반환: 일별 평가액(equity)과 사이클 목록."""
    buy_cost = 1 + p.fee_pct + p.slippage_pct
    sell_cost = 1 - p.fee_pct - p.slippage_pct

    cash = p.initial_capital
    shares = 0.0
    invested = 0.0          # 현재 사이클 투입 원가 (수수료 포함)
    buys_done = 0
    one_buy = 0.0           # 사이클 시작 시 cash/divisions로 설정 (수수료 포함 총지출)
    cycle = None
    cycles: list[Cycle] = []
    equity = []

    for date, price in close.items():
        # 1) 익절 판정 (전일까지 산 물량 기준, 당일 종가로 평가)
        if shares > 0:
            value = shares * price * sell_cost
            if value >= invested * (1 + p.take_profit_pct):
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "take_profit"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                equity.append((date, cash))
                continue

        # 2) 회분 소진 처리
        if cycle and buys_done >= p.divisions:
            if p.exhaust_action == "sell":
                value = shares * price * sell_cost
                cash += value
                cycle.end, cycle.proceeds, cycle.reason = date, value, "exhausted"
                cycles.append(cycle)
                shares, invested, buys_done, cycle = 0.0, 0.0, 0, None
                equity.append((date, cash))
                continue
            # "hold"면 매수 없이 익절 대기

        # 3) 정규 매수 (1회분, 수수료 포함 총지출 기준이라 마지막 회분까지 소진됨)
        if buys_done < p.divisions and cash > 1e-9:
            if cycle is None:
                cycle = Cycle(start=date)
                one_buy = cash / p.divisions  # 복리: 사이클 시작 시점 원금 등분
            spend = min(one_buy, cash)
            qty = spend / (price * buy_cost)
            cash -= spend
            shares += qty
            invested += spend
            buys_done += 1
            cycle.invested = invested
            cycle.days += 1

        equity.append((date, cash + shares * price))

    # 마지막 미종료 사이클은 종가 청산으로 기록 (통계용)
    if cycle is not None:
        value = shares * close.iloc[-1] * sell_cost
        cycle.end, cycle.proceeds, cycle.reason = close.index[-1], value, "eof"
        cycles.append(cycle)

    eq = pd.Series(dict(equity)).sort_index()
    eq.index = pd.DatetimeIndex(eq.index)
    return Result(equity=eq, cycles=cycles)
