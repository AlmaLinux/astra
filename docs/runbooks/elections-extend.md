# Elections runbook: extend an election

## Purpose
Extend the end time of an open election.

Extending the election changes the voting deadline. It does not alter recorded ballots. It is intended for cases like unmet quorum, system outages, or major voter access problems.

## Prerequisites
- You have election admin permissions in Astra.
- The election status is `open`.
- You have a reason for extension that you can communicate publicly.
- You have decided on a new end datetime (timezone and duration).

## Safety notes
- Extension changes the election end datetime. It does not send automatic notifications by itself.
- You should notify voters through your agreed communication channel after extending.

## Procedure

### 1) Decide whether to extend
1. Review participation so far (quorum status and vote counts).
2. Check for active incidents:
   - Email delivery problems
   - FreeIPA or login outages
   - Reports of credential leakage or phishing
3. Decide the new end time:
   - Choose a clear, round deadline when possible.
   - Avoid very short extensions unless there is a strong reason.

### 2) Extend the election in the UI
1. Open the election detail page.
2. Click `Extend Election`.
3. In the modal:
   - Set the new end datetime.
   - Confirm it is later than the current end.
   - Complete the typed confirmation requirement (type the election ID or name).
4. Submit.
5. Confirm you see "Election end date extended.".

### 3) Notify voters
Because extension does not automatically email voters, send an announcement immediately:
- Include the election name and new end time.
- Explain why the election was extended.
- Provide the voting URL.

If you want to remind individual voters, you can also use the election page action to resend credentials.

### 4) Verify
1. Refresh the election detail page.
2. Confirm the voting window displays the new end time.

## Rollback or abort guidance
- If you extended to the wrong time, extend again to the correct time.
- Do not attempt to shorten the election by editing the database directly.

If an extension is controversial or disputed:
1. Document the reason and decision process.
2. Communicate the rationale transparently.
3. If needed, involve governance stakeholders for approval.

## Failure handling

### A) UI says only open elections can be extended
Actions:
1. Confirm the election status. If it is `closed` or `tallied`, you cannot extend.
2. If you closed by mistake, follow the close runbook rollback guidance (a new election is usually required).

### B) Validation errors about the end datetime
Actions:
1. Ensure the new end is later than the current end.
2. Ensure the datetime value is valid for the browser and server timezone handling.
3. Retry.

### C) Extension succeeded but voters did not notice
Actions:
1. Send a reminder communication.
2. Consider resending credentials for users who report issues.
