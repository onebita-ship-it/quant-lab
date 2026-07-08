/**
 * main.gs — 구글 시트 일일 기록·판단 시스템 (Apps Script 본체).
 *
 * signals.gs(룰북 §0 계산 로직)와 같은 프로젝트에 나란히 붙여넣으면 전역으로 연결된다.
 * 설치·운용 방법: sheets/README.md
 *
 * 구성 탭:
 *   ⚙️설정     — 파라미터(key/value) + 유니버스 표 (config/universe.txt 대응)
 *   📊오늘     — 아침 브리핑 대시보드 (daily_check.py 출력의 시트판)
 *   📅일일기록 — 하루 1행 자동 누적 (판단 근거 스냅샷 — 자동 기록)
 *   ✍️매매기록 — 체결 수기 입력 → 규칙 준수·슬리피지 자동 대조 (log_trade.py 대응)
 *   📈월간요약 — 준수율·추적오차·성과 (monthly_report.py 대응)
 *   _시세      — 시세 캐시(숨김, Yahoo 실패 시 폴백)
 *
 * ⚠️ 이 도구는 기록·점검용이며 자동매매가 아니다. 주문·체결은 본인이 수행한다.
 */

var SHEET_CONFIG = '⚙️설정';
var SHEET_TODAY = '📊오늘';
var SHEET_DAILY = '📅일일기록';
var SHEET_TRADES = '✍️매매기록';
var SHEET_MONTHLY = '📈월간요약';
var SHEET_CACHE = '_시세';

var CONFIG_ROWS = [
  // [key, 기본값, 설명]
  ['total_capital', 40000, '코어 슬리브 자본(USD) — 계좌 전체가 아니라 코어 몫(42.5%)만!'],
  ['deploy_frac', 1.0, '전략 투입 비율 (1.0=100% / 0.67=67%+리저브, 룰북 ⑤)'],
  ['divisions', 40, '분할 횟수 (룰북 ②)'],
  ['take_profit_pct', 0.15, '익절 기준 +15% (룰북 ③)'],
  ['fee_pct', 0.0007, '수수료 편도 0.07%'],
  ['slippage_pct', 0.0005, '슬리피지 가정 편도 0.05%'],
  ['ma_window', 200, '이동평균 기간 (룰북 ①)'],
  ['slope_lookback', 20, '200일선 상승 판정 기간 (룰북 ①)'],
  ['confirm_days', 5, '연속 충족 스트릭 (룰북 ①)'],
  ['quarter_days', 4, '쿼터손절 분할일수 (룰북 ④)'],
  ['reserve_triggers', '-0.30,-0.50', '리저브 발동선 (룰북 ⑤, 쉼표 구분)'],
  ['signal_ticker', 'QQQ', '신호 판정 지수'],
  ['trade_ticker', 'TQQQ', '매매 대상'],
  ['email_to', '', '아침 브리핑 이메일 (빈칸이면 미발송)'],
  ['trigger_hour', 7, '자동 실행 시각 (프로젝트 시간대 기준 시, 권장 6~9 KST)'],
];

var UNIVERSE_DEFAULT = [ // config/universe.txt 와 동일하게 유지할 것 (분기 리뷰 재량)
  ['TQQQ', 'core', 'QQQ'],
  ['UPRO', 'core', 'SPY'],
  ['SOXL', 'satellite', 'SOXX'],
];

var DAILY_HEADER = [
  '날짜', 'QQQ종가', '200MA', '종가>MA', 'MA상승', '스트릭', '추세OK',
  '카나리아SPY', '카나리아EFA', '카나리아EEM', '카나리아AGG', '카나리아OK',
  '진입게이트', 'TQQQ종가', '사이클#', '회차', '평단', '평가손익%',
  '처방', '오늘할일', '위성타깃', '위성보유', '금요일SGOV매도$', '월말', '코어총자산$', '기록시각',
];

var TRADES_HEADER_USER = ['날짜', '슬리브', '구분', '티커', '수량', '체결가', '수수료$(선택)', '메모'];
var TRADES_HEADER_AUTO = ['기준종가', '슬리피지bp', '준수', '체크상세', '사이클#', '자동판정메모'];

var SLEEVES = ['코어', '위성', '금', '파킹'];
var ACTIONS = ['buy', 'sell', 'take_profit', 'quarter', 'deploy_reserve'];

// ============================== 메뉴 ==============================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('퀀트랩')
    .addItem('📊 오늘 브리핑 갱신', 'menuRefreshToday')
    .addItem('📅 일일기록 추가/갱신', 'menuAppendDaily')
    .addItem('✍️ 매매기록 검증', 'menuValidateTrades')
    .addItem('📈 월간요약 갱신', 'menuMonthly')
    .addSeparator()
    .addItem('⏰ 매일 자동실행 설치', 'installDailyTrigger')
    .addItem('⏰ 자동실행 해제', 'removeDailyTriggers')
    .addSeparator()
    .addItem('🧰 시트 초기 설정(최초 1회)', 'setup')
    .addToUi();
}

function menuRefreshToday() { var t = computeToday(); renderDashboard(t); toast('오늘 브리핑 갱신 완료 (기준일 ' + t.asof + ')'); }
function menuAppendDaily() { var t = computeToday(); appendDailyRow(t); renderDashboard(t); toast('일일기록 기입 완료 (' + t.asof + ')'); }
function menuValidateTrades() { var n = validateTrades(); toast('매매기록 ' + n + '행 검증 완료'); }
function menuMonthly() { monthlyReport(); toast('월간요약 갱신 완료'); }

