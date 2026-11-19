from __future__ import annotations

import httpx
import respx
from click.testing import CliRunner

from jarvis_sidekick import doctor
from jarvis_sidekick.cli import main


def _by_name(checks):
    return {c.name: c for c in checks}


def test_offline_run_skips_what_is_not_configured(resume_file, monkeypatch):
    checks = _by_name(doctor.run(offline=True))
    assert set(checks) == {"resume", "env file", "browser", "OpenJarvis", "microphone"}
    assert checks["resume"].status == doctor.SKIP and "SIDEKICK_RESUME" in checks["resume"].detail
    monkeypatch.setenv("SIDEKICK_RESUME", str(resume_file))
    assert _by_name(doctor.run(offline=True))["resume"].detail.startswith("name, email, phone found")


def test_env_file_readable_by_others_fails():
    from jarvis_sidekick import paths

    env = paths.home() / ".env"
    env.write_text("X=1")
    env.chmod(0o644)
    assert _by_name(doctor.run(offline=True))["env file"].status == doctor.FAIL
    env.chmod(0o600)
    assert _by_name(doctor.run(offline=True))["env file"].status == doctor.OK


@respx.mock
def test_live_run_reports_each_service_separately(monkeypatch):
    rising = [100.0 + i for i in range(30)]
    chart = {"chart": {"result": [{"meta": {}, "timestamp": [1_760_000_000 + 86_400 * i for i in range(30)],
                                   "indicators": {"quote": [{"close": rising}]}}]}}
    respx.get(url__startswith="https://query1.finance.yahoo.com").mock(return_value=httpx.Response(200, json=chart))
    respx.get(url__startswith="https://stooq.com").mock(return_value=httpx.Response(200, text="No data"))
    respx.get("https://remotive.com/api/remote-jobs").mock(
        return_value=httpx.Response(200, json={"jobs": [{"url": "https://r.example/1", "title": "Engineer",
                                                         "description": "Python"}]})
    )
    respx.route().mock(return_value=httpx.Response(503))  # everything else is down
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "4242")

    checks = _by_name(doctor.run())

    assert checks["prices (Yahoo)"].status == doctor.OK and "129.00" in checks["prices (Yahoo)"].detail
    assert checks["prices fallback (Stooq)"].status == doctor.FAIL
    assert checks["news feeds"].status == doctor.FAIL
    assert checks["jobs: remotive"].detail == "1 listings, 1 with a description"
    assert checks["jobs: arbeitnow"].status == doctor.FAIL
    assert checks["jobs: adzuna"].status == doctor.SKIP
    assert checks["ElevenLabs voice"].status == doctor.SKIP
    assert checks["Telegram bot"].status == doctor.FAIL and "AAAA" not in checks["Telegram bot"].detail


def test_cli_exit_code_follows_failures(monkeypatch):
    monkeypatch.setattr(doctor, "run", lambda offline=False: [doctor.Check("a", doctor.OK, "fine"),
                                                              doctor.Check("b", doctor.SKIP, "set X")])
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0 and "1 ok, 0 failed, 1 skipped" in result.output
    monkeypatch.setattr(doctor, "run", lambda offline=False: [doctor.Check("a", doctor.FAIL, "down")])
    assert CliRunner().invoke(main, ["doctor"]).exit_code == 1
