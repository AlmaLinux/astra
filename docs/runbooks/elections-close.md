# Elections runbook: close an election

## Purpose
Close an open election so no further ballots can be submitted, and anonymize voter credentials.

Closing is an irreversible privacy action. Closing an election triggers anonymization of credentials and scrubs election-related emails from the mail system.

## Prerequisites
- You have election admin permissions in Astra.
- The election status is `open`.
- You have confirmed it is appropriate to end voting now (scheduled end time reached, or an incident requires early closure).
- You have a communication plan for voters if voting is ending earlier than expected.

## Safety notes
- Closing sets the election status to `closed` and sets the election end time to "now".
- Closing performs irreversible anonymization:
  - Voter usernames are removed from stored voting credentials.
  - Election-related emails (credential and receipt emails) are deleted from the email queue/history.
- After close, you cannot recover the voter-to-credential mapping from the application.

## What is and is not scrubbed at close

Closing atomically runs anonymization. The following are the precise fields and records removed:

| Category | What is removed |
|---|---|
| `VotingCredential.freeipa_username` | Set to `NULL` for every credential row belonging to the election. The credential row itself (public ID, weight) is **retained**. |
| `Email` records | All `Email` rows where `context["election_id"]` matches the election ID are **deleted**. This covers credential-delivery and vote-receipt emails issued by the current implementation. Legacy credential emails composed as plain `message`/`html_message` with the credential link are also deleted. |

The following are explicitly **not** scrubbed:

| Category | Why retained |
|---|---|
| `VotingCredential` rows | Public ID and weight are needed for receipt verification and tally audit. Only the username mapping is removed. |
| `Ballot` rows (ranking, hashes, chain) | Required for tally and the public cryptographic audit trail. These rows are not linkable to a username after close. |
| `AuditLogEntry` records | Audit log is append-only and retained in full for accountability. |

The private `election_anonymized` audit event (visible only to admins) records:
- `credentials_affected`: count of credentials whose username was nulled.
- `emails_scrubbed`: count of email records deleted.
- `scrub_anomaly`: `true` if fewer emails were scrubbed than credentials affected (warranting review).
- `scrubbed_fields`: stable list of field names/categories that were scrubbed.

## Procedure

### 1) Pre-close checks
1. Open the election detail page.
2. Confirm the election you are operating on:
   - Election name
   - Election ID (from the URL)
   - Current status: `open`
3. Confirm you are not closing early by mistake:
   - Check the current time.
   - Compare against the configured end time.
4. Review participation (if visible):
   - Check whether quorum is met.
   - Note any anomalies (unexpected ballot volume, reports of issues).
5. Check for active incidents:
   - If there are unresolved credential leaks, phishing, or system outages, consider extending rather than closing.

### 2) Close the election from the UI
1. On the election detail page, click `Conclude Election`.
2. In the confirmation modal:
   - Read the consequences carefully.
   - If you want to close but not tally immediately, select the "Close election, but do not tally votes" option.
   - Complete the typed confirmation requirement (type the election ID or name).
3. Submit the form.
4. Confirm you see a success message:
   - "Election closed." (if you skipped tally)
   - "Election closed and tallied." (if tally ran)

### 3) Confirm close completed
1. Refresh the election detail page.
2. Confirm status is now `closed` (or `tallied` if tally ran).
3. Confirm the audit log becomes available:
   - Click `View audit log`.
   - Look for public events including "Election closed" and "Election anonymized".

## Rollback or abort guidance
- Closing is not reversible.
- Do not attempt to "re-open" an election in production.

If you closed by mistake:
1. Stop and document what happened (time, who acted, why).
2. Inform stakeholders that the election was closed in error.
3. Create a new election to re-run voting, and communicate clearly that the prior election is invalid.
4. Keep the original election and audit log intact for transparency.

## Failure handling

### A) Close fails with an ElectionError
Possible causes:
- Election is not `open`.
- A database or validation error occurred.

Actions:
1. Read the error message shown in the UI.
2. Confirm the election status is still `open`.
3. Retry once.
4. If it fails again, stop and escalate.

### B) Close succeeded, but tally failed
The UI will show: "Election closed, but tally failed: ..."

Actions:
1. Do not attempt to re-open the election.
2. Capture the exact error message.
3. Run tally later using the tally runbook's manual admin UI path.
4. If you must publish results on a timeline, communicate that tally is delayed while the issue is investigated.

### C) Voters report they can still vote after close
Actions:
1. Confirm the election status in the UI.
2. If status is not `closed`, the close operation did not complete; retry close.
3. If status is `closed` but the vote page is still usable, treat this as a high-severity incident and escalate immediately.