function toast(msg) { SpreadsheetApp.getActiveSpreadsheet().toast(msg, '퀀트랩'); }

// ============================== 초기 설정 ==============================

function setup() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();

  // ⚙️설정
  var cfgSh = getOrCreate(ss, SHEET_CONFIG);
  if (cfgSh.getLastRow() < 2) {
    cfgSh.getRange(1, 1, 1, 3).setValues([['키', '값', '설명']]).setFontWeight('bold');
    cfgSh.getRange(2, 1, CONFIG_ROWS.length, 3).setValues(CONFIG_ROWS);
    cfgSh.getRange(1, 5, 1, 3).setValues([['티커', '구분', '원지수']]).setFontWeight('bold');
    cfgSh.getRange(2, 5, UNIVERSE_DEFAULT.length, 3).setValues(UNIVERSE_DEFAULT);
    cfgSh.getRange('I1').setValue('유니버스는 config/universe.txt 와 동일하게 분기 리뷰에서만 변경');
    cfgSh.setColumnWidths(1, 1, 130).setColumnWidths(2, 1, 110).setColumnWidths(3, 1, 420);
  }

  // 📅일일기록
  var dailySh = getOrCreate(ss, SHEET_DAILY);
  if (dailySh.getLastRow() < 1 || dailySh.getRange(1, 1).getValue() === '') {
    dailySh.getRange(1, 1, 1, DAILY_HEADER.length).setValues([DAILY_HEADER]).setFontWeight('bold');
    dailySh.setFrozenRows(1);
  }

  // ✍️매매기록
  var trSh = getOrCreate(ss, SHEET_TRADES);
  if (trSh.getLastRow() < 1 || trSh.getRange(1, 1).getValue() === '') {
    var header = TRADES_HEADER_USER.concat(TRADES_HEADER_AUTO);
    trSh.getRange(1, 1, 1, header.length).setValues([header]).setFontWeight('bold');
    trSh.setFrozenRows(1);
    trSh.getRange(1, TRADES_HEADER_USER.length + 1, 1, TRADES_HEADER_AUTO.length)
      .setBackground('#efefef')
      .setNote('회색 열은 스크립트가 채운다 (메뉴: ✍️ 매매기록 검증)');
    var maxRows = 1000;
    trSh.getRange(2, 2, maxRows, 1).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(SLEEVES, true).setAllowInvalid(false).build());
    trSh.getRange(2, 3, maxRows, 1).setDataValidation(
      SpreadsheetApp.newDataValidation().requireValueInList(ACTIONS, true).setAllowInvalid(false).build());
    trSh.getRange(2, 1, maxRows, 1).setNumberFormat('yyyy-mm-dd');
  }

  getOrCreate(ss, SHEET_TODAY);
  getOrCreate(ss, SHEET_MONTHLY);
  var cacheSh = getOrCreate(ss, SHEET_CACHE);
  cacheSh.hideSheet();

  toast('초기 설정 완료. ⚙️설정에서 total_capital 을 본인 코어 자본으로 바꾼 뒤 "오늘 브리핑 갱신"을 실행하세요.');
}

function getOrCreate(ss, name) {
  return ss.getSheetByName(name) || ss.insertSheet(name);
}

// ============================== 설정 읽기 ==============================

function readConfig() {
  var sh = mustSheet(SHEET_CONFIG);
  var vals = sh.getRange(2, 1, Math.max(sh.getLastRow() - 1, 1), 2).getValues();
  var cfg = {};
  for (var k in RULEBOOK_DEFAULTS) cfg[k] = RULEBOOK_DEFAULTS[k];
  for (var i = 0; i < vals.length; i++) {
    var key = String(vals[i][0]).trim();
    if (!key) continue;
    var v = vals[i][1];
    if (key === 'reserve_triggers') {
      cfg[key] = String(v).split(',').map(function (x) { return parseFloat(x); })
        .filter(function (x) { return !isNaN(x); });
    } else if (key === 'signal_ticker' || key === 'trade_ticker' || key === 'email_to') {
      cfg[key] = String(v).trim();
    } else if (v !== '' && v !== null) {
      cfg[key] = Number(v);
    }
  }
  return cfg;
}

function readUniverse() {
  var sh = mustSheet(SHEET_CONFIG);
  var vals = sh.getRange(2, 5, Math.max(sh.getLastRow() - 1, 1), 3).getValues();
  var rows = [];
  for (var i = 0; i < vals.length; i++) {
    var t = String(vals[i][0]).trim();
    if (!t) continue;
    rows.push({ ticker: t, cls: String(vals[i][1]).trim(), index: String(vals[i][2]).trim() });
  }
  return rows.length ? rows : UNIVERSE_DEFAULT.map(function (r) { return { ticker: r[0], cls: r[1], index: r[2] }; });
}

function mustSheet(name) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(name);
  if (!sh) throw new Error('탭 "' + name + '" 이 없습니다. 메뉴 [퀀트랩 → 시트 초기 설정]을 먼저 실행하세요.');
  return sh;
}

// ============================== 시세 (Yahoo Finance) ==============================

/** Yahoo v8 chart API 로 일봉 종가(배당 조정 = yfinance auto_adjust 와 동일) 로드.
 * 10년치를 받아 과거 매매기록 재검증(200MA 판정에 기준일 이전 ~220거래일 필요)이 깨지지 않게 한다. */
