#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выбирает, чем работать этому контейнеру.

В проекте два сервиса из одного репозитория. Railway по умолчанию читает
railway.json из корня, поэтому оба получают одну команду запуска. Различаем
их по переменным: сборщику задан FEED_TOKEN, сводке — TG_TOKEN.
"""

import os
import runpy

if os.environ.get("FEED_TOKEN") and not os.environ.get("TG_TOKEN"):
    runpy.run_path("collector.py", run_name="__main__")
else:
    runpy.run_path("digest.py", run_name="__main__")
