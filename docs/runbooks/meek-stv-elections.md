# Astra Meek STV Elections: Auditor Runbook

This runbook documents how Astra election behavior is implemented in code for Meek STV elections, voter and candidate eligibility, ballot integrity, quorum, and publication.

## 1. Overview

Astra tallies elections with a Meek STV implementation (`tally_meek`) and publishes the algorithm label as `Meek STV (High-Precision Variant)` version `1.0` in tally output.[^fn1][^fn2][^fn3]

The count uses Python `Decimal` arithmetic with precision set to 80 digits in tally execution.[^fn4]

## 2. Election Lifecycle

### Creation and draft

Election workflows are exposed under `/elections/...` endpoints, including edit, vote, conclude, extend, public exports, audit log, and ballot verification.[^fn5]

Draft editing is handled by `election_edit`. Saving draft data stores/updates the election with `status = draft`.[^fn6]

### Open

Starting an election is a `start_election` action from the edit flow. On start, Astra:

- Locks the election row.
- Sets `start_datetime` to `timezone.now()`.
- Sets `status = open`.
- Issues and emails voting credentials.
- Writes a public `election_started` audit event that includes `genesis_chain_hash`.[^fn7]

Voting routes are available at `/elections/<id>/vote/` and `/elections/<id>/vote/submit.json`. The vote page returns a 410 closed template for `closed`/`tallied` elections.[^fn8][^fn9]

### Close

`election_conclude` calls `close_election` (and optionally `tally_election` unless `skip_tally` is set).[^fn10]

`close_election` requires `status == open`, sets `status = closed`, sets `end_datetime = now`, anonymizes credential/user links, and records a public `election_closed` event including `chain_head` and anonymization/scrub counters.[^fn11]

### Tally

`tally_election` requires `status == closed`, runs Meek tally, saves `tally_result`, and sets `status = tallied`.[^fn12]

During tally, Astra persists `public-ballots.json` and `public-audit.json` to election artifact file fields.[^fn13]

### Publish

Public artifact endpoints are available only for `closed`/`tallied` elections. For `tallied` elections with stored files, endpoints redirect to the file URLs; otherwise they return live JSON from service builders.[^fn14][^fn15]

## 3. Voter and Candidate Eligibility

Eligibility uses a reference datetime:

- Draft elections: `reference = max(start_datetime, now)`.
- Other statuses: `reference = start_datetime`.[^fn16]

Membership age cutoff is `reference - ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS`.[^fn17]

Eligible voting weight is built from:

- Individual memberships: vote-bearing, enabled, individual category.
- Organization memberships: vote-bearing, enabled, organization category, represented by the org representative username.
- Memberships must be active at reference time and old enough per cutoff.[^fn18][^fn19]

If `eligible_group_cn` is set on the election, eligible voters are filtered to recursive FreeIPA group members (including nested groups).[^fn20][^fn21]

Vote weight for a user is the sum of vote-bearing eligible membership lines returned by eligibility breakdown logic.[^fn22]

Candidate and nominator validation applies committee disqualification using `FREEIPA_ELECTION_COMMITTEE_GROUP`. Candidates are filtered against this, and nominations are validated separately.[^fn23][^fn24]

Candidate eligibility can be restricted by `eligible_group_cn`; nominator eligibility explicitly ignores this group restriction (`eligible_group_cn=""`).[^fn25]

## 4. Ballot Casting and Receipts

Vote submission requires:

- Authenticated user.
- Open election.
- Signed CoC.
- Rate-limit pass.
- Credential ownership check and active-membership re-check.[^fn26][^fn27][^fn28][^fn82][^fn83]

Payload parsing requires `credential_public_id` and non-empty ranking, with support for JSON ranking arrays and no-JS username-based ranking fallback mapped to candidate IDs.[^fn29]

`submit_ballot` enforces:

- Election must be open.
- Credential must exist for election.
- Ranking validation (`_MAX_RANKING_SIZE=500`, no invalid candidate IDs, no duplicates).[^fn30][^fn31]