function fetchYahoo(ticker) {
  var url = 'https://query1.finance.yahoo.com/v8/finance/chart/' +
    encodeURIComponent(ticker) + '?range=10y&interval=1d&events=div%2Csplit';
  var resp = UrlFetchApp.fetch(url, { muteHttpExceptions: true, headers: { 'User-Agent': 'Mozilla/5.0' } });
  if (resp.getResponseCode() !== 200) throw new Error(ticker + ' HTTP ' + resp.getResponseCode());
  var data = JSON.parse(resp.getContentText());
  var res = data.chart && data.chart.result && data.chart.result[0];
  if (!res || !res.timestamp) throw new Error(ticker + ' 응답에 데이터 없음');
  var ts = res.timestamp;
  var gmtoff = res.meta.gmtoffset || 0;
  var quote = res.indicators.quote[0].close;
  var adj = res.indicators.adjclose && res.indicators.adjclose[0].adjclose;
  var closes = adj || quote;
  var dates = [], out = [];
  for (var i = 0; i < ts.length; i++) {
    var c = closes[i];
    if (c === null || c === undefined) continue;
    var d = new Date((ts[i] + gmtoff) * 1000);
    dates.push(d.toISOString().slice(0, 10));
    out.push(Number(c));
  }
  return { dates: dates, closes: out };
}

/** 필요한 전 티커 시세 로드 (Yahoo → 실패 시 _시세 캐시 폴백) + 캐시 갱신. */
function fetchAllSeries(cfg, extraTickers) {
  var universe = readUniverse();
  var need = {};
  need[cfg.signal_ticker] = true;
  need[cfg.trade_ticker] = true;
  CANARY_TICKERS.forEach(function (t) { need[t] = true; });
  universe.forEach(function (u) { need[u.ticker] = true; need[u.index] = true; });
  (extraTickers || []).forEach(function (t) { if (t) need[t] = true; });

  var cache = readCache();
  var seriesByTicker = {}, failed = [];
  Object.keys(need).forEach(function (t) {
    try {
      seriesByTicker[t] = fetchYahoo(t);
      Utilities.sleep(150); // Yahoo 예의상 간격
    } catch (e) {
      if (cache[t] && cache[t].dates.length) {
        seriesByTicker[t] = cache[t];
        failed.push(t + '(캐시 사용)');
      } else {
        seriesByTicker[t] = { dates: [], closes: [] };
        failed.push(t + '(데이터 없음!)');
      }
    }
  });
  writeCache(seriesByTicker);
  return { seriesByTicker: seriesByTicker, failed: failed, universe: universe };
}

function readCache() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_CACHE);
  var out = {};
  if (!sh || sh.getLastRow() < 2) return out;
  var vals = sh.getRange(2, 1, sh.getLastRow() - 1, 3).getValues();
  for (var i = 0; i < vals.length; i++) {
    var t = String(vals[i][0]);
    if (!out[t]) out[t] = { dates: [], closes: [] };
    out[t].dates.push(toDateStr(vals[i][1])); // Date 자동변환 셀도 'YYYY-MM-DD' 로 복원
    out[t].closes.push(Number(vals[i][2]));
  }
  return out;
}

/** 새로 받은 시세를 기존 캐시와 병합해 저장 — 과거 이력을 보존한다(common.load_price 의 병합과 동일 취지). */
function writeCache(seriesByTicker) {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(SHEET_CACHE);
  if (!sh) return;
  var old = readCache();
  var merged = {};
  Object.keys(old).forEach(function (t) { merged[t] = old[t]; });
  Object.keys(seriesByTicker).forEach(function (t) {
    var s = seriesByTicker[t];
    if (!s || !s.dates.length) return; // 이번에 실패한 티커는 기존 캐시 유지
    var byDate = {};
    (merged[t] ? merged[t].dates : []).forEach(function (d, i) { byDate[d] = merged[t].closes[i]; });
    s.dates.forEach(function (d, i) { byDate[d] = s.closes[i]; }); // 새 값 우선
    var dates = Object.keys(byDate).sort();
    merged[t] = { dates: dates, closes: dates.map(function (d) { return byDate[d]; }) };
  });
  var rows = [['티커', '날짜', '종가']];
  Object.keys(merged).forEach(function (t) {
    for (var i = 0; i < merged[t].dates.length; i++) {
      rows.push([t, merged[t].dates[i], merged[t].closes[i]]);
    }
  });
  sh.clearContents();
  sh.getRange(1, 2, Math.max(rows.length, 1), 1).setNumberFormat('@'); // 날짜 열은 텍스트로 고정
  sh.getRange(1, 1, rows.length, 3).setValues(rows);
}

// ============================== 매매기록 읽기 ==============================

/** ✍️매매기록 전 행 파싱 (시트 순서 보존). */
function readTrades() {
  var sh = mustSheet(SHEET_TRADES);
  var last = sh.getLastRow();
  if (last < 2) return [];
  var vals = sh.getRange(2, 1, last - 1, TRADES_HEADER_USER.length).getValues();
  var rows = [];
  for (var i = 0; i < vals.length; i++) {
    var v = vals[i];
    if (!v[0] || !v[2]) continue; // 날짜·구분 없는 행은 무시
    rows.push({
      rowIndex: i + 2,
      date: toDateStr(v[0]),
      sleeve: String(v[1]).trim(),
      action: String(v[2]).trim(),
      ticker: String(v[3]).trim().toUpperCase(),
      shares: Number(v[4]) || 0,
      price: Number(v[5]) || 0,
      fee: v[6] === '' || v[6] === null ? null : Number(v[6]),
      memo: String(v[7] || ''),
    });
  }
  // 날짜 오름차순 안정 정렬 (같은 날은 입력 순서 유지 — V8 sort 는 stable)
  rows.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : 0; });
  return rows;
}

