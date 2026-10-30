"""Tests for RateCalculator."""

import pytest

from ros2_vitals.utils import rate_calculator
from ros2_vitals.utils.rate_calculator import RateCalculator


@pytest.fixture
def clock(monkeypatch):
    """Controllable replacement for time.time() inside the rate calculator."""

    class Clock:
        now = 100.0

    monkeypatch.setattr(rate_calculator.time, "time", lambda: Clock.now)
    return Clock


def test_first_sample_returns_zero(clock):
    assert RateCalculator().calculate_rate("a", 500.0) == 0.0


def test_rate_per_second(clock):
    calc = RateCalculator()
    calc.calculate_rate("a", 1000.0)
    clock.now += 2.0
    assert calc.calculate_rate("a", 3000.0) == pytest.approx(1000.0)
    clock.now += 1.0
    assert calc.calculate_rate("a", 3500.0) == pytest.approx(500.0)


def test_zero_time_delta(clock):
    calc = RateCalculator()
    calc.calculate_rate("a", 1000.0)
    assert calc.calculate_rate("a", 2000.0) == 0.0


def test_counter_reset_uses_current_value(clock):
    calc = RateCalculator()
    calc.calculate_rate("a", 1000.0)
    clock.now += 1.0
    assert calc.calculate_rate("a", 200.0) == pytest.approx(200.0)


def test_calculate_rates_keeps_counters_separate(clock):
    calc = RateCalculator()
    calc.calculate_rates("eth0", {"rx": 0.0, "tx": 0.0})
    clock.now += 1.0
    assert calc.calculate_rates("eth0", {"rx": 100.0, "tx": 50.0}) == pytest.approx(
        {"rx": 100.0, "tx": 50.0}
    )


def test_clear_by_prefix(clock):
    calc = RateCalculator()
    calc.calculate_rate("net.rx", 0.0)
    calc.calculate_rate("disk.read", 0.0)
    calc.clear("net.")
    clock.now += 1.0
    assert calc.calculate_rate("net.rx", 100.0) == 0.0
    assert calc.calculate_rate("disk.read", 100.0) == pytest.approx(100.0)


def test_remove_key(clock):
    calc = RateCalculator()
    calc.calculate_rate("a", 0.0)
    calc.remove_key("a")
    calc.remove_key("missing")
    clock.now += 1.0
    assert calc.calculate_rate("a", 100.0) == 0.0
