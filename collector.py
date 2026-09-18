#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отдаёт свежие посты Telegram-каналов простым текстом.

Зачем: облачный агент-разборщик не может открыть t.me — домен закрыт политикой
сети его окружения. Railway такого ограничения не имеет, поэтому забираем посты
здесь и отдаём их по обычному адресу, который агент прочитать может.

Состояние не храним: посты забираются в момент запроса. Публичное веб-превью
канала (t.me/s/<имя>) отдаёт последние ~20 сообщений — этого хватает с запасом.
"""

import html
import os
import re
import ssl
import sys
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html.parser import HTMLParser

CHANNELS = ["zyrybull", "kitinvest", "konenkov_official"]
TOKEN = os.environ.get("FEED_TOKEN", "")
PORT = int(os.environ.get("PORT", "8080"))
CTX = ssl.create_default_context()
UA = {"User-Agent": "Mozilla/5.0 (compatible; market-signals collector)"}


class Posts(HTMLParser):
    """Собирает текст и ссылки сообщений с публичной страницы канала.

    Сообщение на t.me/s/ — это div с классом tgme_widget_message_text.
    Считаем вложенность вручную, чтобы не оборваться на первом же </div>.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.items = []          # [{"date": ..., "text": [...], "links": [...]}]
        self.depth = 0           # глубина внутри блока текста, 0 = снаружи
        self.pending_date = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "time" and "datetime" in a and self.depth == 0:
            self.pending_date = a["datetime"]
            return
        if self.depth:
            self.depth += 1
            if tag == "br":
                self.depth -= 1
                self.items[-1]["text"].append("\n")
            elif tag == "a" and a.get("href", "").startswith("http"):
                self.items[-1]["links"].append(a["href"])
            return
        if tag == "div" and "tgme_widget_message_text" in a.get("class", ""):
            self.depth = 1
            self.items.append({"date": self.pending_date, "text": [], "links": []})

    def handle_endtag(self, tag):
        if self.depth and tag != "br":
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.items[-1]["text"].append(data)


def fetch(channel):
    url = "https://t.me/s/%s" % channel
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        page = r.read().decode("utf-8", "replace")
    p = Posts()
    p.feed(page)
    out = []
    for it in p.items:
        text = re.sub(r"\n{3,}", "\n\n", "".join(it["text"]).strip())
        if not text:
            continue
        out.append({"date": it["date"] or "?", "text": text,
                    "links": sorted(set(l for l in it["links"] if "t.me/" not in l))})
    out.reverse()  # свежие сверху
    return out


def render():
    lines = ["Свежие посты отслеживаемых каналов. Формат: заголовок канала, "
             "затем сообщения от новых к старым.", ""]
    for ch in CHANNELS:
        lines.append("=" * 60)
        lines.append("КАНАЛ: %s  (t.me/%s)" % (ch, ch))
        lines.append("=" * 60)
        try:
            posts = fetch(ch)
        except Exception as e:
            lines += ["ОШИБКА ЧТЕНИЯ: %s" % e, ""]
            continue
        if not posts:
            lines += ["Сообщений не найдено.", ""]
            continue
        for p in posts:
            lines.append("")
            lines.append("--- %s" % p["date"])
            lines.append(p["text"])
            if p["links"]:
                lines.append("Ссылки в посте: %s" % " , ".join(p["links"]))
        lines.append("")
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0].strip("/")
        if path in ("health", ""):
            self._send(200, "ok")
            return
        if not TOKEN or path != TOKEN:
            self._send(404, "not found")
            return
        try:
            self._send(200, render())
        except Exception as e:
            self._send(500, "сбой сборщика: %s" % e)

    def log_message(self, fmt, *args):
        # Путь содержит токен — в логи его не пишем.
        sys.stderr.write("%s %s\n" % (self.command, self.responses.get(0, "")))


if __name__ == "__main__":
    # Разовый прогон: собрать ленту в файл и выйти. Так его запускает GitHub Actions —
    # сервер и токен при этом не нужны.
    if "--once" in sys.argv:
        out = sys.argv[sys.argv.index("--once") + 1] if len(sys.argv) > sys.argv.index("--once") + 1 else "feed.txt"
        from datetime import datetime, timezone
        body = "Собрано: %s\n%s" % (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), render())
        with open(out, "w", encoding="utf-8") as f:
            f.write(body)
        print("записано %s, байт %d" % (out, len(body.encode())))
        sys.exit(0)

    if not TOKEN:
        print("FAIL: не задана переменная FEED_TOKEN", file=sys.stderr)
        sys.exit(1)
    print("сборщик слушает порт %d, каналов: %d" % (PORT, len(CHANNELS)))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
