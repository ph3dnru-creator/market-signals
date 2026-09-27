#!/usr/bin/env python3
"""Проверяет рынок и отправляет в Telegram только изменения сигналов."""

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from signals import evaluate, format_event, format_status

TOKEN = os.environ.get("TG_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE = Path(os.environ.get("STATE_PATH", str(Path(__file__).parent / "state.json")))
CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (market-signals)"}
MSK = ZoneInfo("Europe/Moscow")


def http_get(url, tries=3):
    last = None
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt < tries - 1:
                time.sleep(5 * (attempt + 1))
    raise last


def yahoo(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=1d&range=1y" % symbol
    data = json.loads(http_get(url))
    result = data["chart"]["result"][0]
    price = float(result["meta"]["regularMarketPrice"])
    closes = [float(x) for x in result["indicators"]["quote"][0]["close"] if x is not None]
    peak = max(closes + [price])
    return price, (price / peak - 1) * 100


def get_crypto():
    url = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=bitcoin,ethereum&vs_currencies=usd")
    try:
        data = json.loads(http_get(url))
        return float(data["bitcoin"]["usd"]), float(data["ethereum"]["usd"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        btc, _ = yahoo("BTC-USD")
        eth, _ = yahoo("ETH-USD")
        return btc, eth


def cbr_usd():
    root = ET.fromstring(http_get("https://www.cbr.ru/scripts/XML_daily.asp").decode("windows-1251"))
    for currency in root.findall("Valute"):
        if currency.findtext("CharCode") == "USD":
            value = float(currency.findtext("Value").replace(",", "."))
            nominal = float(currency.findtext("Nominal").replace(",", "."))
            raw_date = root.get("Date")
            quoted_at = datetime.strptime(raw_date.replace(".", "/"), "%d/%m/%Y").date()
            if (datetime.now(MSK).date() - quoted_at).days > 7:
                raise ValueError("Курс ЦБ устарел: %s" % quoted_at)
            return value / nominal, root.get("Date")
    raise ValueError("USD not found in CBR XML")


def fetch_quotes():
    btc, eth = get_crypto()
    usd, usd_date = cbr_usd()
    try:
        jpy, _ = yahoo("JPY=X")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        data = json.loads(http_get("https://api.frankfurter.app/latest?from=USD&to=JPY"))
        jpy = float(data["rates"]["JPY"])
    try:
        ndx, drawdown = yahoo("%5EIXIC")
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError):
        ndx = drawdown = None
    return {"btc": btc, "eth": eth, "usd": usd, "usd_date": usd_date,
            "jpy": jpy, "ndx": ndx, "ndx_drawdown": drawdown}


def send(message):
    if not TOKEN or not CHAT_ID:
        raise RuntimeError("TG_TOKEN и TG_CHAT_ID обязательны для отправки")
    url = "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    body = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": message}).encode()
    request = urllib.request.Request(url, data=body, headers=UA)
    try:
        with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
            data = json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        # HTTPError содержит полный URL с токеном бота; не выводим его в логи.
        code = getattr(error, "code", "network")
        raise RuntimeError("Telegram send failed (%s)" % code) from None
    if not data.get("ok"):
        raise RuntimeError("Telegram rejected message")


def load_state():
    if not STATE.exists() or STATE.stat().st_size == 0:
        return {"version": 2, "statuses": {}, "error_alerted": False}
    data = json.loads(STATE.read_text(encoding="utf-8"))
    if data.get("version") != 2:
        # Миграция старого fired/near1: первый новый прогон фиксирует базу без спама.
        return {"version": 2, "statuses": {}, "error_alerted": False}
    return data


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE.with_name(STATE.name + ".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, STATE)


def main():
    state = load_state()
    quotes = fetch_quotes()
    now = datetime.now(MSK)
    statuses, events = evaluate(quotes, state.get("statuses", {}))
    first_run = not state.get("statuses")
    if os.environ.get("SEND_STATUS") == "1":
        send(format_status(quotes, statuses, now))
    if not first_run:
        for event in events:
            send(format_event(event, now))
            # После отправки фиксируем переход по одному: повторный запуск
            # продолжит остальные события, не рассылая уже учтённые.
            state["statuses"][event["key"]] = event["status"]
            save_state(state)
    state["statuses"] = statuses
    state["last_success"] = now.isoformat()
    state["error_alerted"] = False
    save_state(state)
    print("OK: проверено %s; новых событий %d" % (now.isoformat(), 0 if first_run else len(events)))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("FAIL: %s" % error, file=sys.stderr)
        try:
            state = load_state()
            if not state.get("error_alerted") and TOKEN and CHAT_ID:
                send("⚠️ Капитал: мониторинг не смог проверить сигналы. Причина: %s" % error)
                state["error_alerted"] = True
                save_state(state)
        except Exception as alert_error:
            print("FAIL alert: %s" % alert_error, file=sys.stderr)
        # Railway в прошлом перезапускал контейнер после кода 1 и слал
        # повторные тревоги. Ошибка видна в логе, уведомление уже отправлено.
        sys.exit(0)