Ballot receipt hash input includes `(election_id, credential_public_id, ranking, weight, nonce)` serialized as canonical JSON (`sort_keys=True`, compact separators) then SHA-256.[^fn32][^fn33]

`nonce` is random (`token_hex(16)`), used in hash generation, and intentionally not stored.[^fn34]

Ballot chaining uses previous chain head (or election genesis) and stores both `previous_chain_hash` and `chain_hash` on ballot rows.[^fn35]

If a credential re-votes, Astra supersedes prior final ballot by pointer flips so only one final counted ballot remains for that credential.[^fn36][^fn37]

Submission writes a private `ballot_submitted` audit event and returns receipt fields including `ballot_hash`, `nonce`, `previous_chain_hash`, `chain_hash`, and `email_queued`.[^fn38][^fn39]

Vote-receipt emails include the same receipt data plus a ballot verification URL.[^fn40]

Astra receipts are commitment-style proofs, not plaintext vote disclosures. The receipt email does not include ranking.[^fn86] The receipt hash commits to the full ranking together with election ID, credential ID, weight, and nonce. Therefore, a receipt alone does not prove ballot ranking to a third party. However, if a voter voluntarily discloses their ranking, credential ID, and nonce, a third party can recompute the hash and confirm consistency with the receipt. The system does not provide coercion resistance: receipts can be used by a voter to prove their own vote if they choose to disclose those inputs.[^fn87]

## 4A. Voting Credential Lifecycle

Voting credentials are not single-use tokens. Astra permits re-voting with the same credential; each new submission supersedes the prior final ballot for that credential, and only one final counted ballot remains per `(election, credential_public_id)` at any time.[^fn36][^fn88]

Credential IDs (`VotingCredential.public_id`) are URL-safe random strings (`secrets.token_urlsafe(32)`) scoped to a specific election.[^fn89][^fn90] On the web vote path, the authenticated user must own a credential row for the election and the submitted `credential_public_id` must pass a constant-time comparison against that row.[^fn82][^fn83]

A dedicated credential revocation state machine (e.g. `revoked`/`consumed` states) is not present in this implementation. Effective vote blocking occurs when a user loses their credential row, has a non-positive credential weight, or no longer holds an active vote-bearing membership.[^fn91]

Credential issuance is idempotent by `(election, username)`: existing credentials are returned and have their weight updated if it changed; new credentials are only created when none exists. Credential re-send is an explicit admin action (re-invoking issuance and email delivery).[^fn92][^fn93]

After close-time anonymization, `VotingCredential.freeipa_username` is nulled for all credentials associated with the election. The credential row itself is retained (to preserve the chain of custody for the credential public ID), but the username linkage is removed.[^fn94]

## 4B. Ballot Privacy Model

Before close-time anonymization, a ballot row can be linked to a voter identity at the database level through the join chain: `Ballot.credential_public_id → VotingCredential.public_id → VotingCredential.freeipa_username`.[^fn95][^fn96] This linkage is accessible to anyone with direct database access (e.g. a DBA) or application-level admin access to credential rows during the active voting window. Ballot content (ranking) is not exposed through established Django admin interfaces for the `Ballot` model.[^fn97]

At close/tally time, Astra performs anonymization: `VotingCredential.freeipa_username` is set to `NULL` for all election credentials, and election-linked credential and vote-receipt emails are scrubbed from the mail queue.[^fn98][^fn99] Ballot rows themselves are not rewritten; they retain ranking, weight, receipt hash, chain hashes, and timestamp.

The post-anonymization model is pseudonymous: credential IDs remain in ballot rows, and a `VotingCredential` row still exists per credential, but the direct username-to-credential mapping for that election is removed. Independent correlation of a credential ID back to a specific voter is no longer possible through the application's credential table after anonymization.

**System trust boundary:** ballot secrecy during the open voting window depends on access controls to the application database and credential table. This is an operational control, not a cryptographic one. The system does not implement cryptographic ballot unlinkability at submission time.

