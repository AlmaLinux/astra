# Elections runbook: tally an election

## Purpose
Tally a closed election and publish results and public audit artifacts.

Tallying is a publication step. It produces and publishes:
- The election results (winners, quota, eliminations)
- Public audit log events (tally rounds and tally completion)
- Public JSON artifacts (ballots export and audit export)

Treat tally as operationally irreversible. Even if files are removed later, results may already have been shared or downloaded.

## Prerequisites
- The election status is `closed`.
- You have election admin permissions and operational access to run management commands.
- Database is healthy and you can tolerate the tally runtime.
- You have confirmed there is no unresolved incident that would invalidate tally (credential leak, widespread voting outage, active dispute).

## Procedure

### Option A: Tally during "Conclude Election" (preferred when ending voting)
If the election is still `open` and you are concluding it now:
1. Use the close runbook.
2. In the `Conclude Election` modal, do not select "Close election, but do not tally votes".
3. Submit.
4. Confirm the UI message "Election closed and tallied." and confirm status becomes `tallied`.

### Option B: Tally later using the management command
Use this when the election is already `closed` (or when you closed with "skip tally").

1. Run a dry-run to see what would be tallied:
   - `podman-compose exec -T web python manage.py advance_elections --dry-run`
2. Confirm the output includes your election in the "would tally" set.
3. Run the command:
   - `podman-compose exec -T web python manage.py advance_elections`
4. Confirm the output reports the election as tallied.

Note: `advance_elections` may close or tally multiple elections. Run it only when you are confident that is safe.

### Confirm results are published
1. Open the election detail page.
2. Confirm status is `tallied`.
3. Confirm the `Results` section is visible.
4. Confirm the audit log is visible and includes:
   - Tally rounds
   - Tally completed
5. Confirm public exports are available:
   - Ballots JSON (`/elections/<id>/public/ballots.json` or the linked file)
   - Audit JSON (`/elections/<id>/public/audit.json` or the linked file)

## Rollback or abort guidance
- If tally fails, do not modify election data by hand.
- There is no supported "un-tally" operation.

If you suspect the election should not be tallied (for example, because of a security incident):
1. Do not run tally.
2. Preserve logs and evidence.
3. Communicate that results are delayed and under review.
4. Follow the incident response runbook.

If you already tallied and later discover a disqualifying issue:
1. Communicate clearly that the published results are under dispute.
2. Do not delete audit artifacts. Preserve transparency.
3. Decide, with stakeholders, whether the election must be rerun.

## Failure handling

### A) Tally fails with an ElectionError
Actions:
1. Capture the exact error output.
2. Confirm the election is still `closed`.
3. Retry once after verifying database health.
4. If it fails again, stop and escalate.

### B) Tally is slow or appears stuck
Actions:
1. Do not restart the application mid-tally unless you understand the transaction boundaries.
2. Check application logs for progress or errors.
3. If you must stop the process, document the exact time and what was run, then re-evaluate before retrying.

### C) Results look wrong
Actions:
1. Do not publish additional summaries until reviewed.
2. Review the audit log timeline and tally rounds.
3. Validate inputs:
   - Candidate list
   - Exclusion group configuration
   - Seat count
4. Escalate to maintainers for verification against expected outcomes.
