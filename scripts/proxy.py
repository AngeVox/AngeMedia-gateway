#!/usr/bin/env python3
"""兼容入口：启动 AngeMedia Gateway。"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
runpy.run_module("angemedia_gateway.server", run_name="__main__")