## 5. Ballot Chain Integrity

Genesis chain hash is election-specific (`sha256("election:<id>..."`), preventing cross-election chain splicing from a shared genesis.[^fn41]

Next chain hash is `sha256(f"{previous_chain_hash}:{ballot_hash}")`.[^fn42]

`public-ballots.json` includes per-ballot `ballot_hash`, `previous_chain_hash`, `chain_hash`, `is_counted`, `superseded_by`, and export-level `chain_head`.[^fn43]

At close, Astra also records `chain_head` in public close event payload.[^fn44]

The published verification scripts implement local checks:

- `verify-ballot-hash.py`: recomputes ballot hash from receipt inputs with the same payload and SHA-256 method as model code.[^fn45]
- `verify-ballot-chain.py`: reconstructs chain from genesis, verifies per-row hash links, and fails on fork, cycle, disconnected graph, missing genesis linkage, or head mismatch.[^fn46]

## 6. Quorum Rules

Election model stores quorum as a percentage (`0..100`) with default `10`.[^fn47]

Quorum status calculation:

- Draft: eligible counts from current membership eligibility.
- Non-draft: eligible counts from issued voting credentials snapshot (`weight>0`).
- Participation from final ballots count and summed weight.[^fn48]

Required participation thresholds use integer ceil math for both voter count and vote weight:

- `required_voters = ceil(eligible_voters * quorum_percent / 100)`
- `required_weight = ceil(eligible_weight * quorum_percent / 100)`

and `quorum_met` is true only if both required thresholds are non-zero and both participation metrics meet/exceed thresholds.[^fn49]

After each ballot commit, Astra reevaluates quorum and records a public `quorum_reached` event once conditions are met.[^fn50]

If quorum is not met, closure is not blocked in `close_election`; close checks election status only. Extension is a separate explicit action (`extend_election_end_datetime`) that validates a later/future end time and emits `election_end_extended`.[^fn51][^fn76][^fn84][^fn85]

## 7. The Meek STV Count

### a) Droop quota formula

Quota is computed as:

`quota = floor(total_weight / (seats + 1)) + 1`

implemented with Decimal round-down (`ROUND_DOWN`).[^fn52]

### b) Keeping multipliers

Retention factors start at `1` for all candidates. For elected continuing candidates, each round updates `new_r = quota / incoming`, then clamps to `[0, 1]`.[^fn53]

### c) Computing vote totals

Ballot distribution tracks:

- `incoming[cid] += remaining` when candidate appears in continuing ranking path.
- `portion = remaining * retention[cid]` counted as retained.
- `remaining -= portion` passed to later preferences.[^fn54]

### d) Surplus distribution

Surplus transfer is implicit via retention: elected candidates keep only `quota/incoming` fraction; unretained value continues to lower preferences in ranking order.[^fn55]

### e) Candidate election

Candidates are newly elected when `retained_totals[cid] >= quota - epsilon`. If more candidates meet quota than remaining seats, Astra elects only up to remaining seats in deterministic order.[^fn56]

### f) Exclusion rules

When exclusion-group max is reached, unelected members of that group are force-removed from continuing set, retention is set to 0, and exclusion metadata is recorded (`reason = exclusion_group_max_reached`).[^fn57]

### g) Tie-breaking

Astra tie-breaking is deterministic and rule-ordered. For tied candidate sets, the implementation applies rules in sequence (`_tie_break_rules_trace`):[^fn58]

1. Prior-round retained totals.
2. Current support totals.
3. First-preference votes.
4. Fixed candidate ordering identifier (`tiebreak_uuid`) — a stable UUID set at candidate creation time with `editable=False`.

Rule direction is explicit: election ordering uses highest-first (`prefer_highest=True`); elimination uses lowest-first (`prefer_highest=False`).[^fn59]

