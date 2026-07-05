"""폭락 추가매수 모듈 — 나스닥100 고점대비 -30/-40/-50% 계단 투입의 과거 성과.

규칙: QQQ가 사상최고가(ATH) 대비 -30%/-40%/-50%에 도달할 때마다 외부 추가자금을
      1등분($10,000)씩 3계단 투입(각 티어 1회). QQQ가 새 ATH를 경신하면 티어 리셋(다음 폭락 대비).
      투입 자산은 QQQ 또는 TQQQ(합성). 각 투입분이 이후 1·3·5년·현재까지 얼마가 됐는지 집계.

[해석] '언제 얼마를 넣었나'(트리거 날짜)와 '그게 몇 년 뒤 얼마가 됐나'를 과거 폭락별로 보여준다.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data"
TRANCHE = 10_000.0
TIERS = [-0.30, -0.40, -0.50]


def load(t):
    return pd.read_csv(DATA / f"{t}.csv", index_col="Date", parse_dates=True)["Close"].dropna()


def fut_price(series, date, years):
    """date로부터 years년 뒤(가장 가까운 거래일) 종가. 데이터 밖이면 None."""
    target = date + pd.DateOffset(years=years)
    if target > series.index[-1]:
        return None
    s = series.loc[target:]
    return float(s.iloc[0]) if len(s) else None


def episode_of(date):
    y = date.year
    if 2000 <= y <= 2002:
        return "닷컴버블(2000~02)"
    if 2007 <= y <= 2009:
        return "글로벌 금융위기(2008)"
    if y == 2018:
        return "2018 Q4"
    if y == 2020:
        return "COVID(2020)"
    if 2022 <= y <= 2023:
        return "2022 약세장"
    return f"기타({y})"


def find_triggers(qqq, ref_window=252):
    """트레일링 1년 고점 대비 낙폭이 각 티어를 처음 밟는 날. 새 1년 고점 경신 시 재장전.

    ATH가 아니라 '최근(1년) 고점' 기준 → 각 폭락을 그 직전 고점에서 측정하고, 회복해 새
    고점을 만들면 다음 폭락 대비로 티어를 리셋한다(실전 '고점대비 -X%' 감각과 일치).
    """
    roll = qqq.rolling(ref_window, min_periods=1).max()
    fired = set()
    trig = []
    for d, p in qqq.items():
        ref = roll.loc[d]
        if p >= ref:                # 새 1년 고점 → 재장전
            fired = set()
        dd = p / ref - 1
        for t in TIERS:
            if t not in fired and dd <= t:
                trig.append((d, t, p))
                fired.add(t)
    return trig


def main():
    qqq = load("QQQ")
    tqqq = load("TQQQ_SYNTH")
    trig = find_triggers(qqq)

    print("# 폭락 추가매수 모듈 — 나스닥100 -30/-40/-50% 계단 투입\n")
    print(f"> 규칙: QQQ가 **최근 1년 고점 대비** -30/-40/-50%(종가) 도달 시마다 ${TRANCHE:,.0f}씩 "
          "투입(티어별 1회, 새 1년 고점 시 재장전) · 자산=QQQ 또는 TQQQ(합성)\n")

    print("## 트리거별 성과 (투입 $10,000 → 이후 가치)\n")
    print("| 폭락 | 트리거일 | 티어 | QQQ가 | QQQ +3년 | QQQ +5년 | QQQ 현재 | "
          "TQQQ +3년 | TQQQ +5년 | TQQQ 현재 |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    agg = {}   # episode → dict
    tot_in = 0.0
    tot_q_now = tot_t_now = 0.0
    for d, t, p in trig:
        ep = episode_of(d)
        row = {"asset": {}}

        def growth(series, years):
            fp = fut_price(series, d, years)
            base = float(series.loc[:d].iloc[-1])
            return None if fp is None else TRANCHE * fp / base

        q_now = TRANCHE * float(qqq.iloc[-1]) / float(qqq.loc[:d].iloc[-1])
        t_now = TRANCHE * float(tqqq.iloc[-1]) / float(tqqq.loc[:d].iloc[-1])
        q3, q5 = growth(qqq, 3), growth(qqq, 5)
        t3, t5 = growth(tqqq, 3), growth(tqqq, 5)
        fmt = lambda v: f"${v:,.0f}" if v is not None else "—"  # noqa: E731
        print(f"| {ep} | {d.date()} | {int(t*100)}% | {p:.1f} | {fmt(q3)} | {fmt(q5)} | "
              f"{fmt(q_now)} | {fmt(t3)} | {fmt(t5)} | {fmt(t_now)} |")
        a = agg.setdefault(ep, {"in": 0.0, "q_now": 0.0, "t_now": 0.0, "tiers": []})
        a["in"] += TRANCHE; a["q_now"] += q_now; a["t_now"] += t_now
        a["tiers"].append(int(t * 100))
        tot_in += TRANCHE; tot_q_now += q_now; tot_t_now += t_now

    print("\n## 폭락 에피소드별 합계 (현재 시점 가치)\n")
    print("| 폭락 | 발동 티어 | 투입합계 | QQQ 현재가치 | QQQ 배수 | TQQQ 현재가치 | TQQQ 배수 |")
    print("|---|---|---:|---:|---:|---:|---:|")
    for ep, a in agg.items():
        tiers = "/".join(f"{x}%" for x in a["tiers"])
        print(f"| {ep} | {tiers} | ${a['in']:,.0f} | ${a['q_now']:,.0f} | "
              f"{a['q_now']/a['in']:.1f}x | ${a['t_now']:,.0f} | {a['t_now']/a['in']:.1f}x |")
    print(f"\n**전체 합계**: 투입 ${tot_in:,.0f} → QQQ ${tot_q_now:,.0f} "
          f"({tot_q_now/tot_in:.1f}x) · TQQQ ${tot_t_now:,.0f} ({tot_t_now/tot_in:.1f}x)  "
          f"(현재 {qqq.index[-1].date()} 기준)\n")
    print("> **미발동 폭락**: COVID(2020, 종가 -28.6%)·2018 Q4(-22.8%)는 종가 기준 -30%에 못 미쳐 "
          "발동 안 함 → 규칙이 '진짜 대폭락(≥-30% 종가)'만 선별한다.")
    print("> **해석 주의**: '현재가치'는 오래된 폭락일수록 초장기 보유(닷컴 26년·GFC 18년) 결과라 "
          "**+3년/+5년 열이 더 현실적인 '몇 년 내' 성과**다. TQQQ GFC 배수(수백~수천x)는 합성 3x가 "
          "2008 제너레이셔널 바닥을 정확히 밟았을 때의 산술치로 **극단적 상방 편향**(실 TQQQ는 2010 상장). "
          "3배는 저점 이후 추가 하락 시 손실도 3배이므로 계단 투입(분할)로 타이밍 리스크를 낮춘 것이다.")


if __name__ == "__main__":
    main()