function toDateStr(v) {
  if (v instanceof Date) {
    return Utilities.formatDate(v, SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone(), 'yyyy-MM-dd');
  }
  return String(v).slice(0, 10);
}

function coreTrades(trades) {
  return trades.filter(function (t) { return t.sleeve === '코어'; });
}

/** 체결일의 매매티커 기준종가를 refClose 로 붙인다 (peakEquity 갱신용 — log_trade.ref_close 대응).
 *  시세 이력이 그 날짜를 못 덮으면 체결가로 폴백한다. */
function withRefClose(t, seriesByTicker, cfg) {
  var s = seriesByTicker[t.ticker && seriesByTicker[t.ticker] && seriesByTicker[t.ticker].dates.length
    ? t.ticker : cfg.trade_ticker];
  var rc = null;
  if (s && s.dates.length) {
    var sl = sliceUpTo(s, t.date);
    if (sl.closes.length) rc = sl.closes[sl.closes.length - 1];
  }
  if (rc === null && t.price) rc = t.price;
  return { date: t.date, action: t.action, shares: t.shares, price: t.price, fee: t.fee, refClose: rc };
}

/** 스프레드시트 시간대 기준 '오늘' 요일 (0=월 … 6=일). */
function todayWeekdayKst() {
  var tz = SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone();
  return (Number(Utilities.formatDate(new Date(), tz, 'u')) + 6) % 7; // u: 1=월 … 7=일
}

/** 슬리브별 순보유 (buy/sell·take_profit·quarter 반영). */
function netHoldings(trades, sleeve) {
  var net = {};
  trades.forEach(function (t) {
    if (t.sleeve !== sleeve || !t.ticker) return;
    if (t.action === 'buy') net[t.ticker] = (net[t.ticker] || 0) + t.shares;
    else if (t.action === 'sell' || t.action === 'take_profit' || t.action === 'quarter') {
      net[t.ticker] = (net[t.ticker] || 0) - t.shares;
    }
  });
  var out = {};
  Object.keys(net).forEach(function (k) { if (net[k] > 1e-9) out[k] = net[k]; });
  return out;
}

// ============================== 오늘 판단 (daily_check 대응) ==============================

/** 시세·상태·신호를 모두 계산해 하나의 컨텍스트로 반환. */
function computeToday() {
  var cfg = readConfig();
  var trades = readTrades();
  var tradeTickers = trades.map(function (t) { return t.ticker; });
  var fetched = fetchAllSeries(cfg, tradeTickers);
  var S = fetched.seriesByTicker;

  var qqq = S[cfg.signal_ticker];
  var tqqq = S[cfg.trade_ticker];
  if (!qqq.dates.length || !tqqq.dates.length) {
    throw new Error('시세 로드 실패: ' + fetched.failed.join(', '));
  }
  var sig = latestSignal(qqq, cfg);
  var asof = sig.date;
  var canary = canaryStatus(S, asof);
  var entryOk = sig.entryOk && canary.ok; // 룰북 ① = 추세 AND 카나리아

  // 리플레이에 기준종가를 공급해 peakEquity(리저브 낙폭 기준 고점)가 log_trade 와 동일하게 갱신되게 한다
  var st = replayCore(coreTrades(trades).map(function (t) {
    return withRefClose(t, S, cfg);
  }), cfg);
  var price = tqqq.closes[tqqq.closes.length - 1];
  var priceDate = tqqq.dates[tqqq.dates.length - 1];

  var rx = prescribe(st, cfg, entryOk, sig.entryOk, canary.ok, price);
  // 금요일 판정은 '오늘(KST)' 기준 — 금요일 아침에 알림을 받아 그날 밤(미국 금요일 장)에
  // SGOV 를 매도할 수 있게 한다. (마지막 봉 요일 기준이면 알림이 토요일에 와서 늦다)
  var park = parkingInfo(st, cfg, todayWeekdayKst());
  var sat = satelliteStatus(fetched.universe, S, cfg, asof);
  var satHold = Object.keys(netHoldings(trades, '위성'));

  var avg = st.shares ? st.invested / st.shares : 0;
  var val = st.shares * price * sellCost(cfg);
  var gain = st.invested ? val / st.invested - 1 : 0;

  return {
    cfg: cfg, seriesByTicker: S, universe: fetched.universe, failed: fetched.failed,
    trades: trades, state: st,
    asof: asof, sig: sig, canary: canary, entryOk: entryOk,
    price: price, priceDate: priceDate,
    prescription: rx, parking: park, satellite: sat, satelliteHolding: satHold,
    avg: avg, gain: gain,
    monthEnd: isMonthEnd(priceDate),
    equity: equityOf(st, price),
  };
}

// ============================== 📊오늘 대시보드 ==============================