`tiebreak_uuid` values are per-candidate (`Candidate.tiebreak_uuid`, unique within an election) and are passed into the tally as candidate input data.[^fn100][^fn101] Tie-break decisions are emitted in each round's public tally artifact as `tie_breaks` with per-rule `rule_trace` values, published via `tally_round` events in `public-audit.json`.[^fn67] This means any tie-breaking that actually occurs can be independently verified from the published artifacts alone.

### h) Termination conditions

The count loop stops when any applicable condition is met:

- Seats filled.
- No eligible candidates remain.
- Remaining eligible candidates exactly fill remaining seats.
- Numeric convergence (`max_retention_delta < epsilon` and no new elections/forced exclusions) followed by elimination loop as needed.
- Failure to converge within `max_iterations` raises an error.[^fn60]

### i) Special edge cases

Guardrails enforce bounds (seats, ballots, candidates, epsilon, max_iterations, candidate IDs, tiebreak UUID presence).[^fn61]

If candidate list is empty, tally raises `ValueError("must have at least one candidate")`.[^fn62]

If remaining candidates exactly equal remaining seats, they are elected immediately (deterministically).[^fn63]

If no candidates remain eligible while seats remain, count completion allows seats to remain vacant (`is_count_complete` logic).[^fn64]

### j) Algorithm Reference Boundaries

In code metadata, Astra identifies its tally implementation as `Meek STV (High-Precision Variant)` version `1.0`.[^fn1][^fn2] The implementation is documented via internal architecture artifacts and code docstrings covering quota, retention updates, convergence conditions, exclusion handling, and tie-breaking rules.

As of this commit, the code does not cite an external formal Meek STV specification text directly.[^fn4] Auditors should treat Astra's internal architecture documentation, the published `public-audit.json` round artifacts, and the tally implementation source as the normative reference for this deployment.

Convergence parameters: `epsilon` (default `1e-28`, used without override in standard tally calls) and `max_iterations` (default `200`) are defined in the tally function and are not currently exposed in published artifacts. A forthcoming hardening item will publish these in `public-audit.json` so independent verifiers can reproduce convergence behavior exactly.[^fn4][^fn60]

## 8. Audit Report

Audit records are stored in `AuditLogEntry` with `event_type`, JSON `payload`, timestamp, and `is_public` visibility flag.[^fn65]

Observed election event emission in code includes:

- Public: `election_started`, `quorum_reached`, `election_end_extended`, `election_closed`, `tally_round`, `tally_completed`.
- Private: `ballot_submitted`, `election_anonymized`, `election_close_failed`, `tally_failed`.[^fn7][^fn38][^fn50][^fn66][^fn77][^fn78][^fn79][^fn80][^fn81][^fn85]

`tally_round` payload includes machine-verifiable round artifacts such as retention factors, retained totals, tie-break traces, exclusions, and generated audit/summary text.[^fn67]

Public audit export contains only `is_public=True` events, strips `actor` from payload, and exports timestamps as dates (`YYYY-MM-DD`).[^fn68]

The web audit-log page is available only after close/tally; non-managers see only public entries, while managers can also view grouped ballot-submission summaries.[^fn69]

## 9. Public Verification

Published verification surfaces:

- `GET /elections/<id>/public/ballots.json`
- `GET /elections/<id>/public/audit.json`
- `GET /elections/ballot/verify/?receipt=<hash>`
- `GET /elections/<id>/audit/`[^fn70]

Ballot verify accepts 64-char hex receipts (case-insensitive; normalized to lowercase before matching) and is rate-limited. It reports whether the receipt hash exists, whether it is superseded/final, and links to public artifacts for tallied elections.[^fn71]

Voters can independently verify:

1. Receipt hash recomputation with `verify-ballot-hash.py`.
2. Inclusion and chain consistency with `verify-ballot-chain.py` against published ballots + chain head.[^fn72]

## 10. Key Settings Reference

