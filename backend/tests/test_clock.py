"""Game clock & action time costs (t_1dd0f6eb)."""
import pytest

from app.modules.exploration.clock import (
    CONVERSATION_MIN,
    SEARCH_MIN,
    ClockAuthorityError,
    GameClock,
)


def test_conversation_costs_within_5_to_20():
    clock = GameClock()
    clock.conversation("brief")
    assert CONVERSATION_MIN == (5, 20)
    assert clock.now.hour == 8 and clock.now.minute == 5
    clock.conversation("parley")
    assert clock.now.minute == 25


def test_search_training_travel_sleep_costs():
    clock = GameClock()
    clock.search()
    assert (clock.now.hour, clock.now.minute) == (8, SEARCH_MIN)
    clock.training(1)
    assert (clock.now.hour, clock.now.minute) == (9, 20)
    with pytest.raises(ValueError):
        clock.training(5)
    clock.travel(10)  # 10 km variable travel
    assert (clock.now.hour, clock.now.minute) == (11, 20)
    clock.sleep()
    assert (clock.now.hour, clock.now.minute) == (19, 20)


def test_ai_can_never_advance_time():
    clock = GameClock()
    with pytest.raises(ClockAuthorityError):
        clock.advance_minutes(10, actor="ai:narrator", reason="dramatic timeskip")
    with pytest.raises(ClockAuthorityError):
        clock.advance_minutes(10, actor="player", reason="rest")
    clock.advance_minutes(10, actor="engine", reason="test")
    assert clock.now.minute == 10
    assert len(clock.log) == 1


def test_time_never_runs_backwards():
    clock = GameClock()
    with pytest.raises(ValueError):
        clock.advance_minutes(-5, actor="engine", reason="oops")