function renderDashboard(t) {
  var sh = mustSheet(SHEET_TODAY);
  var cfg = t.cfg, st = t.state;
  var ck = function (b) { return b ? '✅' : '❌'; };
  var rows = [];
  var push = function (a, b, c) { rows.push([a || '', b === undefined ? '' : b, c || '']); };

  push('🌅 매매일지 아침 브리핑', '기준일 ' + t.asof, '갱신 ' + nowStr());
  if (t.failed.length) push('⚠️ 시세 경고', t.failed.join(', '), 'Yahoo 실패 시 캐시 폴백');
  push();
  push('— 추세 필터 (' + cfg.signal_ticker + ' 200일선, 룰북 ①) —');
  push(cfg.signal_ticker + ' 종가', round2(t.sig.close), '200일선 ' + round2(t.sig.ma));
  push(ck(t.sig.above) + ' 종가 > 200일선');
  push(ck(t.sig.rising) + ' 200일선 상승 (' + cfg.slope_lookback + '일 전 대비)');
  push(ck(t.sig.streak >= cfg.confirm_days) + ' 연속 충족', t.sig.streak + '/' + cfg.confirm_days + '일');
  push();
  push('— 13612W 카나리아 (룰북 ① v10, 판정 월말 ' + t.canary.cutoff + ' · 이번 달 유지) —');
  t.canary.assets.forEach(function (a) {
    if (a.missing) push('❌ ' + a.ticker, '데이터 없음', '시세 확인 필요');
    else if (a.mom === null) push('⚠️ ' + a.ticker, '이력 253일 미만', '판정 제외');
    else push(ck(a.mom >= 0) + ' ' + a.ticker, (a.mom >= 0 ? '+' : '') + a.mom.toFixed(3), '기준 ' + a.date);
  });
  push('→ 카나리아', t.canary.ok ? '통과 ✅' : '차단 ❌', '하나라도 음수면 신규 진입 금지');
  push();
  push('▶ 신규 진입 게이트 (추세 AND 카나리아)', t.entryOk ? '충족 ✅' : '미충족 ❌');
  push();
  push('— 코어 포트폴리오 상태 (' + cfg.trade_ticker + ' 종가 ' + round2(t.price) + ', ' + t.priceDate + ') —');
  push('운용현금', usd(st.cash), '리저브 ' + usd(st.reserve));
  if (st.cycleActive) {
    push('사이클 #' + st.currentCycleId, st.buysDone + '/' + cfg.divisions + '회차',
      '시작 ' + st.cycleStart);
    push('보유 ' + st.shares.toFixed(4) + '주', '평단(비용포함) ' + round2(t.avg),
      '평가손익 ' + pct(t.gain));
    var tp = cfg.take_profit_pct;
    if (t.gain >= tp) push('🎯 익절 도달!', '목표 +' + Math.round(tp * 100) + '%');
    else {
      var needUp = (1 + tp) / (1 + t.gain) - 1;
      push('익절(+' + Math.round(tp * 100) + '%)까지', ((tp - t.gain) * 100).toFixed(1) + '%p 남음',
        '가격 약 +' + (needUp * 100).toFixed(1) + '% 더 오르면 익절');
    }
  } else {
    push('사이클', '없음 (대기)', '');
  }
  push();
  push('— 오늘 할 일 —');
  push(t.prescription.title, '', t.prescription.detail);
  push();
  push('— 파킹 SGOV (룰북 ⑦) —');
  if (t.parking.holdSgov) push('미투입 현금 ' + usd(st.cash) + ' + 리저브 ' + usd(st.reserve), '전액 SGOV 파킹 유지');
  if (t.parking.isFriday) {
    push('📅 오늘은 금요일(KST)', '오늘 밤 미국 장에서 SGOV ' + usd(t.parking.next5) + ' 매도',
      '다음 주 5회분 예수금 확보 (1회분 ' + usd(t.parking.oneBuy) + ' × 5) — 매일 매도 금지');
  } else {
    push('오늘은 SGOV 매도일 아님', '', '금요일(KST) 아침에 알림 → 그날 밤 미국 장에서 다음 주 5회분만 매도');
  }
  push();
  push('— 위성(엔진A) 신호 (룰북 ⑧, 계좌의 42.5%) —');
  t.satellite.assets
    .slice()
    .sort(function (a, b) { return (b.mom === null ? -1e9 : b.mom) - (a.mom === null ? -1e9 : a.mom); })
    .forEach(function (r) {
      if (r.missing) push('❌ ' + r.ticker, '데이터 없음 (원지수 ' + r.index + ')');
      else push(ck(!!r.on) + ' ' + r.ticker,
        '원지수 ' + r.index + ' 추세 ' + (r.on ? 'ON' : 'OFF') + ' (스트릭 ' + r.streak + '/' + t.cfg.confirm_days + ')',
        '3·6모멘텀 ' + (r.mom === null ? 'n/a' : (r.mom >= 0 ? '+' : '') + r.mom.toFixed(3)));
    });
  push('→ 오늘 타깃', t.satellite.target, t.satellite.target === 'SGOV' ? '추세 ON 자산 없음 → 전량 SGOV' : '');
  var hold = t.satelliteHolding.length ? t.satelliteHolding.join(', ') : '(매매기록상 위성 보유 없음)';
  var mismatch = t.satelliteHolding.length && t.satelliteHolding.indexOf(t.satellite.target) < 0;
  push('현재 위성 보유(기록 기준)', hold, mismatch ? '⚠️ 타깃과 불일치 — 신호 OFF면 즉시 교체' : '');
  if (t.monthEnd) push('📅 오늘은 월말(근사)', '위성 리밸런스일', '종가에 타깃과 보유 일치시킬 것');
  else push('월중 규칙', '보유 자산 신호 OFF → 즉시 타깃 교체', '그 외 교체는 월말에');
  if (st.reserve > 1e-6) {
    push();
    var dd = st.peakEquity ? t.equity / st.peakEquity - 1 : 0;
    push('— 리저브 —', '총자산 ' + usd(t.equity) + ' · 고점대비 ' + pct(dd),
      '발동선 ' + cfg.reserve_triggers.join('/') + ' (기발동 ' + (st.reserveTiersFired.join(',') || '없음') + ')');
  }
  push();
  push('※ 이 시트는 기록·점검용 — 자동매매 아님. 룰북 원문: result_final.md §0');

  sh.clearContents();
  sh.getRange(1, 1, rows.length, 3).setValues(rows);
  sh.getRange(1, 1, 1, 3).setFontWeight('bold');
  sh.setColumnWidths(1, 1, 330).setColumnWidths(2, 1, 260).setColumnWidths(3, 1, 380);
}

