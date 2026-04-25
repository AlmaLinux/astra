# Membership request repair runbook

## Purpose
Use the `membership_request_repair` management command for targeted operator recovery when a membership request was rejected in error and must be returned to `pending` without direct database edits.

This command is the supported replacement for ad hoc `manage.py shell` snippets for the rejected-to-pending recovery path.

## Supported repair
- `rejected -> pending`

The command currently:
- clears `decided_at`
- clears `decided_by_username`
- clears `on_hold_at`
- removes a trailing synthetic `{"Rejection reason": ...}` response row when that row was appended by the reject workflow
- records an operator note with the supplied actor and reason

The command does not currently support arbitrary field edits or other state transitions.

## Prerequisites
- You have shell access to the Astra application container host.
- You know the membership request ID that needs repair.
- You have a short operator-facing reason for the repair.
- You have confirmed the request should return to `pending` rather than stay rejected.

## Preferred entrypoint
If you are running from a checkout of this repository on the container host, prefer the wrapper script instead of hand-assembling `podman-compose exec` each time.

```bash
infra/scripts/astra-manage-web.sh help membership_request_repair
```

The wrapper is only a convenience layer. The authoritative behavior still lives in the Django management commands.

## Dry run first
Always preview the repair before applying it.

```bash
infra/scripts/astra-manage-web.sh membership_request_repair \
  --request-id 232 \
  --reset-to-pending \
  --dry-run
```

Equivalent direct container invocation:

```bash
podman-compose exec -T web python manage.py membership_request_repair \
  --request-id 232 \
  --reset-to-pending \
  --dry-run
```

Expected output is a JSON summary like:

```json
{"request_id": 232, "from_status": "rejected", "to_status": "pending", "dry_run": true, "trimmed_rejection_reason": true, "note_created": false}
```

Check that:
- `from_status` is `rejected`
- `to_status` is `pending`
- `trimmed_rejection_reason` matches your expectation

If the command reports a different source status, stop and reassess. Do not force the repair through a shell.

## Apply the repair
When the dry run looks correct, apply the change with explicit attribution.

```bash
infra/scripts/astra-manage-web.sh membership_request_repair \
  --request-id 232 \
  --reset-to-pending \
  --apply \
  --actor alex \
  --reason "Resetting due to email bug"
```

Equivalent direct container invocation:

```bash
podman-compose exec -T web python manage.py membership_request_repair \
  --request-id 232 \
  --reset-to-pending \
  --apply \
  --actor alex \
  --reason "Resetting due to email bug"
```

Expected output is a JSON summary like:

```json
{"request_id": 232, "from_status": "rejected", "to_status": "pending", "dry_run": false, "trimmed_rejection_reason": true, "note_created": true}
```

## Post-apply checks
After applying the repair, verify:
- the request status is `pending`
- the request no longer shows stale rejection metadata
- the note timeline contains the operator reason you supplied

If the request still needs content changes from the applicant, use the normal product workflow after the repair rather than editing fields directly in the database.

## Fallback maintenance shell
Use the maintenance shell only when the guided command cannot cover the recovery case yet.

Start it with:

```bash
infra/scripts/astra-manage-web.sh maintenance_shell
```

Inside the shell, prefer the preloaded helpers instead of ad hoc model mutation:
- `get_membership_request(request_id)`
- `preview_reset_rejected_request(request_id)`
- `apply_reset_rejected_request(request_id, actor_username="alex", note_content="reason")`
- `inspect_cache(prefix="freeipa_", key=None)`
- `clear_cache()`
- `send_test_email("user@example.com", subject="Astra test email", content="Astra maintenance shell test email.", deliver_queued=False)`
- `run_send_queued_mail()`

The shell banner also points back to `membership_request_repair --help`. Treat that command as the default path and the shell as an exception path.

### Inspect cache data
To inspect the current cache contents from the maintenance shell:

```python
inspect_cache(prefix="freeipa_")
inspect_cache(key="freeipa_user_alice")
```

`inspect_cache()` returns a dict with backend information, visible keys, and an optional preview of a specific key.

### Clear all cache data
To clear the default Django cache from the maintenance shell:

```python
clear_cache()
```

This clears the configured default cache backend, not just FreeIPA-related keys.

### Send a test email
To queue a test email through the same SSOT email pipeline used by the app:

```python
send_test_email("user@example.com")
send_test_email(
  "user@example.com",
  subject="Operator smoke test",
  content="This is a manually queued maintenance-shell test email.",
)
```

If you want the shell to also trigger queued-mail delivery immediately:

```python
send_test_email("user@example.com", deliver_queued=True)
```

Or run delivery separately:

```python
run_send_queued_mail()
```

Be aware that `run_send_queued_mail()` processes the queued mail command, not just the single test message.

## Failure handling
- If the command says the request does not exist, verify the ID.
- If the command says the request is not rejected, stop. This runbook only covers rejected requests.
- If the dry-run summary does not match the expected state change, stop and escalate instead of editing through `manage.py shell`.
- If you find yourself needing custom writes in the maintenance shell, stop and convert the case into a new guided command or helper instead of improvising the mutation.

## Guardrails
- Do not use direct SQL or unmanaged `manage.py shell` writes for this recovery path.
- Do not reuse someone else's username in `--actor`; attribution should match the operator performing the repair.
- Keep the `--reason` text concise and factual so the note timeline remains useful during later review.