import app as appmod


def test_run_scheduled_job_command_invokes_the_named_job(app, monkeypatch):
    calls = []
    monkeypatch.setitem(appmod._SCHEDULED_JOBS, "generate_missing_payments", lambda: calls.append(1))

    runner = app.test_cli_runner()
    result = runner.invoke(args=["run-scheduled-job", "generate_missing_payments"])

    assert result.exit_code == 0
    assert calls == [1]
    with app.app_context():
        run = appmod.ScheduledJobRun.query.filter_by(job_name="generate_missing_payments").one()
        assert run.status == "success"


def test_run_scheduled_job_command_rejects_unknown_name(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["run-scheduled-job", "not_a_real_job"])

    assert result.exit_code != 0
    assert "Unknown scheduled job" in result.output


def test_all_registered_jobs_are_real_functions():
    # Guards against a typo silently making a job unreachable from any cron
    # schedule -- every entry must resolve to a callable, not None or a stale name.
    for name, fn in appmod._SCHEDULED_JOBS.items():
        assert callable(fn), f"{name!r} is not callable"