// ============================== 📅일일기록 ==============================

/** 하루 1행 기록 — 같은 날짜가 이미 있으면 그 행을 갱신(멱등). */
function appendDailyRow(t) {
  var sh = mustSheet(SHEET_DAILY);
  var st = t.state;
  var mark = function (b) { return b ? '✓' : '✗'; };
  var canaryVals = {};
  t.canary.assets.forEach(function (a) { canaryVals[a.ticker] = a.mom; });
  var row = [
    t.asof, round2(t.sig.close), round2(t.sig.ma),
    mark(t.sig.above), mark(t.sig.rising), t.sig.streak, mark(t.sig.entryOk),
    numOr(canaryVals.SPY), numOr(canaryVals.EFA), numOr(canaryVals.EEM), numOr(canaryVals.AGG),
    mark(t.canary.ok), mark(t.entryOk), round2(t.price),
    st.cycleActive ? st.currentCycleId : '', st.cycleActive ? st.buysDone + '/' + t.cfg.divisions : '',
    st.cycleActive ? round2(t.avg) : '', st.cycleActive ? Math.round(t.gain * 1000) / 10 : '',
    t.prescription.code, t.prescription.title,
    t.satellite.target, t.satelliteHolding.join(',') || '-',
    t.parking.isFriday ? round2(t.parking.next5) : '',
    t.monthEnd ? '월말' : '', round2(t.equity), nowStr(),
  ];
  var last = sh.getLastRow();
  var targetRow = last + 1;
  if (last >= 2) {
    var dates = sh.getRange(2, 1, last - 1, 1).getDisplayValues();
    for (var i = 0; i < dates.length; i++) {
      if (String(dates[i][0]).slice(0, 10) === t.asof) { targetRow = i + 2; break; }
    }
  }
  sh.getRange(targetRow, 1).setNumberFormat('@'); // 날짜를 문자열 그대로 유지 (기입 전에 설정)
  sh.getRange(targetRow, 1, 1, row.length).setValues([row]);
}

function numOr(v) { return v === null || v === undefined ? '' : Math.round(v * 1000) / 1000; }

// ============================== ✍️매매기록 검증 (log_trade 대응) ==============================

/**
 * 매매기록 전 행을 리플레이하며 규칙 준수·슬리피지 검증 열을 채운다.
 * 코어 슬리브: log_trade.py 와 동일 검사(+§0 ①의 카나리아 검사 추가).
 * 위성 슬리브: 매수 티커가 당일 타깃과 일치하는지 검사.
 * 금/파킹: 기록만 (검사 없음).
 */
function validateTrades() {
  var cfg = readConfig();
  var trades = readTrades();
  if (!trades.length) return 0;
  var fetched = fetchAllSeries(cfg, trades.map(function (t) { return t.ticker; }));
  var S = fetched.seriesByTicker;
  var qqq = S[cfg.signal_ticker];

  var st = defaultState(cfg);
  var results = {}; // rowIndex → [기준종가, 슬리피지bp, 준수, 체크상세, 사이클#, 메모]

  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var refSeries = S[t.ticker && S[t.ticker] && S[t.ticker].dates.length ? t.ticker : cfg.trade_ticker];
    var refClose = null;
    if (refSeries && refSeries.dates.length) {
      var sl = sliceUpTo(refSeries, t.date);
      if (sl.closes.length) refClose = sl.closes[sl.closes.length - 1];
    }
    var slipBps = (refClose && t.price) ? (t.price - refClose) / refClose * 1e4 : null;

    if (t.sleeve === '코어') {
      var isNew = t.action === 'buy' && !st.cycleActive;
      var entrySig = null, canaryOk = null;
      if (isNew) {
        entrySig = latestSignal(qqq, cfg, t.date);
        canaryOk = canaryStatus(S, t.date).ok;
      }
      // 시세 이력이 판정에 부족하면 ❌(가짜 위반)/✅(가짜 통과) 대신 '판정 불가'로 표시
      var noHistory = refClose === null || (isNew && entrySig.ma === null);
      var chk = checkTradeRow(t, {
        stBefore: st, cfg: cfg, refClose: refClose, entrySig: entrySig, canaryOk: canaryOk, isNewCycle: isNew,
      });
      // 상태 전이 (리플레이 — signals.gs 의 applyTrade). 기준종가가 없으면 체결가로 폴백해 고점 추적 유지.
      applyTrade(st, { date: t.date, action: t.action, shares: t.shares, price: t.price, fee: t.fee,
        refClose: refClose !== null ? refClose : (t.price || null) }, cfg);
      var compliant = Object.keys(chk.checks).every(function (k) { return chk.checks[k]; });
      var notes = chk.notes.slice();
      if (slipBps !== null && Math.abs(slipBps) > 50) notes.push('⚠️ 슬리피지 과다 ' + slipBps.toFixed(0) + 'bp');
      results[t.rowIndex] = noHistory ? [
        '', '', '⚠️', '',
        st.currentCycleId || '',
        '시세 이력이 이 날짜를 덮지 않음 — 판정 불가 (시세 캐시 확인)',
      ] : [
        refClose === null ? '' : round2(refClose),
        slipBps === null ? '' : Math.round(slipBps * 10) / 10,
        compliant ? '✅' : '❌',
        Object.keys(chk.checks).map(function (k) { return k + '=' + (chk.checks[k] ? '✓' : '✗'); }).join('; '),
        st.currentCycleId || '',
        notes.join(' / '),
      ];
    } else if (t.sleeve === '위성' && t.action === 'buy') {
      var target = satelliteStatus(fetched.universe, S, cfg, t.date).target;
      var okBuy = t.ticker === target;
      results[t.rowIndex] = [
        refClose === null ? '' : round2(refClose),
        slipBps === null ? '' : Math.round(slipBps * 10) / 10,
        okBuy ? '✅' : '❌',
        '위성타깃일치=' + (okBuy ? '✓' : '✗') + ' (당일 타깃 ' + target + ')',
        '', okBuy ? '' : '⚠️ 당일 타깃(' + target + ')과 다른 자산 매수',
      ];
    } else {
      results[t.rowIndex] = [
        refClose === null ? '' : round2(refClose),
        slipBps === null ? '' : Math.round(slipBps * 10) / 10,
        '—', '', '', '기록만 (규칙 검사 대상 아님)',
      ];
    }
  }

  var sh = mustSheet(SHEET_TRADES);
  var col = TRADES_HEADER_USER.length + 1;
  Object.keys(results).forEach(function (r) {
    sh.getRange(Number(r), col, 1, TRADES_HEADER_AUTO.length).setValues([results[r]]);
  });
  return trades.length;
}

