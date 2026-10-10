"""The smoke verdict must reject wrong behavior even when config.h was opened."""
import pytest
from buildchecker import smoke


@pytest.mark.parametrize("app_code,app_output", [(0, "2\n"), (1, "1\n")])
def test_smoke_rejects_wrong_result(tmp_path, monkeypatch, app_code, app_output):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "main.c").write_text("/* fixture */")

    def fake_run(cmd, cwd):
        if cmd[0] == "strace" and "-o" in cmd:
            (cwd / "trace.txt").write_text('1 openat(AT_FDCWD, "config.h", O_RDONLY) = 3\n')
            return 0, ""
        if cmd[0] == "./app":
            return app_code, app_output
        return 0, "tool version\n"

    monkeypatch.setattr(smoke, "run", fake_run)
    result = smoke.run_smoke(fixture)
    assert result["config_h_opened"]
    assert result["passed"] is False
