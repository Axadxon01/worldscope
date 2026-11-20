from __future__ import annotations

import pytest

from worldscope import cli


def test_prepare_migrates_then_syncs(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "alembic.command.upgrade", lambda config, rev: calls.append(f"upgrade {rev}")
    )

    def fake_sync() -> int:
        calls.append("sync")
        return 0

    monkeypatch.setattr(cli, "cmd_sync", fake_sync)
    assert cli.main(["prepare"]) == 0
    assert calls == ["upgrade head", "sync"]


def test_unknown_command_is_refused() -> None:
    with pytest.raises(SystemExit):
        cli.main(["drop-everything"])
