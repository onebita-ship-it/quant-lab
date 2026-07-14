"""매일 아침 실행 — 오늘 진입 조건(추세 AND 카나리아) + 1회분 금액 + 위성 신호 브리핑.

사용:
  python journal/daily_check.py                # 캐시 데이터로 오늘 브리핑
  python journal/daily_check.py --refresh      # yfinance로 최신가 갱신 후
  python journal/daily_check.py --asof 2020-03-23   # 특정일 기준(시뮬)

판정 범위 (룰북 = result_final.md §0):
  ① 코어 진입 게이트 = 200일선 3조건 AND 13612W 카나리아(SPY·EFA·EEM·AGG, 월말 판정)
  ⑦ SGOV 파킹(금요일 알림)  ⑧ 위성(v9 엔진A) 오늘 타깃(원지수 추세 ON 중 모멘텀 1위)

상태(state.json)는 변경하지 않는다(조언용). 체결 후엔 log_trade.py로 기록.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from journal import common as C  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="yfinance로 최신가 갱신")
    ap.add_argument("--asof", default=None, help="기준일(YYYY-MM-DD), 시뮬용")
    args = ap.parse_args()

    cfg = C.load_config()
    st = C.load_state(cfg)
    qqq = C.load_price(cfg["signal_ticker"], refresh=args.refresh)
    tqqq = C.load_price(cfg["trade_ticker"], refresh=args.refresh)
    if args.asof:
        import pandas as pd
        qqq = qqq.loc[:pd.Timestamp(args.asof)]
        tqqq = tqqq.loc[:pd.Timestamp(args.asof)]

    sig = C.latest_signal(qqq, cfg)
    price = float(tqqq.iloc[-1])
    pdate = tqqq.index[-1].date()
    asof = args.asof or sig["date"]
    canary = C.canary_status(asof, refresh=args.refresh)
    entry_ok = sig["entry_ok"] and canary["ok"]  # 룰북 ① = 추세 AND 카나리아

    print("=" * 60)
    print(f" 매매일지 아침 브리핑  |  기준일 {sig['date'].date()}")
    print("=" * 60)
    ck = lambda b: "✅" if b else "❌"  # noqa: E731
    print(f"\n[추세 필터 — {cfg['signal_ticker']} 200일선 판정]")
    print(f"  {cfg['signal_ticker']} 종가 {sig['close']:.2f}  vs  200일선 {sig['ma']:.2f}")
    print(f"  {ck(sig['above'])} 종가 > 200일선")
    print(f"  {ck(sig['rising'])} 200일선 상승({cfg['slope_lookback']}일 전 대비)")
    print(f"  {ck(sig['streak'] >= sig['confirm_days'])} 연속 충족 {sig['streak']}/{sig['confirm_days']}일")

    print(f"\n[13612W 카나리아 — 룰북 ① v10 (판정 월말 {canary['cutoff']}, 이번 달 유지)]")
    for a in canary["assets"]:
        if a["missing"]:
            print(f"  ❌ {a['ticker']:<4} 데이터 없음 → python scripts/download_data.py 실행")
        elif a["mom"] is None:
            print(f"  ⚠️ {a['ticker']:<4} 이력 253일 미만 → 판정 제외")
        else:
            print(f"  {ck(a['mom'] >= 0)} {a['ticker']:<4} 13612W {a['mom']:+.3f}  (기준 {a['date']})")
    print(f"  → 카나리아: {'통과 ✅' if canary['ok'] else '차단 ❌ (하나라도 음수면 신규 진입 금지)'}")

    print(f"\n  ▶ 신규 진입 게이트(추세 AND 카나리아): {'충족 ✅' if entry_ok else '미충족 ❌'}")

    print(f"\n[포트폴리오 상태]  ({cfg['trade_ticker']} 종가 {price:.2f}, {pdate})")
    div = cfg["divisions"]
    print(f"  운용현금 {C.fmt_won(st['cash'])} | 리저브 {C.fmt_won(st['reserve'])} | "
          f"보유 {st['shares']:.4f}주")
    if st["cycle_active"]:
        avg = st["invested"] / st["shares"] if st["shares"] else 0.0
        val = st["shares"] * price * C.sell_cost(cfg)
        gain = val / st["invested"] - 1 if st["invested"] else 0.0
        tp = cfg["take_profit_pct"]
        # 익절(+tp)까지: 남은 손익 여유 + 필요한 추가 상승률
        gap_pp = (tp - gain) * 100
        need_up = (1 + tp) / (1 + gain) - 1 if (1 + gain) > 0 else float("inf")
        print(f"  사이클 진행 중: {st['buys_done']}/{div}회차, 평단(비용포함) {avg:.2f}, "
              f"평가손익 {gain:+.1%}")
        if gain >= tp:
            print(f"  🎯 익절 도달! (목표 +{tp:.0%})")
        else:
            print(f"  익절(+{tp:.0%})까지: {gap_pp:+.1f}%p 남음 "
                  f"(가격 약 +{need_up:.1%} 더 오르면 익절)")

    print("\n[오늘 할 일]")
    tp = cfg["take_profit_pct"]
    if st["cycle_active"]:
        val = st["shares"] * price * C.sell_cost(cfg)
        if st["shares"] > 0 and val >= st["invested"] * (1 + tp) and not st["liquidating"]:
            print(f"  🎯 익절! 평가액이 평단 대비 +{tp:.0%} 도달 → 전량 매도 ({st['shares']:.4f}주)")
            print(f"     체결 후: python journal/log_trade.py --action take_profit "
                  f"--shares {st['shares']:.4f} --price <체결가>")
        elif st["liquidating"] or st["buys_done"] >= div:
            per = st["liq_per_day"] if st["liquidating"] else st["shares"] / cfg["quarter_days"]
            left = st["liq_left"] if st["liquidating"] else cfg["quarter_days"]
            print(f"  🔻 쿼터손절 진행: 40회분 소진 → 4일 분할청산. 오늘 {per:.4f}주 매도 "
                  f"(남은 {left}일)")
            print(f"     체결 후: python journal/log_trade.py --action quarter "
                  f"--shares {per:.4f} --price <체결가>")
        else:
            one = st["one_buy"]
            est = one / (price * C.buy_cost(cfg))
            print(f"  🟩 정규 매수: 1회분 {C.fmt_won(one)} → 약 {est:.4f}주 ({st['buys_done']+1}/{div}회차)")
            print(f"     체결 후: python journal/log_trade.py --action buy "
                  f"--shares <체결주> --price <체결가>")
    else:
        if entry_ok and st["cash"] > 1e-6:
            one = st["cash"] / div
            est = one / (price * C.buy_cost(cfg))
            print(f"  🟩 새 사이클 시작 + 1회차 매수: 1회분 {C.fmt_won(one)} (= 현금/{div}) "
                  f"→ 약 {est:.4f}주")
            print(f"     체결 후: python journal/log_trade.py --action buy "
                  f"--shares <체결주> --price <체결가> --new-cycle")
        else:
            if not sig["entry_ok"]:
                why = "추세 미충족"
            elif not canary["ok"]:
                why = "카나리아 차단"
            else:
                why = "운용현금 없음"
            print(f"  ⏸  진입 대기 — {why}. 오늘 주문 없음.")

    # 파킹(SGOV) 안내 — 미투입 현금은 SGOV로, 금요일엔 다음 주 5회분 매도(룰북 ⑦)
    print("\n[파킹(SGOV) — 룰북 ⑦]")
    one = st["one_buy"] if st["cycle_active"] and st["one_buy"] > 0 else st["cash"] / div
    if st["cash"] > 1e-6 or st["reserve"] > 1e-6:
        print(f"  미투입 현금 {C.fmt_won(st['cash'])} + 리저브 {C.fmt_won(st['reserve'])}")
        if st["cycle_active"] and st["one_buy"] > 0:
            # 룰북 ⑦: 사이클 시작 시 첫 10회분만 예수금, 이후 주간 5회분 버퍼. 나머지는 SGOV.
            print(f"    → 예수금은 버퍼만 유지: 사이클 초기 10회분(약 {C.fmt_won(10 * one)}), "
                  f"이후 주간 5회분(약 {C.fmt_won(5 * one)}). 나머지 현금은 SGOV 파킹.")
            print(f"    → 리저브 {C.fmt_won(st['reserve'])}는 전액 SGOV(발동 시 매도 후 투입).")
        else:
            # 신호 대기·리저브 자금은 발생 즉시 전액 SGOV
            print(f"    → 신호 대기·리저브 자금이므로 전액 SGOV 파킹 유지.")
    weekday = tqqq.index[-1].weekday()  # 0=월 … 4=금
    if weekday == 4:
        next5 = 5 * one
        print(f"  📅 금요일 — 다음 주 5회분 예수금 확보: SGOV {C.fmt_won(next5)}어치 매도 "
              f"(1회분 {C.fmt_won(one)} × 5). 매일 매도 금지.")
    else:
        wd = "월화수목금토일"[weekday]
        print(f"  오늘은 {wd}요일 — SGOV 매도일 아님(금요일에 다음 주 5회분만 매도).")

    # 위성(v9 엔진A) 신호 — 룰북 ⑧. 상태 추적 없이 판정만(보유 자산과 비교는 본인이).
    print("\n[위성(엔진A) 신호 — 룰북 ⑧ (계좌의 42.5%)]")
    try:
        sat = C.satellite_status(cfg, asof=args.asof, refresh=args.refresh)
        for r in sorted(sat["assets"], key=lambda x: (x["mom"] is None, -(x["mom"] or 0))):
            if r["missing"]:
                print(f"  ❌ {r['ticker']:<5} 데이터 없음(원지수 {r['index']}) "
                      f"→ python scripts/download_data.py 실행")
                continue
            mom = f"{r['mom']:+.3f}" if r["mom"] is not None else "  n/a"
            print(f"  {ck(bool(r['on']))} {r['ticker']:<5} 원지수 {r['index']:<4} "
                  f"추세 {'ON ' if r['on'] else 'OFF'} (스트릭 {r['streak']}/{cfg['confirm_days']})"
                  f" · 3·6모멘텀 {mom}")
        print(f"  → 오늘 타깃: {sat['target']}"
              + ("  (추세 ON 자산 없음 → 전량 SGOV)" if sat["target"] == "SGOV" else ""))
        if C.is_month_end(pdate):
            print("  📅 오늘은 월말(근사) — 위성 리밸런스일. 종가에 타깃과 보유 일치시킬 것.")
        else:
            print("  월중엔 '보유 자산 신호 OFF → 즉시 타깃 교체'만. 그 외 교체는 월말에.")
    except Exception as e:
        print(f"  ⚠️ 위성 신호 계산 실패({e}) — config/universe.txt·데이터 캐시 확인")

    # 리저브 안내
    if st["reserve"] > 1e-6:
        eq = C.equity(st, price)
        dd = eq / st["peak_equity"] - 1 if st["peak_equity"] else 0.0
        print(f"\n[리저브] 총자산 {C.fmt_won(eq)}, 고점대비 {dd:+.1%}. "
              f"발동선 {cfg['reserve_triggers']} (기발동 {st['reserve_tiers_fired']})")
    print()


if __name__ == "__main__":
    main()
