"""패리티 감사 — sheets/signals.gs(JS) vs journal/common.py·log_trade.py·daily_check.py(Python).

구글 시트 시스템은 룰북 §0 로직을 JS 로 재구현했으므로, 왜곡(구현 차이)이 없는지
파이썬 원본과 전 구간 비교한다. 이 감사를 통과해야 시트 로직을 신뢰할 수 있다.

검사 항목:
  1. 추세 필터     — signal_series 전 거래일 (MA·3조건·스트릭·entry_ok)
  2. latest_signal — 기준일 격자에서 스냅샷 비교
  3. 카나리아      — 매월 기준일에서 13612W·차단 판정 비교
  4. 위성 엔진A    — 기준일 격자에서 ON/OFF·모멘텀·타깃 비교
  5. 달력          — is_month_end·요일 전 거래일
  6. 리플레이      — 동일 체결 시나리오를 log_trade.py CLI 로 실행한 state.json vs
                     JS replayCore 결과 + 행별 준수 체크 비교 (키 소실도 실패로 판정)
  7. 리저브        — deploy_frac 0.67 + 폭락 시나리오에서 deploy_reserve 를 CLI 와 교차검증
                     (발동/미발동 판정 일치 + 발동 시 상태 일치)
  8. 오늘 할 일    — daily_check.py CLI 출력(익절/쿼터/매수/새사이클/대기 + 금액)과
                     JS prescribe/parkingInfo 비교

사용:
  python sheets/parity/run_parity.py

데이터: data/ 에 있는 티커는 실데이터를 쓰고, 없는 티커는 임시 디렉터리에 합성 생성한다.
⚠️ 운영 캐시 data/ 에는 아무것도 쓰지 않는다 (합성 시세가 실전 판정에 섞이면 안 되므로).
종료코드 0 = 전 항목 통과.
"""
import csv
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
REAL_DATA = ROOT / "data"
sys.path.insert(0, str(ROOT))
from journal import common as C  # noqa: E402

CFG = dict(C.DEFAULT_CONFIG)

# 패리티에 필요한 전체 티커 (신호/매매/카나리아/유니버스)
PARITY_TICKERS = ["QQQ", "TQQQ", "SPY", "EFA", "EEM", "AGG", "UPRO", "SOXL", "SOXX"]

# 합성 파라미터: (연수익, 연변동성, 하락구간 시작 거래일) — 하락구간 120일 동안 -0.4%/일을
# 얹어 추세 OFF·카나리아 차단 국면을 만들어 게이트 분기 커버리지를 확보한다.
SYNTH_PARAMS = {
    "QQQ": (0.12, 0.22, 500), "SPY": (0.09, 0.18, 520), "EFA": (0.06, 0.19, 480),
    "EEM": (0.05, 0.24, 460), "AGG": (0.03, 0.05, 900), "SOXX": (0.14, 0.30, 540),
}
LEVERAGED = {"TQQQ": "QQQ", "UPRO": "SPY", "SOXL": "SOXX"}
N_DAYS = 1750  # ≈ 7년


def build_data_dir(scratch: Path) -> Path:
    """실데이터는 심볼릭 링크, 없는 티커는 합성 생성 — 전부 임시 디렉터리에.
    운영 캐시(data/)는 절대 건드리지 않는다."""
    d = scratch / "data"
    d.mkdir(parents=True)
    dates = pd.bdate_range("2019-01-02", periods=N_DAYS)
    base_rets = {}
    made, linked = [], []
    for i, (t, (mu, sig, bear_at)) in enumerate(sorted(SYNTH_PARAMS.items())):
        rng = np.random.default_rng(1234 + i)
        ret = rng.normal(mu / 252, sig / np.sqrt(252), N_DAYS)
        ret[bear_at:bear_at + 120] -= 0.004  # 하락 국면
        base_rets[t] = ret
    for t in PARITY_TICKERS:
        real = REAL_DATA / f"{t}.csv"
        out = d / f"{t}.csv"
        if real.exists():
            out.symlink_to(real)
            linked.append(t)
            continue
        if t in SYNTH_PARAMS:
            daily = base_rets[t]
        else:
            daily = 3 * base_rets[LEVERAGED[t]] - 0.0095 / 252
        close = 100 * np.cumprod(1 + daily)
        pd.DataFrame({"Close": close}, index=dates).rename_axis("Date").to_csv(out)
        made.append(t)
    if linked:
        print(f"[데이터] 실데이터 사용: {', '.join(linked)}")
    if made:
        print(f"[데이터] 합성 생성(임시 디렉터리): {', '.join(made)} — 운영 data/ 는 건드리지 않음")
    return d