| Setting | Default | Effect on elections |
|---|---|---|
| `ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS` | `0` in DEBUG, else `60` | Minimum membership age for vote eligibility cutoff. |
| `ELECTION_FREEIPA_CIRCUIT_BREAKER_SECONDS` | `30` | Election FreeIPA circuit-breaker duration setting (seconds). |
| `ELECTION_RATE_LIMIT_BALLOT_VERIFY_LIMIT` | `30` | Max ballot-verify requests per verify window. |
| `ELECTION_RATE_LIMIT_BALLOT_VERIFY_WINDOW_SECONDS` | `60` | Window for ballot-verify rate limiting. |
| `ELECTION_RATE_LIMIT_VOTE_SUBMIT_LIMIT` | `20` | Max vote submissions per user/election within submit window. |
| `ELECTION_RATE_LIMIT_VOTE_SUBMIT_WINDOW_SECONDS` | `3600` | Vote-submit rate-limit window. |
| `ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_LIMIT` | `10` | Max credential-resend operations per admin/election within resend window. |
| `ELECTION_RATE_LIMIT_CREDENTIAL_RESEND_WINDOW_SECONDS` | `600` | Credential-resend rate-limit window. |
| `ELECTION_COMMITTEE_EMAIL` | `elections@almalinux.org` | Reply-to and committee contact in election emails. |
| `ELECTION_VOTING_CREDENTIAL_EMAIL_TEMPLATE_NAME` | `election-voting-credential` | Default template name for credential emails. |
| `ELECTION_VOTE_RECEIPT_EMAIL_TEMPLATE_NAME` | `election-vote-receipt` | Default template name for vote receipt emails. |
| `FREEIPA_ELECTION_COMMITTEE_GROUP` | `election-committee` | Group used to disqualify committee members from candidacy/nomination. |

Settings defaults are defined in Django settings.[^fn73]

## 11. Security Guarantees and Trust Boundaries

### What Astra provides

- **Tally integrity:** The Meek STV count is deterministic given the published ballot set, candidate tie-break UUIDs, and convergence parameters. Tally round artifacts in `public-audit.json` document every round.
- **Inclusion verifiability:** Each voter receives a receipt hash they can use to confirm their ballot appears in `public-ballots.json`.
- **Chain integrity:** The ballot hash chain from genesis to chain head is independently verifiable using the published `verify-ballot-chain.py` script.
- **Post-election auditability:** Public artifacts (`public-ballots.json`, `public-audit.json`) are generated at tally and allow third-party review of the count, ballot set, and anonymized voter weights.
- **Pseudonymization at close:** Direct username-to-ballot linkage is removed at close time.

### What Astra does NOT provide

- **Coercion resistance:** Ballot receipts commit to the voter's ranking. A voter can use their receipt inputs (ballot hash + credential ID + nonce + ranking) to prove to a third party how they voted. The system does not implement mechanisms to deny or obscure a submitted ranking.
- **Pre-close ballot secrecy from infrastructure operators:** During the active voting window, ballot-to-voter links exist in the database and are accessible to parties with database or application admin access. Secrecy during this window is an operational control, not a cryptographic guarantee.
- **Protection from malicious operators with full DB/app access:** The chain and audit log provide tamper evidence after the fact, but do not prevent a party with full database write access from altering history before publication.
- **External timestamp anchoring:** The chain head is published but not anchored to an external immutable timestamping authority. Chain integrity claims are verifiable post-publication but rely on operator honesty prior to publication.

## Auditor Notes

