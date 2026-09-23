import app as appmod


def test_success_records_a_run(app, client):
    """A normal fire creates one ScheduledJobRun row and marks it successful."""
    with app.app_context():
        calls = []
        appmod._run_scheduled_job("unit_test_job", lambda: calls.append(1))

        assert calls == [1]
        run = appmod.ScheduledJobRun.query.filter_by(job_name="unit_test_job").one()
        assert run.status == "success"
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.error is None


def test_failure_is_recorded_not_raised(app, client):
    """The wrapper must never let a job's exception escape -- APScheduler would
    otherwise log a scary traceback and, worse, a caller relying on this for
    audit purposes needs the failure captured in the row, not just in logs."""
    with app.app_context():
        def boom():
            raise ValueError("simulated failure")

        appmod._run_scheduled_job("unit_test_failing_job", boom)

        run = appmod.ScheduledJobRun.query.filter_by(job_name="unit_test_failing_job").one()
        assert run.status == "failed"
        assert "simulated failure" in run.error
        assert run.finished_at is not None


def test_sqlite_has_no_advisory_lock_and_still_runs_once(app, client):
    """Tests run on SQLite (see conftest.py), which has no pg_try_advisory_lock.
    The wrapper must not even attempt the Postgres-only lock dance there, and
    must still run the job exactly once."""
    with app.app_context():
        assert appmod.db.engine.dialect.name == "sqlite"
        calls = []
        appmod._run_scheduled_job("unit_test_sqlite_job", lambda: calls.append(1))
        assert calls == [1]


def test_old_runs_are_pruned(app, client):
    from datetime import datetime, timedelta

    with app.app_context():
        old = appmod.ScheduledJobRun(
            job_name="ancient_job", status="success",
            started_at=datetime.utcnow() - timedelta(days=appmod.SCHEDULED_JOB_RUN_RETENTION_DAYS + 5),
            finished_at=datetime.utcnow() - timedelta(days=appmod.SCHEDULED_JOB_RUN_RETENTION_DAYS + 5),
        )
        appmod.db.session.add(old)
        appmod.db.session.commit()

        appmod._run_scheduled_job("unit_test_job_triggering_prune", lambda: None)

        assert appmod.ScheduledJobRun.query.filter_by(job_name="ancient_job").count() == 0
