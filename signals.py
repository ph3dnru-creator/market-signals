"""Чистая логика ценовых наблюдений для бота «Капитал».

Порог сам по себе не является поручением купить или продать актив.
Недельные закрытия, ETF, Strategy и BitMine здесь намеренно не оцениваются:
текущие источники не дают надёжной автоматической проверки их составных условий.
"""

import json
from pathlib import Path

ACTION_PLAN = Path(__file__).with_name("signal_actions.json")

RULES = (
    ("btc_down", "BTC ниже $57 735", "btc", lambda q: q["btc"] < 57735,
     "Проверить сценарий второй волны; решение о траншах — после сверки плана."),
    ("btc_reserve", "BTC в зоне $48–53 тыс.", "btc", lambda q: 48000 <= q["btc"] <= 53000,
     "Проверить резерв, ликвидность и ограничения до любого решения о покупке."),
    ("eth_pause", "ETH ниже $1 750", "eth", lambda q: q["eth"] < 1750,
     "Пересмотреть график ETH-траншей."),
    ("eth_reserve", "ETH в зоне $1 200–1 500", "eth", lambda q: 1200 <= q["eth"] <= 1500,
     "Проверить резерв, ликвидность и ограничения до любого решения о покупке."),
    ("ethbtc", "ETH/BTC выше 0,031", "ethbtc", lambda q: q["eth"] / q["btc"] > 0.031,
     "Пересмотреть долю ETH в крипто-ядре; автоматически не ускорять транши."),
    ("usd_low", "USD/RUB ниже 79", "usd", lambda q: q["usd"] < 79,
     "Пересмотреть график валютных траншей после проверки доступного маршрута."),
    ("usd_high", "USD/RUB выше 84", "usd", lambda q: q["usd"] > 84,
     "Проверить паузу валютных траншей; сверить с полной политикой из разбора 05.08."),
    ("jpy", "USD/JPY ниже 150", "jpy", lambda q: q["jpy"] < 150,
     "Проверить риск carry-trade; одного курса недостаточно для вывода о ликвидациях."),
    ("ndx", "Nasdaq ниже пика на 20%", "ndx_drawdown",
     lambda q: q["ndx_drawdown"] is not None and q["ndx_drawdown"] <= -20,
     "Пересмотреть стресс-сценарий и длительность крипто-траншей."),
)


def evaluate(quotes, previous):
    if quotes["btc"] <= 0 or quotes["eth"] <= 0 or quotes["usd"] <= 0 or quotes["jpy"] <= 0:
        raise ValueError("Некорректная цена в источнике")
    values = dict(quotes)
    values["ethbtc"] = quotes["eth"] / quotes["btc"]
    statuses = {}
    events = []
    for key, title, field, predicate, action in RULES:
        if field == "ndx_drawdown" and values[field] is None:
            statuses[key] = previous.get(key, "unknown")
            continue
        status = "active" if predicate(values) else "inactive"
        old = previous.get(key)
        if key == "ethbtc" and old in ("active", "inactive"):
            # Dead band: tiny fluctuations must not reverse a confirmed state.
            ratio = values[field]
            status = ("active" if ratio >= 0.0313 else "inactive" if ratio <= 0.0307 else old)
        statuses[key] = status
        if old is not None and old not in ("unknown", status):
            events.append({"key": key, "title": title, "field": field,
                           "value": values[field], "old": old, "status": status,
                           "action": action})
    return statuses, events


def _value(field, value):
    if field in ("btc", "eth"):
        return "$%s" % format(value, ",.0f").replace(",", " ")
    if field == "ethbtc":
        return "%.6f" % value
    if field == "ndx_drawdown":
        return "%.1f%% от пика" % value
    return "%.2f" % value


def format_event(event, now):
    """Only approved, quantified actions warrant a Telegram notification."""
    plan = json.loads(ACTION_PLAN.read_text(encoding="utf-8"))
    action = plan.get(event["key"], {}).get(event["status"])
    if not action:
        return None
    required = ("side", "asset", "quantity", "price", "reason")
    if any(not isinstance(action.get(k), str) or not action[k].strip() for k in required):
        raise ValueError("Incomplete approved signal action")
    if action["side"] not in ("Купить", "Продать", "Приостановить покупки"):
        raise ValueError("Invalid signal action")
    return (f"🔔 Капитал · {action['asset']}\n\n"
            f"Действие: {action['side']}\n"
            f"Объём: {action['quantity']}\n"
            f"Цена / условие: {action['price']}\n\n"
            f"Почему: {action['reason']}\n"
            f"Рынок: {_value(event['field'], event['value'])}\n"
            f"{now.strftime('%d.%m.%Y %H:%M')} МСК")


def format_status(quotes, statuses, now):
    active = [title for key, title, _, _, _ in RULES if statuses.get(key) == "active"]
    return ("📍 Капитал · %s МСК\nBTC %s · ETH %s\nUSD/RUB %.2f · USD/JPY %.2f\n"
            "Активные ценовые условия: %s\n"
            "Недельные закрытия, ETF и корпоративные события требуют отдельной проверки."
            % (now.strftime("%d.%m.%Y %H:%M"), _value("btc", quotes["btc"]),
               _value("eth", quotes["eth"]), quotes["usd"], quotes["jpy"],
               ", ".join(active) if active else "нет"))
