import re
from pathlib import Path
import app as appmod


def _workflow_crons():
    text = Path(".github/workflows/scheduled-jobs.yml").read_text()
    return {m.group(2): m.group(1) for m in re.finditer(r'-\s*cron:\s*"([^"]+)"\s*#\s*(\w+)', text)}


def test_schedule_matches_github_workflow():
    assert appmod.SCHEDULE == _workflow_crons()


def test_every_scheduled_job_exists():
    for name in list(appmod.SCHEDULE) + list(appmod.ONPREM_EXTRA_SCHEDULE):
        assert name in appmod._SCHEDULED_JOBS


def test_onprem_schedule_drops_saas_billing_and_adds_license_refresh():
    s = appmod.onprem_schedule()
    assert "check_pro_plan_expirations" not in s and s["refresh_license"] == "0 3 * * *"


def test_run_scheduler_refuses_in_saas(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["run-scheduler"])
    assert result.exit_code != 0 and "onprem" in result.output


def test_run_scheduler_registers_jobs(app, monkeypatch):
    app.config["DEPLOYMENT_MODE"] = "onprem"
    added = []
    class FakeSched:
        def __init__(self, *a, **k): pass
        def add_job(self, fn, trigger=None, **k): added.append(k.get("id") or k.get("name"))
        def start(self): pass
    monkeypatch.setattr(appmod, "BlockingScheduler", FakeSched, raising=False)
    try:
        result = app.test_cli_runner().invoke(args=["run-scheduler"])
        assert result.exit_code == 0, result.output
    finally:
        app.config["DEPLOYMENT_MODE"] = "saas"
    assert set(appmod.onprem_schedule()) <= set(added)
