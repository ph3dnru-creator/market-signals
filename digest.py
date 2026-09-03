#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ежедневная сводка рынков в Telegram + сигналы по триггерам.
Запускается launchd в 09:45 МСК. Состояние (вчерашние статусы) — в state.json."""

import json, ssl, sys, urllib.request, urllib.parse, xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from pathlib import Path

import os

TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
if not TOKEN or not CHAT_ID:
    print("FAIL: не заданы переменные TG_TOKEN и TG_CHAT_ID", file=sys.stderr)
    sys.exit(1)

# На Railway состояние живёт на диске (volume), локально — рядом со скриптом
STATE = Path(os.environ.get("STATE_PATH", str(Path(__file__).parent / "state.json")))
STATE.parent.mkdir(parents=True, exist_ok=True)
CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (market-signals)"}

class Transient(Exception):
    """Временный сбой: лимит запросов, таймаут, сеть. Тревогу не поднимаем."""

def http_get(url, timeout=30, tries=4):
    import time
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout, context=CTX) as r:
                return r.read()
        except Exception as e:
            last = e
            code = getattr(e, "code", None)
            if i < tries - 1:
                time.sleep((20 if code == 429 else 5) * (i + 1))
    raise last

def http_post(url, data, timeout=30):
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=UA), timeout=timeout, context=CTX) as r:
        return r.read()

def yahoo(symbol):
    """Возвращает (цена, изменение за день в %, просадка от годового максимума в %)."""
    d = json.loads(http_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"))
    r = d["chart"]["result"][0]
    price = float(r["meta"]["regularMarketPrice"])
    closes = [c for c in r["indicators"]["quote"][0]["close"] if c]
    chg = (price / closes[-2] - 1) * 100 if len(closes) >= 2 else None
    peak = max(closes + [price])
    return price, chg, (price / peak - 1) * 100

def get_crypto():
    """CoinGecko основной; при лимите 429 — Yahoo, тем же путём, что Nasdaq."""
    try:
        d = json.loads(http_get("https://api.coingecko.com/api/v3/simple/price"
                                "?ids=bitcoin,ethereum&vs_currencies=usd&include_24hr_change=true"))
        return (float(d["bitcoin"]["usd"]), float(d["bitcoin"]["usd_24h_change"]),
                float(d["ethereum"]["usd"]), float(d["ethereum"]["usd_24h_change"]))
    except Exception:
        b, bc, _ = yahoo("BTC-USD")
        e, ec, _ = yahoo("ETH-USD")
        return b, bc, e, ec

def cbr_usd(day=None):
    url = "https://www.cbr.ru/scripts/XML_daily.asp" + (f"?date_req={day}" if day else "")
    root = ET.fromstring(http_get(url).decode("windows-1251"))
    for v in root.findall("Valute"):
        if v.findtext("CharCode") == "USD":
            val = float(v.findtext("Value").replace(",", ".")) / float(v.findtext("Nominal").replace(",", "."))
            return val, root.get("Date")
    raise RuntimeError("USD not found in CBR XML")

def get_usdrub():
    """Курс ЦБ и изменение к предыдущему рабочему дню."""
    today, dt = cbr_usd()
    for back in range(1, 6):
        prev, dp = cbr_usd((date.today() - timedelta(days=back)).strftime("%d/%m/%Y"))
        if dp != dt:
            return today, (today / prev - 1) * 100
    return today, None

def get_usdjpy():
    try:
        p, c, _ = yahoo("JPY=X")
        return p, c
    except Exception:
        d = json.loads(http_get("https://api.frankfurter.app/latest?from=USD&to=JPY"))
        return float(d["rates"]["JPY"]), None

def ru(n, dec=0):
    return f"{n:,.{dec}f}".replace(",", " ").replace(".", ",")

def pct(x, dec=1):
    return "н/д" if x is None else f"{'+' if x >= 0 else '−'}{abs(x):.{dec}f}%".replace(".", ",")

def send(text):
    resp = json.loads(http_post("https://api.telegram.org/bot%s/sendMessage" % TOKEN,
                                urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()))
    if not resp.get("ok"):
        raise RuntimeError("telegram: %s" % resp)

def already_sent_today():
    """Railway может перезапустить контейнер. Второй раз за день не шлём."""
    if os.environ.get("FORCE"):
        return False
    try:
        ts = json.loads(STATE.read_text()).get("ts", "")
        return ts[:10] == datetime.now().strftime("%Y-%m-%d")
    except Exception:
        return False

def main():
    if already_sent_today():
        print("сводка за сегодня уже отправлена — пропускаем")
        return
    btc, btc_d, eth, eth_d = get_crypto()
    usd, usd_d = get_usdrub()
    jpy, jpy_d = get_usdjpy()
    try:
        ndx, ndx_d, ndx_dd = yahoo("%5EIXIC")
    except Exception:
        ndx = ndx_d = ndx_dd = None
    ethbtc = eth / btc
    monday = datetime.now().weekday() == 0

    # (ключ, название, сработал, дистанция %, действие)
    trigs = [
        ("btc_rev",  "BTC разворот (нед. закрытие > $69 000)", monday and btc > 69000, (69000 - btc) / btc * 100,
         "ускорить оставшиеся крипто-транши"),
        ("btc_dn",   "BTC вторая волна (< $57 735)", btc < 57735, (57735 - btc) / btc * 100,
         "не ускоряться; резерв готовить к $48–53k"),
        ("btc_rez",  "BTC зона резерва ($48–53k)", 48000 <= btc <= 53000, (53000 - btc) / btc * 100,
         "докупка резервом"),
        ("eth_rev",  "ETH дно подтверждено (нед. закрытие > $2 036)", monday and eth > 2036, (2036 - eth) / eth * 100,
         "ускорить ETH-часть"),
        ("eth_dn",   "ETH пауза (< $1 750)", eth < 1750, (1750 - eth) / eth * 100,
         "пауза дискреционных докупок"),
        ("eth_rez",  "ETH зона резерва ($1 200–1 500)", 1200 <= eth <= 1500, (1500 - eth) / eth * 100,
         "докупка резервом"),
        ("ethbtc",   "ETH/BTC разворот (> 0,031)", ethbtc > 0.031, (0.031 - ethbtc) / ethbtc * 100,
         "разворот пары — можно наращивать долю ETH в ядре"),
        ("usd_lo",   "USD/RUB ускорить транши (< 79)", usd < 79, (79 - usd) / usd * 100,
         "ускорить валютные транши"),
        ("usd_hi",   "USD/RUB не догонять (> 84)", usd > 84, (84 - usd) / usd * 100,
         "не догонять, пауза валютных покупок"),
        ("jpy",      "Иена: сворачивание carry-trade (USD/JPY < 150)", jpy < 150, (150 - jpy) / jpy * 100,
         "отток ликвидности с рискованных активов — пауза дискреционных докупок крипты"),
    ]
    if ndx_dd is not None:
        trigs.append(("ndx", "Nasdaq −20% от пика", ndx_dd <= -20, ndx_dd + 20,
                      "рецессионный сценарий — растянуть крипто-транши до апреля 2027"))

    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {"fired": [], "near1": []}

    fired_now, near1_now, msgs = [], [], []
    for key, name, hit, dist, action in trigs:
        if hit:
            fired_now.append(key)
            if key not in state.get("fired", []):
                if key.startswith("usd"):   val = ru(usd, 2)
                elif key == "jpy":          val = ru(jpy, 2)
                elif key == "ndx":          val = f"{ru(ndx)} ({pct(ndx_dd)} от пика)"
                elif key == "ethbtc":       val = "0,%04d" % round(ethbtc * 10000)
                else:                       val = "$" + ru(btc if key.startswith("btc") else eth)
                msgs.append("🔴 СИГНАЛ: %s\nТекущее значение: %s\nДействие: %s" % (name, val, action))
        elif abs(dist) <= 1.0:
            near1_now.append(key)
            if key not in state.get("near1", []):
                msgs.append("🟡 БЛИЗКО: %s — до порога %s%%" % (name, ru(abs(dist), 1)))

    pending = sorted((abs(d), n) for k, n, h, d, a in trigs if not h)
    nearest = f"{pending[0][1]} ({ru(pending[0][0], 1)}%)" if pending else "—"
    line2 = ("Сигналов нет. Ближе всех: " + nearest) if not fired_now else \
            ("СРАБОТАЛИ: " + ", ".join(n for k, n, h, d, a in trigs if h))

    lines = ["📊 " + datetime.now().strftime("%d.%m"),
             f"BTC ${ru(btc)} ({pct(btc_d)})",
             f"ETH ${ru(eth)} ({pct(eth_d)})",
             f"USD {ru(usd, 2)} ({pct(usd_d)})",
             f"Иена {ru(jpy, 1)} ({pct(jpy_d)})"]
    if ndx is not None:
        lines.append(f"Nasdaq {ru(ndx)} ({pct(ndx_d)}, {pct(ndx_dd)} от пика)")
    lines.append(line2)

    send("\n".join(lines))
    for m in msgs:
        send(m)

    STATE.write_text(json.dumps({"fired": fired_now, "near1": near1_now,
                                 "ts": datetime.now().isoformat()}, ensure_ascii=False))
    print("OK: сводка отправлена, дополнительных сообщений:", len(msgs))

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        transient = isinstance(e, (Transient, TimeoutError)) or \
                    getattr(e, "code", None) in (429, 500, 502, 503, 504) or \
                    isinstance(e, (urllib.error.URLError, OSError))
        if not transient:
            try:
                send("⚠️ Сводка не собралась: %s" % e)
            except Exception:
                pass
        print("FAIL%s: %s" % (" (временный)" if transient else "", e), file=sys.stderr)
        # выходим нулём: ненулевой код заставляет Railway перезапускать контейнер по кругу
        sys.exit(0)
