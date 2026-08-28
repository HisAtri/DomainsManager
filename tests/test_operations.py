from datetime import UTC, datetime

from domainsmanager_api.operations import OperationalMetrics, alert_events


def test_operational_alerts_only_include_nonzero_attention_states() -> None:
    metrics = OperationalMetrics(
        generated_at=datetime(2026, 8, 28, tzinfo=UTC),
        refresh_tasks_queued=4,
        refresh_tasks_running=1,
        refresh_tasks_expired_leases=0,
        notification_outbox_pending=3,
        notification_outbox_running=0,
        notification_outbox_dead_letter=2,
        notification_outbox_expired_leases=1,
        overdue_monitored_domains=0,
    )

    assert list(alert_events(metrics)) == [
        {
            "event": "operational_alert",
            "alert": "notification_dead_letter",
            "count": 2,
            "generated_at": "2026-08-28T00:00:00+00:00",
        },
        {
            "event": "operational_alert",
            "alert": "notification_expired_lease",
            "count": 1,
            "generated_at": "2026-08-28T00:00:00+00:00",
        },
    ]
