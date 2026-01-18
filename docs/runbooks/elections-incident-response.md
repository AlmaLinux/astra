# Elections runbook: incident response

## Purpose
Provide a repeatable, documented response for election incidents that can impact integrity, availability, or voter trust.

This runbook covers both technical failures (FreeIPA, email, database) and governance-impacting issues (credential leakage, suspected coercion, admin error).

## Prerequisites
- You have election admin permissions.
- You know how to contact:
  - The on-call maintainer (app and infrastructure)
  - Governance stakeholders for the election
  - The communications owner (who can send official notices)
- You can access logs and run management commands (if needed).

## Severity guide
- High: Integrity or privacy may be compromised, or voting is unavailable to many users.
- Medium: Limited user impact, workarounds exist.
- Low: Cosmetic issues, no integrity risk.

When in doubt, treat as High until proven otherwise.

## First response checklist (do this immediately)
1. Record the time, election name, and election ID.
2. Describe the symptoms and how they were detected.
3. Preserve evidence:
   - Screenshot relevant UI states.
   - Save error messages.
   - Capture log excerpts if you have access.
4. Decide whether to pause operations:
   - Avoid closing or tallying while an integrity incident is unresolved.
5. Notify the right people:
   - Maintainers for technical incidents.
   - Governance stakeholders for integrity or fairness issues.

## Incident scenarios and responses

### 1) Credential leakage or phishing
Examples:
- A voter posts their credential publicly.
- A phishing email asks voters to share their credential or nonce.

Actions:
1. Treat as High severity.
2. Communicate immediately: voters should not share credentials, receipts, or nonces.
3. If the leak is isolated:
   - Resend the credential to the affected voter if policy allows.
   - Ask them to submit a replacement ballot if they suspect compromise.
4. If the leak is widespread:
   - Consider extending the election to allow recovery.
   - Consider invalidating and rerunning the election if integrity cannot be assured.
5. Document all decisions.

### 2) FreeIPA outage or degraded FreeIPA performance
Impact:
- Starting the election may skip credential emails.
- Credential reminders may fail.

Actions:
1. If the election is not started: do not start until FreeIPA is stable.
2. If the election is open and users cannot log in or receive emails:
   - Extend the election.
   - Communicate the outage and the new deadline.
3. After recovery:
   - Resend credentials for impacted users.

### 3) Email delivery failure
Impact:
- Voters do not receive credentials or receipts.

Actions:
1. Check email provider status.
2. Do not end the election while email delivery is failing for many voters.
3. Extend the election if needed.
4. After recovery:
   - Resend credentials.
   - Provide voters guidance on verifying their receipt in the UI.

### 4) Suspected ballot stuffing or abnormal voting patterns
Signals:
- Ballot submissions much higher than expected.
- Rapid repeated submissions for the same election.

Actions:
1. Treat as High severity.
2. Do not conclude or tally until reviewed.
3. Review the election audit log after the election closes (or gather internal logs if you have access).
4. Consider extending the election to allow investigation without forcing closure.
5. If integrity cannot be confirmed, plan for a rerun and communicate clearly.

### 5) Admin error (wrong election extended or concluded)
Actions:
1. Stop and document what happened.
2. If you extended the wrong election:
   - Extend again to correct the end time.
   - Communicate correction if voters may have seen the wrong deadline.
3. If you concluded the wrong election:
   - Closing is irreversible due to anonymization and email scrubbing.
   - Inform stakeholders immediately.
   - Create a replacement election and rerun voting.
4. Preserve the original election and audit log for transparency.

### 6) Tally failure
Actions:
1. Confirm whether the election is `closed` or `tallied`.
2. Capture the error message.
3. Do not modify data by hand.
4. Retry tally once using the management command `advance_elections`.
5. If it continues to fail, escalate to maintainers.

## Communications templates (short)

### Voter notice: extension
"Voting for <election name> has been extended to <new end time>. We extended the deadline due to <reason>. If you already voted, you may resubmit a replacement ballot before voting closes. Your latest ballot will be counted."

### Voter notice: incident under review
"We are investigating an issue affecting <election name>. Voting remains <available/unavailable>. We will share an update by <time>."

### Stakeholder notice: integrity concern
"We identified a potential integrity issue in <election name> at <time>. We have paused close/tally operations while we investigate. Next update by <time>."