- There is no distinct `published` status in the election model. Publication behavior is derived from `closed`/`tallied` state plus artifact generation and public routes.[^fn74]
- Election retrieval helpers and list/detail views use `Election.objects.active()` which excludes soft-deleted elections (`status="deleted"`).[^fn75]
- The `public-ballots.json` export includes **all** ballots—including superseded (re-voted) and un-counted ones—marked via the `is_counted` field. The chain is not limited to final counted ballots.[^fn43]
- The ballot-verify endpoint intentionally does not reveal ranking, voter IP addresses, or precise timestamps; it exposes only a submission date. This is a deliberate privacy guardrail.[^fn71]
- Rate-limit scoping: vote submission is scoped per (election, username); ballot verification is scoped per client IP address.[^fn50][^fn71]
- Empty-election chain head: if no ballots are cast, the chain head equals the election genesis hash. Independent verifiers should account for this case when no ballot rows are present.[^fn41]
- Quorum and closure: the election model's `quorum_percent` help text implies quorum affects whether an election can be concluded, but `close_election` does not block when quorum is unmet. Quorum is informational/tracked, and extension is a separate explicit action. Auditors should verify participation independently via the `public-audit.json` `quorum_reached` event (or its absence).[^fn49][^fn51]

[^fn1]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L38
[^fn2]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L832
[^fn3]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L889
[^fn4]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L607
[^fn5]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/urls.py#L68
[^fn6]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/edit.py#L716
[^fn7]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/edit.py#L467
[^fn8]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/urls.py#L88
[^fn9]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L203
[^fn10]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/lifecycle.py#L137
[^fn11]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L784
[^fn12]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L840
[^fn13]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L264 / https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L901
[^fn14]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/audit.py#L39
[^fn15]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/audit.py#L48
[^fn16]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L69
[^fn17]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L98
[^fn18]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L106
[^fn19]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L129
[^fn20]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L305
[^fn21]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L268
[^fn22]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L332 / https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L357
[^fn23]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L460
[^fn24]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/config/settings.py#L831
[^fn25]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_eligibility.py#L537
[^fn26]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L87
[^fn27]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L99
[^fn28]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L108
[^fn29]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L32
[^fn30]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L528
[^fn31]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L282
[^fn32]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1416
[^fn33]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/static/verify-ballot-hash.py#L58
[^fn34]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L545
[^fn35]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L555
[^fn36]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L569
[^fn37]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1397
[^fn38]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L615
[^fn39]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L191
[^fn40]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L343
[^fn41]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/tokens.py#L25
[^fn42]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/tokens.py#L47
[^fn43]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L181
[^fn44]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L791
[^fn45]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/static/verify-ballot-hash.py#L65
[^fn46]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/static/verify-ballot-chain.py#L47
[^fn47]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1134
[^fn48]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L469
[^fn49]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L499
[^fn50]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L623
[^fn51]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L777
[^fn52]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L611
[^fn53]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L613
[^fn54]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L58
[^fn55]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L958
[^fn56]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L912
[^fn57]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L773
[^fn58]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L681
[^fn59]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L1068
[^fn60]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L968
[^fn61]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L531
[^fn62]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L541
[^fn63]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L1011
[^fn64]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L626
[^fn65]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1437
[^fn66]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L800
[^fn67]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_meek.py#L834
[^fn68]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L243
[^fn69]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/audit.py#L84
[^fn70]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/urls.py#L105
[^fn71]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/ballot_verify.py#L18 / https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/ballot_verify.py#L26
[^fn72]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/static/verify-ballot-chain.py#L147
[^fn73]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/config/settings.py#L448
[^fn74]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1121
[^fn75]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1109
[^fn76]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L84
[^fn77]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L722
[^fn78]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L906
[^fn79]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L927
[^fn80]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L817
[^fn81]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L946
[^fn82]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L132
[^fn83]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L136
[^fn84]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L86
[^fn85]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L103
[^fn86]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L372
[^fn87]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/static/verify-ballot-hash.py#L49
[^fn88]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1397
[^fn89]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1270
[^fn90]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1269
[^fn91]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/views_elections/vote.py#L122
[^fn92]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L657
[^fn93]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/admin.py#L1516
[^fn94]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L707
[^fn95]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1372
[^fn96]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1273
[^fn97]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/admin.py#L1502
[^fn98]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L705
[^fn99]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L755
[^fn100]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/models.py#L1199
[^fn101]: https://github.com/AlmaLinux/astra/blob/089549a726552204126dca498f5f78ee56ca5e40/astra_app/core/elections_services.py#L848
