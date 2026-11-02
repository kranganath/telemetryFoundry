"""Unit tests for the dry-run expression evaluator."""

import pytest

from app.evaluator import EvalError, cast_value, dry_run, eval_expr


def test_literals_and_columns():
    assert eval_expr("`a`", {"a": "x"}) == "x"
    assert eval_expr("42", {}) == 42
    assert eval_expr("'it''s'", {}) == "it's"
    assert eval_expr("true", {}) is True
    assert eval_expr("NULL", {}) is None


def test_cast_and_functions():
    assert eval_expr("CAST(`a` AS BIGINT)", {"a": "42"}) == 42
    assert eval_expr("CAST(`a` AS TIMESTAMP)", {"a": "2024-01-02 03:04:05"})
    assert eval_expr("upper(`a`)", {"a": "x"}) == "X"
    assert eval_expr("concat(`a`, '-', `b`)", {"a": "x", "b": "y"}) == "x-y"
    assert eval_expr("coalesce(`a`, 'd')", {"a": ""}) == "d"


def test_errors_are_explicit():
    with pytest.raises(EvalError):
        eval_expr("`missing`", {})
    with pytest.raises(EvalError):
        eval_expr("CAST(`a` AS BIGINT)", {"a": "nope"})
    with pytest.raises(EvalError):
        eval_expr("mystery(`a`)", {"a": 1})
    with pytest.raises(EvalError):
        cast_value("2024-13-99", "TIMESTAMP")


def test_dry_run_table():
    mappings = [
        {"source": "event_time", "target": "time", "transform": "cast"},
        {"source": "", "target": "class_uid", "transform": "constant",
         "expression": "4001"},
    ]
    result = dry_run(mappings, [{"event_time": "2024-01-02 03:04:05"}],
                     {"time": "TIMESTAMP"})
    assert result["columns"] == ["time", "class_uid"]
    assert result["errors"] == 0
    assert result["rows"][0]["class_uid"] == 4001

    bad = dry_run(mappings, [{"event_time": "not-a-time"}],
                  {"time": "TIMESTAMP"})
    assert bad["errors"] == 1
    assert bad["rows"][0]["time"].startswith("ERR:")
