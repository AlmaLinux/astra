# Elections runbook: start an election

## Purpose
Start a draft election, issue voting credentials, and send credential emails to eligible voters.

Starting an election is a high-impact operation. It sets the election status to `open`, sets the published start time to the current time, and sends credential emails.

## Prerequisites
- You have election admin permissions in Astra (can create and manage elections).
- You have a stable connection to FreeIPA (user lookup, email addresses, timezones).
- Outbound email delivery is working (SES or configured SMTP) and queues are healthy.
- You have the election details finalized:
  - Election name, description, URL
  - Number of seats
  - Planned end date and time (UTC)
  - Quorum configuration (turnout requirement)
  - Candidate list and any exclusion groups
- You have a communication plan if emails fail or eligibility changes after start.

## Procedure

### 1) Pre-flight checklist (do this before clicking Start)
1. Open the election draft in the UI:
   - Go to `Elections`.
   - Open the draft election.
   - Click `Edit`.
2. Confirm the candidate list is correct.
   - Verify candidate usernames are correct.
   - Verify nomination information if shown.
3. Confirm exclusion groups (if used):
   - Group names are clear.
   - The candidate list in each group is correct.
   - `max_elected` matches the intended policy.
4. Confirm eligibility assumptions:
   - The eligible group is correct (if configured).
   - Minimum membership age is correct.
5. Confirm the voting window:
   - Start time will become "now" when you start the election.
   - End time is set to the intended close time (and timezone).
6. Confirm the credential email template:
   - Use the email preview in the election editor.
   - Verify the subject and body mention the correct election name and dates.
   - Verify the email uses the correct term "quorum" (not "quota") when describing turnout.
7. Confirm support readiness:
   - Identify who will handle voter support during the election window.
   - Prepare a short help response for "I did not get my credential".

### 2) Start the election
1. In the election edit page, click the action to start the election.
2. Wait for the result message.
3. Confirm you see a success message like "Election started; emailed N voter(s)."
4. If you see warnings about skipped voters or failures, do not ignore them.

### 3) Verify the election opened
1. Open the election detail page.
2. Confirm:
   - Status shows `open`.
   - Voting window shows the intended end date.
3. Confirm the `Vote` button is visible to an eligible test account.
4. Confirm an audit log entry exists for the start event:
   - Go to `View audit log` (only visible after close/tally). For start validation, rely on the start success message and admin visibility.

### 4) Spot-check credential delivery
1. In the election detail page, use `Resend voting credential` for one or two known users.
2. Confirm those users receive the email.
3. Confirm the vote link works and the credential autofill behavior matches expectations.

## Rollback or abort guidance
Starting an election cannot be "undone" cleanly:
- Emails cannot be unsent.
- The election is now open and may receive ballots.

If you started the wrong election or started too early:
1. Immediately communicate in the expected channel (website notice, mailing list, etc.).
2. Decide whether to:
   - Let voting continue (if the mistake is minor), or
   - Conclude the election early and create a corrected replacement election.
3. If you conclude early, include the reason in the public explanation and retain the audit trail.

Do not try to repair this by editing the database directly in production. If a correction requires data changes, escalate to maintainers and document the full timeline.

## Failure handling

### A) "Election started" but many emails were skipped
Likely causes:
- Users missing email addresses in FreeIPA
- Users not found in FreeIPA

Actions:
1. Review the UI message counts (emailed, skipped, failures).
2. For high skip counts, pause and assess whether the election should remain open.
3. Fix missing emails in FreeIPA if appropriate.
4. Use `Resend voting credential` for affected users after fixes.

### B) "Election started" but many emails failed
Likely causes:
- Email provider outage
- Template rendering errors
- Network or FreeIPA lookup errors

Actions:
1. Check email service health.
2. If failures are widespread, consider concluding the election and restarting later with a new election.
3. If failures are limited, retry via `Resend voting credential` once the issue is resolved.

### C) FreeIPA is unavailable
Actions:
1. Do not start the election until FreeIPA is stable. Starting without FreeIPA will likely skip or fail many emails.
2. If the election is already started, prioritize restoring FreeIPA, then resend credentials.

### D) Voter reports "I never received a credential"
Actions:
1. Confirm the voter is eligible.
2. Ask them to check spam/junk.
3. Use `Resend voting credential` for their username.
4. If the user has no email on file, fix the email in FreeIPA (if policy allows) and resend.
