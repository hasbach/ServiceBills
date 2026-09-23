import os
import app as appmod


def _join_trigger_pool():
    # SCHEDULED_JOB_TRIGGER_POOL.spawn() schedules a greenlet that only runs
    # once something yields to gevent's hub -- the test client's synchronous
    # call never does that on its own, so without this the assertions below
    # would race the job that's supposed to have already run.
    if appmod.SCHEDULED_JOB_TRIGGER_POOL is not None:
        appmod.SCHEDULED_JOB_TRIGGER_POOL.join()


def test_missing_secret_config_fails_closed(client, monkeypatch):
    monkeypatch.delenv("CRON_TRIGGER_SECRET", raising=False)
    r = client.post("/api/internal/scheduled-jobs/generate_missing_payments")
    assert r.status_code == 503


def test_wrong_secret_is_rejected(client, monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_SECRET", "the-real-secret")
    r = client.post("/api/internal/scheduled-jobs/generate_missing_payments",
                     headers={"X-Cron-Secret": "guess"})
    assert r.status_code == 401


def test_unknown_job_name_is_rejected(client, monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_SECRET", "the-real-secret")
    r = client.post("/api/internal/scheduled-jobs/not_a_real_job",
                     headers={"X-Cron-Secret": "the-real-secret"})
    assert r.status_code == 404


def test_valid_trigger_runs_the_job(app, client, monkeypatch):
    monkeypatch.setenv("CRON_TRIGGER_SECRET", "the-real-secret")
    calls = []
    monkeypatch.setitem(appmod._SCHEDULED_JOBS, "generate_missing_payments", lambda: calls.append(1))

    r = client.post("/api/internal/scheduled-jobs/generate_missing_payments",
                     headers={"X-Cron-Secret": "the-real-secret"})

    assert r.status_code == 202
    _join_trigger_pool()
    assert calls == [1]
    with app.app_context():
        run = appmod.ScheduledJobRun.query.filter_by(job_name="generate_missing_payments").one()
        assert run.status == "success"