// ============================== 📈월간요약 (monthly_report 대응) ==============================

function monthlyReport() {
  var cfg = readConfig();
  var trades = coreTrades(readTrades());
  var sh = mustSheet(SHEET_MONTHLY);
  sh.clearContents();
  var header = ['월', '체결수', '매수', '매도', '준수', '위반', '준수율',
    '평균슬리피지bp', '드래그$', '가정예산$', '초과드리프트$',
    '종료사이클', '승률', '실현손익$'];
  var rows = [header];

  if (!trades.length) {
    rows.push(['(코어 매매기록 없음)', '', '', '', '', '', '', '', '', '', '', '', '', '']);
    sh.getRange(1, 1, rows.length, header.length).setValues(padRows(rows, header.length));
    return;
  }

  // 검증 열을 다시 계산하지 않고, 전체 리플레이로 월별 지표 산출
  var fetched = fetchAllSeries(cfg, trades.map(function (t) { return t.ticker; }));
  var S = fetched.seriesByTicker;
  var qqq = S[cfg.signal_ticker];

  var months = {};
  var st = defaultState(cfg);
  for (var i = 0; i < trades.length; i++) {
    var t = trades[i];
    var m = t.date.slice(0, 7);
    if (!months[m]) {
      months[m] = { n: 0, buys: 0, sells: 0, ok: 0, judged: 0, slips: [], drag: 0, assumed: 0, closed: [], realized: 0 };
    }
    var mo = months[m];
    var refSeries = S[t.ticker && S[t.ticker] && S[t.ticker].dates.length ? t.ticker : cfg.trade_ticker];
    var refClose = null;
    if (refSeries && refSeries.dates.length) {
      var sl = sliceUpTo(refSeries, t.date);
      if (sl.closes.length) refClose = sl.closes[sl.closes.length - 1];
    }
    var isNew = t.action === 'buy' && !st.cycleActive;
    var entrySig = isNew ? latestSignal(qqq, cfg, t.date) : null;
    var noHistory = refClose === null || (isNew && entrySig.ma === null);
    var chk = checkTradeRow(t, {
      stBefore: st, cfg: cfg, refClose: refClose,
      entrySig: entrySig,
      canaryOk: isNew ? canaryStatus(S, t.date).ok : null,
      isNewCycle: isNew,
    });
    var closedBefore = st.cyclesClosed.length;
    applyTrade(st, {
      date: t.date, action: t.action, shares: t.shares, price: t.price, fee: t.fee,
      refClose: refClose !== null ? refClose : (t.price || null),
    }, cfg);
    mo.n++;
    if (t.action === 'buy') mo.buys++;
    if (t.action === 'take_profit' || t.action === 'quarter') mo.sells++;
    if (!noHistory) { // 시세 이력이 없는 행은 준수율 분모에서 제외 (가짜 위반/통과 방지)
      mo.judged++;
      if (Object.keys(chk.checks).every(function (k) { return chk.checks[k]; })) mo.ok++;
    }
    if (refClose && t.shares && t.price) {
      mo.slips.push((t.price - refClose) / refClose * 1e4);
      if (t.action === 'buy') mo.drag += t.shares * (t.price - refClose);
      else if (t.action === 'take_profit' || t.action === 'quarter') mo.drag += t.shares * (refClose - t.price);
      mo.assumed += t.shares * refClose * cfg.slippage_pct;
    }
    if (st.cyclesClosed.length > closedBefore) {
      var c = st.cyclesClosed[st.cyclesClosed.length - 1];
      mo.closed.push(c);
      mo.realized += c.invested * c.pnlPct;
    }
  }

  Object.keys(months).sort().forEach(function (m) {
    var mo = months[m];
    var avgSlip = mo.slips.length ? mo.slips.reduce(function (a, b) { return a + b; }, 0) / mo.slips.length : 0;
    var wins = mo.closed.filter(function (c) { return c.pnlPct > 0; }).length;
    rows.push([
      m, mo.n, mo.buys, mo.sells, mo.ok, mo.judged - mo.ok,
      mo.judged ? Math.round(mo.ok / mo.judged * 100) + '%' : '판정불가',
      Math.round(avgSlip * 10) / 10, round2(mo.drag), round2(mo.assumed), round2(mo.drag - mo.assumed),
      mo.closed.length, mo.closed.length ? Math.round(wins / mo.closed.length * 100) + '%' : '—',
      round2(mo.realized),
    ]);
  });

  rows.push([]);
  var tq = S[cfg.trade_ticker];
  var lastPrice = tq.closes[tq.closes.length - 1];
  var eq = equityOf(st, lastPrice);
  rows.push(['현재 스냅샷 (' + cfg.trade_ticker + ' ' + round2(lastPrice) + ')',
    '총자산 ' + usd(eq), '현금 ' + usd(st.cash), '보유 ' + st.shares.toFixed(4) + '주',
    '리저브 ' + usd(st.reserve),
    '누적수익률 ' + pct(eq / cfg.total_capital - 1) + ' (초기 ' + usd(cfg.total_capital) + ')',
    '', '', '', '', '', '', '', '']);
  sh.getRange(1, 1, rows.length, header.length).setValues(padRows(rows, header.length));
  sh.getRange(1, 1, 1, header.length).setFontWeight('bold');
}

