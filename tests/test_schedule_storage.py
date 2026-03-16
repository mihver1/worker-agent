from __future__ import annotations

from datetime import UTC, datetime

import pytest


def test_schedule_storage_add_update_delete_and_merge(tmp_path, monkeypatch):
    from worker_core import config as cfg_mod
    from worker_core.schedules import (
        ScheduleRegistry,
        add_schedule,
        delete_schedule,
        get_schedule,
        update_schedule,
    )

    fake_config = tmp_path / "config"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".artel").mkdir()
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", fake_config)

    add_schedule(
        scope="global",
        schedule_id="daily",
        project_dir=str(project_dir),
        kind="interval",
        every_seconds=60,
        prompt="global prompt",
        run_missed="latest",
    )
    add_schedule(
        scope="project",
        schedule_id="daily",
        project_dir=str(project_dir),
        kind="cron",
        cron="0 9 * * 1-5",
        prompt_name="review",
    )

    registry = ScheduleRegistry()
    loaded = registry.load_merged_config(str(project_dir))
    assert loaded.schedules["daily"].scope == "project"
    assert loaded.schedules["daily"].cron == "0 9 * * 1-5"

    updated = update_schedule(
        "daily",
        project_dir=str(project_dir),
        enabled=False,
        arg="repo=backend",
    )
    assert updated.enabled is False
    assert updated.arg == "repo=backend"
    assert get_schedule("daily", str(project_dir)) is not None

    deleted = delete_schedule("daily", str(project_dir))
    assert deleted is not None
    assert get_schedule("daily", str(project_dir)).scope == "global"
    assert get_schedule("daily", str(project_dir)).run_missed == "latest"


@pytest.mark.parametrize(
    ("cron", "expected"),
    [
        ("*/5 * * * *", "2025-01-01 12:05:00"),
        ("0 9 * * 1-5", "2025-01-01 09:00:00"),
    ],
)
def test_next_schedule_time_for_cron(cron, expected):
    from worker_core.schedules import ScheduleRecord, next_schedule_time

    schedule = ScheduleRecord(
        id="job",
        scope="project",
        kind="cron",
        cron=cron,
        prompt="hello",
    )
    start = datetime(
        2025,
        1,
        1,
        12 if cron.startswith("*/") else 8,
        2 if cron.startswith("*/") else 0,
        tzinfo=UTC,
    )
    next_run = next_schedule_time(schedule, start)
    assert next_run is not None
    assert next_run.strftime("%Y-%m-%d %H:%M:%S") == expected


def test_render_prompt_variables_supports_key_value_and_input():
    from worker_core.schedules import render_prompt_variables

    assert render_prompt_variables("hello world") == {"input": "hello world"}
    assert render_prompt_variables("repo=backend branch=main") == {
        "repo": "backend",
        "branch": "main",
    }
