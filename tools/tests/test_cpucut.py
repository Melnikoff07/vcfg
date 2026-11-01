#!/usr/bin/env python3
"""Alias runner for tools/tests/test_cpu_cost.py (Item H: opt-in CPU reduction options)."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from test_cpu_cost import CallSites, CpuCostHelpers, FlightRecorderLite, MonitorEventsKeepThePll  # noqa: F401

if __name__ == "__main__":
    unittest.main()