function padRows(rows, width) {
  return rows.map(function (r) {
    var o = r.slice();
    while (o.length < width) o.push('');
    return o;
  });
}

// ============================== 자동 실행 (트리거) + 이메일 ==============================

/** 매일 아침 자동 실행: 시세 → 일일기록 → 대시보드 → (새 거래일일 때만) 이메일. */
function dailyJob() {
  var t = computeToday();
  var sh = mustSheet(SHEET_DAILY);
  var isNewDay = true;
  var last = sh.getLastRow();
  if (last >= 2) {
    var dates = sh.getRange(2, 1, last - 1, 1).getDisplayValues();
    for (var i = 0; i < dates.length; i++) {
      if (String(dates[i][0]).slice(0, 10) === t.asof) { isNewDay = false; break; }
    }
  }
  appendDailyRow(t);
  renderDashboard(t);
  if (isNewDay && t.cfg.email_to) sendBriefingEmail(t);
}

function installDailyTrigger() {
  removeDailyTriggers();
  var cfg = readConfig();
  var hour = Math.max(0, Math.min(23, Math.round(cfg.trigger_hour || 7)));
  ScriptApp.newTrigger('dailyJob').timeBased().everyDays(1).atHour(hour).create();
  toast('매일 ' + hour + '시(프로젝트 시간대) 자동실행 설치 완료. 프로젝트 시간대가 Asia/Seoul 인지 확인하세요.');
}

function removeDailyTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (tr) {
    if (tr.getHandlerFunction() === 'dailyJob') ScriptApp.deleteTrigger(tr);
  });
}

function sendBriefingEmail(t) {
  var lines = [
    '기준일 ' + t.asof + ' — 퀀트랩 아침 브리핑',
    '',
  ];
  if (t.failed.length) {
    lines.push('⚠️ 시세 경고: ' + t.failed.join(', ') + ' — 일부 판단이 캐시(과거) 시세 기준일 수 있음. 시트에서 확인 후 행동할 것.', '');
  }
  lines = lines.concat([
    '▶ 오늘 할 일: ' + t.prescription.title,
    (t.prescription.detail ? '   ' + t.prescription.detail : ''),
    '',
    '진입 게이트: ' + (t.entryOk ? '충족 ✅' : '미충족 ❌') +
    ' (추세 ' + (t.sig.entryOk ? 'OK' : 'NO') + ' / 카나리아 ' + (t.canary.ok ? 'OK' : '차단') + ')',
    '위성 타깃: ' + t.satellite.target +
    (t.satelliteHolding.length && t.satelliteHolding.indexOf(t.satellite.target) < 0
      ? ' ⚠️ 보유(' + t.satelliteHolding.join(',') + ')와 불일치' : ''),
  ]);
  if (t.parking.isFriday) lines.push('📅 금요일(KST): 오늘 밤 미국 장에서 SGOV ' + usd(t.parking.next5) + ' 매도 (다음 주 5회분)');
  if (t.monthEnd) lines.push('📅 월말: 위성 리밸런스 + 월간요약 확인');
  lines.push('', '시트에서 상세 확인 → ' + SpreadsheetApp.getActiveSpreadsheet().getUrl());
  MailApp.sendEmail({
    to: t.cfg.email_to,
    subject: '[퀀트랩 ' + t.asof + '] ' + t.prescription.title.slice(0, 80),
    body: lines.join('\n'),
  });
}

// ============================== 표시 유틸 ==============================

function usd(x) {
  var v = Number(x);
  var s = Math.abs(v).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return (v < 0 ? '-$' : '$') + s;
}
function pct(x) { return (x >= 0 ? '+' : '') + (x * 100).toFixed(1) + '%'; }
function nowStr() {
  return Utilities.formatDate(new Date(), SpreadsheetApp.getActiveSpreadsheet().getSpreadsheetTimeZone(), 'yyyy-MM-dd HH:mm');
}