def load_universe():
    rows = []
    for line in (ROOT / "config" / "universe.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) >= 3:
            rows.append({"ticker": p[0], "cls": p[1], "index": p[2]})
    return rows


# ---------------- 비교 유틸 ----------------

FAILS = []


def close_enough(a, b, rel=1e-9, abs_tol=1e-9):
    """수치는 |a-b| <= max(abs_tol, rel*max(|a|,|b|)). 금액은 rel=0 으로 절대 오차만 허용
    (상대 오차는 큰 금액의 회계 불일치를 감춘다)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if isinstance(a, float) and math.isnan(a):
            return b is None
        return abs(a - b) <= max(abs_tol, rel * max(abs(a), abs(b)))
    return a == b


def check(section, name, py, js, rel=1e-9, abs_tol=1e-9):
    if not close_enough(py, js, rel=rel, abs_tol=abs_tol):
        FAILS.append(f"{section} | {name}: py={py!r} js={js!r}")


def money(section, name, py, js, abs_tol=0.05):
    """금액 비교 — 절대 오차만 (라운딩 방식 차이 half-even vs half-up 여유분)."""
    check(section, name, py, js, rel=0.0, abs_tol=abs_tol)


def nan_none(x):
    if x is None:
        return None
    try:
        xf = float(x)
        return None if math.isnan(xf) else xf
    except (TypeError, ValueError):
        return x


_reported = 0


def report(title, n_items):
    global _reported
    new = len(FAILS) - _reported
    _reported = len(FAILS)
    status = "✅" if new == 0 else f"❌ 불일치 {new}건"
    print(f"  {status}  {title} (표본 {n_items})")
    if new:
        for f in FAILS[-new:][:10]:
            print(f"      · {f}")


def parse_money(s):
    return float(s.replace(",", ""))


# ---------------- CLI 샌드박스 ----------------

def make_sandbox(scratch, data_dir, name, cfg):
    sb = scratch / name
    (sb / "journal").mkdir(parents=True)
    (sb / "config").mkdir()
    for f in ["common.py", "log_trade.py", "daily_check.py"]:
        shutil.copy(ROOT / "journal" / f, sb / "journal" / f)
    shutil.copy(ROOT / "config" / "universe.txt", sb / "config" / "universe.txt")
    (sb / "data").symlink_to(data_dir)
    (sb / "journal" / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    return sb


def run_cli(sandbox, script, args):
    r = subprocess.run([sys.executable, f"journal/{script}"] + args,
                       cwd=sandbox, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[오류] {script} 실패:", r.stderr)
        sys.exit(2)
    return r.stdout


# ---------------- 시나리오 6: 기본 리플레이 (log_trade CLI) ----------------

def cli_scenario(scratch, data_dir, px, dates):
    sandbox = make_sandbox(scratch, data_dir, "sandbox_replay", CFG)
    i0 = 700
    seq = []

    def buy(i, one):
        d = dates[i]
        p = round(px[d], 2)
        sh = round(one / (p * (1 + CFG["fee_pct"] + CFG["slippage_pct"])), 4)
        new = not seq or seq[-1][2]  # 직전이 사이클 종료 행이면 새 사이클
        args = ["--action", "buy", "--shares", str(sh), "--price", str(p), "--date", d]
        if new:
            args.append("--new-cycle")
        seq.append((args, {"date": d, "action": "buy", "shares": sh, "price": p,
                           "fee": None, "refClose": px[d]}, False))
        return sh

    one1 = round(CFG["total_capital"] * CFG["deploy_frac"] / CFG["divisions"], 2)
    # 사이클 1: 매수 3회 → 익절
    shares = buy(i0, one1) + buy(i0 + 1, one1) + buy(i0 + 2, one1)
    d_tp = dates[i0 + 3]
    p_tp = round(3 * one1 * 1.17 / shares, 2)
    seq.append((["--action", "take_profit", "--shares", str(round(shares, 4)),
                 "--price", str(p_tp), "--date", d_tp],
                {"date": d_tp, "action": "take_profit", "shares": round(shares, 4),
                 "price": p_tp, "fee": None, "refClose": px[d_tp]}, True))
    # 사이클 2: 매수 2회 → 쿼터손절 4일 (미소진 청산 = 위반 케이스 — 양쪽이 같게 판정하는지 본다)
    shares2 = buy(i0 + 5, one1) + buy(i0 + 6, one1)
    per = round(shares2 / CFG["quarter_days"], 6)
    for k in range(CFG["quarter_days"]):
        d = dates[i0 + 7 + k]
        seq.append((["--action", "quarter", "--shares", str(per),
                     "--price", str(round(px[d], 2)), "--date", d],
                    {"date": d, "action": "quarter", "shares": per, "price": round(px[d], 2),
                     "fee": None, "refClose": px[d]}, k == CFG["quarter_days"] - 1))

    for args, _, _ in seq:
        run_cli(sandbox, "log_trade.py", args)
    state = json.loads((sandbox / "journal" / "state.json").read_text(encoding="utf-8"))
    with open(sandbox / "journal" / "trades.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [t for _, t, _ in seq], state, rows


# ---------------- 시나리오 7: 리저브 (deploy_frac 0.67 + 폭락) ----------------

def reserve_scenario(scratch, data_dir, px, dates):
    cfg67 = dict(CFG)
    cfg67["deploy_frac"] = 0.67
    sandbox = make_sandbox(scratch, data_dir, "sandbox_reserve", cfg67)
    i0 = 460  # 합성 QQQ 하락구간(500~620) 직전부터 매수해 낙폭을 만든다
    one = round(cfg67["total_capital"] * cfg67["deploy_frac"] / cfg67["divisions"], 2)
    seq = []
    for k in range(30):
        d = dates[i0 + k]
        p = round(px[d], 2)
        sh = round(one / (p * (1 + cfg67["fee_pct"] + cfg67["slippage_pct"])), 4)
        args = ["--action", "buy", "--shares", str(sh), "--price", str(p), "--date", d]
        if k == 0:
            args.append("--new-cycle")
        run_cli(sandbox, "log_trade.py", args)
        seq.append({"date": d, "action": "buy", "shares": sh, "price": p,
                    "fee": None, "refClose": px[d]})
    # 발동일을 먼저 수학으로 찾는다 (CLI 와 동일한 dd = equity/peak - 1 판정).
    # 미발동 시도를 기록에 섞지 않는 이유: CLI 는 미발동 deploy 를 no-op 처리하지만
    # 시트는 '기록=진실'로 이체를 반영하는 의도된 차이가 있어 상태 비교가 어긋난다.
    # (미발동 '판정'의 일치는 아래 별도 네거티브 케이스로 검사)
    st_mid = json.loads((sandbox / "journal" / "state.json").read_text(encoding="utf-8"))
    fired_date = None
    for idx in range(500, 700, 5):
        d = dates[idx]
        eq = st_mid["reserve"] + st_mid["cash"] + st_mid["shares"] * px[d]
        if st_mid["peak_equity"] and eq / st_mid["peak_equity"] - 1 <= cfg67["reserve_triggers"][0]:
            fired_date = d
            break
    if fired_date:
        run_cli(sandbox, "log_trade.py", ["--action", "deploy_reserve", "--date", fired_date])
        seq.append({"date": fired_date, "action": "deploy_reserve", "shares": 0, "price": 0,
                    "fee": None, "refClose": px[fired_date]})
    state = json.loads((sandbox / "journal" / "state.json").read_text(encoding="utf-8"))
    with open(sandbox / "journal" / "trades.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return cfg67, seq, state, rows, fired_date


def reserve_negative_case(scratch, data_dir, cfg67, dates, px):
    """낙폭 없는 상태의 deploy_reserve — CLI 는 미발동(compliant=False)·상태 불변.
    JS 준수 판정(리저브발동조건)도 False 인지 비교한다. (상태 전이는 의도된 차이라 비교 제외)"""
    sandbox = make_sandbox(scratch, data_dir, "sandbox_rsvneg", cfg67)
    d = dates[450]
    run_cli(sandbox, "log_trade.py", ["--action", "deploy_reserve", "--date", d])
    with open(sandbox / "journal" / "trades.csv", newline="", encoding="utf-8") as f:
        row = list(csv.DictReader(f))[-1]
    st = json.loads((sandbox / "journal" / "state.json").read_text(encoding="utf-8"))
    return {"date": d, "cli_compliant": row["compliant"] == "True",
            "cli_reserve_intact": abs(st["reserve"] - round(cfg67["total_capital"] * (1 - cfg67["deploy_frac"]), 2)) < 0.02,
            "trade": {"date": d, "action": "deploy_reserve", "shares": 0, "price": 0,
                      "fee": None, "refClose": px[d]}}


# ---------------- 시나리오 8: 오늘 할 일 (daily_check CLI) ----------------

def make_state(cfg, **kw):
    """common.default_state 기반 파이썬 상태 + camelCase 미러 생성."""
    st = C.default_state(cfg)
    st.update(kw)
    camel = {
        "cash": st["cash"], "reserve": st["reserve"], "shares": st["shares"],
        "invested": st["invested"], "buysDone": st["buys_done"], "oneBuy": st["one_buy"],
        "cycleActive": st["cycle_active"], "cycleStart": st["cycle_start"],
        "cycleProceeds": st["cycle_proceeds"], "cycleSeq": st["cycle_seq"],
        "currentCycleId": st["current_cycle_id"], "liquidating": st["liquidating"],
        "liqLeft": st["liq_left"], "liqPerDay": st["liq_per_day"],
        "peakEquity": st["peak_equity"], "reserveTiersFired": st["reserve_tiers_fired"],
        "cyclesClosed": st["cycles_closed"],
    }
    return st, camel


def parse_daily_check(out):
    """daily_check.py stdout → {code, oneBuy, shares, perDay, left, friday_next5, why}"""
    r = {"code": None, "oneBuy": None, "shares": None, "perDay": None, "left": None,
         "friday_next5": None, "why": None}
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("🎯 익절!"):
            r["code"] = "TAKE_PROFIT"
            m = re.search(r"\(([\d.]+)주\)", s)
            if m:
                r["shares"] = float(m.group(1))
        elif s.startswith("🔻 쿼터손절"):
            r["code"] = "QUARTER"
            m = re.search(r"오늘 ([\d.]+)주 매도.*남은 (\d+)일", s)
            if m:
                r["perDay"], r["left"] = float(m.group(1)), int(m.group(2))
        elif s.startswith("🟩 새 사이클"):
            r["code"] = "NEW_CYCLE"
            m = re.search(r"1회분 \$([\d,.]+)", s)
            if m:
                r["oneBuy"] = parse_money(m.group(1))
        elif s.startswith("🟩 정규 매수"):
            r["code"] = "BUY"
            m = re.search(r"1회분 \$([\d,.]+)", s)
            if m:
                r["oneBuy"] = parse_money(m.group(1))
        elif s.startswith("⏸"):
            r["code"] = "WAIT"
            m = re.search(r"대기 — (.+?)\.", s)
            if m:
                r["why"] = m.group(1)
        elif s.startswith("📅 금요일"):
            m = re.search(r"SGOV \$([\d,.]+)어치", s)
            if m:
                r["friday_next5"] = parse_money(m.group(1))
    return r


def prescribe_cases(scratch, data_dir, qqq, dates):
    """daily_check.py 를 여러 상태·기준일로 실행해 (파이썬 결과, JS 입력) 쌍을 만든다."""
    sandbox = make_sandbox(scratch, data_dir, "sandbox_daily", CFG)
    sig = C.signal_series(qqq, CFG)
    tqqq = C.load_price("TQQQ")

    def canary_ok(d):
        return C.canary_status(d)["ok"]

    # 대표 기준일 찾기 (충분한 이력이 쌓인 400일 이후에서)
    d_gate_on = d_trend_off = d_canary_block = d_friday = None
    for i in range(400, len(dates)):
        d = dates[i]
        eok = bool(sig["entry_ok"].iloc[i])
        if d_gate_on is None and eok and canary_ok(d):
            d_gate_on = d
        if d_trend_off is None and not eok:
            d_trend_off = d
        if d_canary_block is None and eok and not canary_ok(d):
            d_canary_block = d
        if d_friday is None and pd.Timestamp(d).weekday() == 4:
            d_friday = d
        if all([d_gate_on, d_trend_off, d_canary_block, d_friday]):
            break
    d_any = dates[900]

    def px_at(d):
        return float(tqqq.loc[:pd.Timestamp(d)].iloc[-1])

    cases = []
    # 1) 대기 — 추세 미충족 (금요일 파킹 금액도 이 케이스로 검사)
    if d_trend_off:
        cases.append(("WAIT-추세", d_trend_off, *make_state(CFG)))
    # 2) 대기 — 카나리아 차단
    if d_canary_block:
        cases.append(("WAIT-카나리아", d_canary_block, *make_state(CFG)))
    # 3) 새 사이클
    if d_gate_on:
        cases.append(("NEW_CYCLE", d_gate_on, *make_state(CFG)))
    # 4) 정규 매수 (사이클 중, 익절 미달)
    p = px_at(d_any)
    inv = 6 * 1000.0
    sh_mid = round(inv / (p * 1.10), 4)  # 평가손익 ≈ -9% (익절 미달)
    cases.append(("BUY", d_any, *make_state(
        CFG, cash=34000.0, cycle_active=True, cycle_start=dates[880], cycle_seq=1,
        current_cycle_id=1, one_buy=1000.0, buys_done=6, invested=inv, shares=sh_mid)))
    # 5) 익절 도달
    sh_tp = round(inv * 1.20 / (p * C.sell_cost(CFG)), 4)  # 평가액 = 투입 × 1.20
    cases.append(("TAKE_PROFIT", d_any, *make_state(
        CFG, cash=34000.0, cycle_active=True, cycle_start=dates[880], cycle_seq=1,
        current_cycle_id=1, one_buy=1000.0, buys_done=6, invested=inv, shares=sh_tp)))
    # 6) 쿼터손절 (40회분 소진)
    cases.append(("QUARTER", d_any, *make_state(
        CFG, cash=100.0, cycle_active=True, cycle_start=dates[850], cycle_seq=1,
        current_cycle_id=1, one_buy=1000.0, buys_done=40, invested=40000.0, shares=sh_mid)))
    # 7) 금요일 파킹 (대기 상태에서)
    if d_friday:
        cases.append(("FRIDAY", d_friday, *make_state(CFG)))

    py_results, js_cases, labels = [], [], []
    for label, asof, pyst, camel in cases:
        (sandbox / "journal" / "state.json").write_text(json.dumps(pyst), encoding="utf-8")
        out = run_cli(sandbox, "daily_check.py", ["--asof", asof])
        py_results.append(parse_daily_check(out))
        js_cases.append({"asof": asof, "state": camel})
        labels.append(label)
    missing = [n for n, d in [("게이트ON", d_gate_on), ("추세OFF", d_trend_off),
                              ("카나리아차단", d_canary_block), ("금요일", d_friday)] if d is None]
    if missing:
        FAILS.append(f"처방 | 대표 기준일 못 찾음: {', '.join(missing)} (커버리지 부족)")
    return labels, py_results, js_cases


# ---------------- 메인 ----------------

def main():
    scratch = Path(tempfile.mkdtemp(prefix="parity_"))
    data_dir = build_data_dir(scratch)
    C.DATA_DIR = data_dir  # 파이썬 원본도 같은(임시) 데이터로 판정하게 주입
    universe = load_universe()

    qqq = C.load_price(CFG["signal_ticker"])
    tqqq = C.load_price(CFG["trade_ticker"])
    all_dates = [d.strftime("%Y-%m-%d") for d in qqq.index]
    n = len(all_dates)
    px = {d.strftime("%Y-%m-%d"): float(v) for d, v in zip(tqqq.index, tqqq.values)}

    asof_grid = all_dates[10::10]
    sat_grid = all_dates[300::15]
    months_grid = [m + "-15" for m in sorted({d[:7] for d in all_dates})[3:]]

    trades, py_state, py_rows = cli_scenario(scratch, data_dir, px, all_dates)
    cfg67, rsv_trades, rsv_state, rsv_rows, rsv_fired = reserve_scenario(scratch, data_dir, px, all_dates)
    rsv_neg = reserve_negative_case(scratch, data_dir, cfg67, all_dates, px)
    rx_labels, rx_py, rx_js_cases = prescribe_cases(scratch, data_dir, qqq, all_dates)

    payload = {
        "cfg": CFG,
        "universe": universe,
        "csv": {t: str(data_dir / f"{t}.csv") for t in PARITY_TICKERS},
        "asofGrid": asof_grid,
        "monthsGrid": months_grid,
        "satGrid": sat_grid,
        "trades": trades,
        "reserve": {"cfg": cfg67, "trades": rsv_trades},
        "reserveNegative": {"cfg": cfg67, "trade": rsv_neg["trade"]},
        "prescribeCases": rx_js_cases,
    }
    payload_path = scratch / "payload.json"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    res = subprocess.run(["node", str(HERE / "run_signals.mjs"), str(payload_path)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print("[오류] Node 실행 실패:\n" + res.stderr)
        sys.exit(2)
    js = json.loads(res.stdout)

    # ---- 1. 추세 시리즈 전 구간 ----
    sig = C.signal_series(qqq, CFG)
    jt = js["trend"]
    check("추세", "행 수", n, len(jt["dates"]))
    for i in range(min(n, len(jt["dates"]))):
        check("추세", f"{all_dates[i]} 날짜", all_dates[i], jt["dates"][i])
        check("추세", f"{all_dates[i]} MA", nan_none(sig["ma"].iloc[i]), jt["ma"][i], abs_tol=1e-7)
        check("추세", f"{all_dates[i]} above", bool(sig["above"].iloc[i]), jt["above"][i])
        check("추세", f"{all_dates[i]} rising", bool(sig["rising"].iloc[i]), jt["rising"][i])
        check("추세", f"{all_dates[i]} ok", bool(sig["ok_today"].iloc[i]), jt["ok"][i])
        check("추세", f"{all_dates[i]} entry_ok", bool(sig["entry_ok"].iloc[i]), jt["entryOk"][i])
    report("1. 추세 필터 전 구간", n)

    # ---- 2. latest_signal ----
    check("latest", "표본 수", len(asof_grid), len(js["latest"]))
    for row in js["latest"]:
        ls = C.latest_signal(qqq, CFG, asof=row["asof"])
        check("latest", f"{row['asof']} date", ls["date"].strftime("%Y-%m-%d"), row["date"])
        check("latest", f"{row['asof']} close", ls["close"], row["close"])
        check("latest", f"{row['asof']} ma", nan_none(ls["ma"]), row["ma"], abs_tol=1e-7)
        check("latest", f"{row['asof']} above", ls["above"], row["above"])
        check("latest", f"{row['asof']} rising", ls["rising"], row["rising"])
        check("latest", f"{row['asof']} streak", ls["streak"], row["streak"])
        check("latest", f"{row['asof']} entry_ok", ls["entry_ok"], row["entryOk"])
    report("2. latest_signal 격자", len(js["latest"]))

    # ---- 3. 카나리아 ----
    check("카나리아", "표본 수", len(months_grid), len(js["canary"]))
    for row in js["canary"]:
        pc = C.canary_status(row["asof"])
        check("카나리아", f"{row['asof']} ok", pc["ok"], row["ok"])
        check("카나리아", f"{row['asof']} cutoff", str(pc["cutoff"]), row["cutoff"])
        check("카나리아", f"{row['asof']} 자산 수", len(pc["assets"]), len(row["assets"]))
        for pa, ja in zip(pc["assets"], row["assets"]):
            check("카나리아", f"{row['asof']} {pa['ticker']} mom", nan_none(pa["mom"]), ja["mom"])
            check("카나리아", f"{row['asof']} {pa['ticker']} date",
                  str(pa["date"]) if pa["date"] else None, ja["date"])
            check("카나리아", f"{row['asof']} {pa['ticker']} missing", pa["missing"], ja["missing"])
    report("3. 카나리아 월별", len(js["canary"]))

    # ---- 4. 위성 ----
    check("위성", "표본 수", len(sat_grid), len(js["satellite"]))
    for row in js["satellite"]:
        ps = C.satellite_status(CFG, asof=row["asof"])
        check("위성", f"{row['asof']} target", ps["target"], row["target"])
        check("위성", f"{row['asof']} 자산 수", len(ps["assets"]), len(row["assets"]))
        for pa, ja in zip(ps["assets"], row["assets"]):
            check("위성", f"{row['asof']} {pa['ticker']} on", pa["on"], ja["on"])
            check("위성", f"{row['asof']} {pa['ticker']} streak", pa["streak"], ja["streak"])
            check("위성", f"{row['asof']} {pa['ticker']} mom", nan_none(pa["mom"]), ja["mom"])
    report("4. 위성 엔진A 격자", len(js["satellite"]))

    # ---- 5. 달력 ----
    for i, row in enumerate(js["calendar"]):
        d = qqq.index[i]
        check("달력", f"{row['date']} 월말", C.is_month_end(d.date()), row["monthEnd"])
        check("달력", f"{row['date']} 요일", d.weekday(), row["weekday"])
    report("5. 달력(월말·요일) 전 구간", len(js["calendar"]))

    # ---- 6. 리플레이 (CLI state.json vs JS replayCore) ----
    compare_state("리플레이", py_state, js["replay"])
    compare_row_checks("행별체크", py_rows, js["rowChecks"])
    report("6. 리플레이 상태 + 행별 체크", len(py_rows))

    # ---- 7. 리저브 ----
    if rsv_fired is None:
        FAILS.append("리저브 | 폭락 시나리오에서 발동 케이스를 만들지 못함 (커버리지 부족)")
    compare_state("리저브", rsv_state, js["reserve"]["state"])
    compare_row_checks("리저브체크", rsv_rows, js["reserve"]["rowChecks"])
    # 미발동 케이스: 판정(False)은 양쪽 일치해야 하고, CLI 상태는 불변이어야 한다
    check("리저브", "미발동 CLI 판정", False, rsv_neg["cli_compliant"])
    check("리저브", "미발동 CLI 리저브 불변", True, rsv_neg["cli_reserve_intact"])
    check("리저브", "미발동 JS 판정", False,
          js["reserveNegative"].get("리저브발동조건(고점대비)"))
    report(f"7. 리저브 (발동일 {rsv_fired} + 미발동 케이스)", len(rsv_rows) + 1)

    # ---- 8. 오늘 할 일 처방 + 파킹 ----
    check("처방", "표본 수", len(rx_py), len(js.get("prescribe", [])))
    for label, pyr, jsr in zip(rx_labels, rx_py, js["prescribe"]):
        code = "WAIT" if label == "FRIDAY" else label.split("-")[0]
        check("처방", f"{label} py코드", pyr["code"], code)  # 파서가 의도한 상태를 실제로 만들었는지
        check("처방", f"{label} 코드", pyr["code"], jsr["code"])
        if pyr["oneBuy"] is not None:
            money("처방", f"{label} 1회분", pyr["oneBuy"], jsr["oneBuy"], abs_tol=0.02)
        if pyr["shares"] is not None:
            check("처방", f"{label} 전량주수", pyr["shares"], jsr["shares"], rel=0, abs_tol=1e-4)
        if pyr["perDay"] is not None:
            check("처방", f"{label} 쿼터주수", pyr["perDay"], jsr["perDay"], rel=0, abs_tol=1e-4)
            check("처방", f"{label} 남은일", pyr["left"], jsr["left"])
        if pyr["why"] is not None:
            check("처방", f"{label} 대기사유 포함", True, pyr["why"] in jsr["title"])
        if pyr["friday_next5"] is not None:
            check("처방", f"{label} 금요일 플래그", True, jsr["parking"]["isFriday"])
            money("처방", f"{label} 금요일 SGOV 5회분", pyr["friday_next5"],
                  jsr["parking"]["next5"], abs_tol=0.02)
    report("8. 오늘 할 일(daily_check) + 파킹", len(rx_py))

    shutil.rmtree(scratch, ignore_errors=True)
    print("\n" + "=" * 60)
    if FAILS:
        print(f"❌ 패리티 감사 실패 — 불일치 {len(FAILS)}건 (위 상세 참조)")
        sys.exit(1)
    print("✅ 패리티 감사 통과 — 시트(JS) 로직이 파이썬 원본과 일치")
    sys.exit(0)


def compare_state(section, py_state, js_state):
    """상태 필드 비교 — 금액은 절대 오차(상대 오차는 회계 불일치를 감춘다)."""
    m = [("cash", "cash", 0.10), ("reserve", "reserve", 0.10), ("shares", "shares", 1e-6),
         ("invested", "invested", 0.10), ("one_buy", "oneBuy", 0.05),
         ("liq_per_day", "liqPerDay", 1e-6), ("peak_equity", "peakEquity", 0.10)]
    for pk, jk, tol in m:
        money(section, pk, py_state[pk], js_state[jk], abs_tol=tol)
    for pk, jk in [("buys_done", "buysDone"), ("cycle_active", "cycleActive"),
                   ("cycle_seq", "cycleSeq"), ("liquidating", "liquidating"),
                   ("liq_left", "liqLeft")]:
        check(section, pk, py_state[pk], js_state[jk])
    check(section, "리저브 발동 티어", py_state["reserve_tiers_fired"], js_state["reserveTiersFired"])
    check(section, "종료사이클 수", len(py_state["cycles_closed"]), len(js_state["cyclesClosed"]))
    for pc, jc in zip(py_state["cycles_closed"], js_state["cyclesClosed"]):
        check(section, f"사이클#{pc['id']} pnl", pc["pnl_pct"], jc["pnlPct"], rel=0, abs_tol=1e-3)
        check(section, f"사이클#{pc['id']} reason", pc["reason"], jc["reason"])
        money(section, f"사이클#{pc['id']} invested", pc["invested"], jc["invested"], abs_tol=0.10)


def compare_row_checks(section, py_rows, js_rows):
    """행별 준수 체크 비교 — 파이썬 체크 키가 JS 에 없으면(키 소실) 실패로 판정."""
    check(section, "행 수", len(py_rows), len(js_rows))
    n_checked = 0
    for prow, jrow in zip(py_rows, js_rows):
        pych = dict(kv.rsplit("=", 1) for kv in prow["checks"].split(";") if kv)
        for k, v in pych.items():
            if k not in jrow["checks"]:
                FAILS.append(f"{section} | {prow['date']} {prow['action']} 체크 키 소실: {k}")
                continue
            check(section, f"{prow['date']} {prow['action']} {k}", v == "True", jrow["checks"][k])
            n_checked += 1
    if py_rows and n_checked == 0:
        FAILS.append(f"{section} | 비교된 체크가 0건 — 감사가 공허함")


if __name__ == "__main__":
    main()
