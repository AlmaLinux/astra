# Election Lifecycle Automation

`operations_hourly` runs `election_lifecycle_automation` for opted-in elections.

- Automatic start opens a draft election after its configured start time.
- Automatic end closes an open election after its configured end time only when no quorum is required or the required quorum is met.
- An unmet quorum leaves voting open and records one private deferral per scheduled end time.
- Manual start and conclude actions remain available. A successful manual action clears the corresponding automatic option.

## Control and rollout

Set `ELECTION_LIFECYCLE_AUTOMATION_ENABLED=false` to disable all automatic election transitions globally. Re-enable it by removing the setting or setting it to a true value.

Run the hourly operation in staging without mutation:

```bash
python manage.py operations_hourly --dry-run
```

`--force` is forwarded for operational consistency but does not override election lifecycle eligibility, quorum, timing, or opt-in policy.
