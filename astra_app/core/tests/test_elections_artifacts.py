
import datetime
import json
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import elections_services
from core.election_chain import build_config_manifest, config_manifest_sha256
from core.models import AuditLogEntry, Ballot, Candidate, Election
from core.tests.ballot_chain import compute_chain_hash
from core.tokens import election_chain_anchor_hash, election_genesis_chain_hash


class ElectionArtifactGenerationTests(TestCase):
    def _create_coherent_v2_election(
        self,
        *,
        status: str = Election.Status.tallied,
        include_ballot: bool = False,
    ) -> tuple[Election, Candidate, str, str]:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact v2 election",
            description="",
            url="https://example.com/elections/artifact-v2",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            quorum=10,
            eligible_group_cn="voters",
            status=Election.Status.draft,
            chain_version=2,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )
        manifest = build_config_manifest(election=election)
        manifest_digest = config_manifest_sha256(manifest)
        anchor_hash = election_chain_anchor_hash(
            election_id=election.id,
            config_manifest_sha256=manifest_digest,
        )
        Election.objects.filter(pk=election.pk).update(
            status=status,
            config_manifest_version=1,
            config_manifest=manifest,
            config_manifest_sha256=manifest_digest,
            chain_anchor_hash=anchor_hash,
        )
        election.refresh_from_db()
        AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "chain_version": 2,
                "config_manifest_version": 1,
                "config_manifest_sha256": manifest_digest,
                "chain_anchor_hash": anchor_hash,
                "config_manifest": manifest,
            },
            is_public=True,
        )
        if include_ballot:
            ballot_hash = Ballot.compute_hash(
                election_id=election.id,
                credential_public_id="cred-1",
                ranking=[candidate.id],
                weight=1,
                nonce="0" * 32,
            )
            chain_hash = compute_chain_hash(previous_chain_hash=anchor_hash, ballot_hash=ballot_hash)
            Ballot.objects.create(
                election=election,
                credential_public_id="cred-1",
                ranking=[candidate.id],
                weight=1,
                ballot_hash=ballot_hash,
                previous_chain_hash=anchor_hash,
                chain_hash=chain_hash,
                is_counted=True,
            )
        return election, candidate, manifest_digest, anchor_hash

    def test_build_public_ballots_export_uses_candidate_usernames_in_rankings(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact election (ranking mapping)",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
            chain_version=1,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        payload = elections_services.build_public_ballots_export(election=election)
        self.assertEqual(payload["ballots"][0]["ranking"], ["alice"])

    def test_tally_generates_public_ballots_and_audit_artifacts(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
            chain_version=1,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()

        self.assertEqual(election.status, Election.Status.tallied)
        self.assertTrue(str(election.public_ballots_file.name or "").strip())
        self.assertTrue(str(election.public_audit_file.name or "").strip())
        self.assertIn(f"elections/{election.id}/", election.public_ballots_file.name)
        self.assertIn(f"elections/{election.id}/", election.public_audit_file.name)

    def test_tally_persists_public_audit_artifact_with_public_tally_events(self) -> None:
        election, _candidate, _manifest_digest, _anchor_hash = self._create_coherent_v2_election(
            status=Election.Status.closed,
            include_ballot=True,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()

        with election.public_audit_file.open("rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))

        event_types = [event["event_type"] for event in payload["audit_log"]]
        self.assertEqual(event_types[0], "election_started")
        self.assertIn("tally_round", event_types)
        self.assertIn("tally_completed", event_types)

    @override_settings(
        ELECTION_REKOR_ENDPOINT="https://rekor.example",
        ELECTION_REKOR_SIGNING_KEY="-----BEGIN PRIVATE KEY-----fake",
    )
    def test_tally_persists_public_audit_artifact_with_tally_completed_timestamping(self) -> None:
        election, _candidate, _manifest_digest, _anchor_hash = self._create_coherent_v2_election(
            status=Election.Status.closed,
            include_ballot=True,
        )

        def _fake_attest_entry(entry: AuditLogEntry) -> None:
            entry.rekor_log_id = "uuid-tally-completed"
            entry.rekor_endpoint = "https://rekor.example"
            entry.rekor_log_index = 7
            entry.rekor_message_digest_hex = "a" * 64
            entry.rekor_canonical_message_version = 1
            entry.rekor_integrated_time = timezone.now()
            entry.save(
                update_fields=[
                    "rekor_log_id",
                    "rekor_endpoint",
                    "rekor_log_index",
                    "rekor_message_digest_hex",
                    "rekor_canonical_message_version",
                    "rekor_integrated_time",
                ]
            )

        with patch("core.elections_timestamping._attest_entry", side_effect=_fake_attest_entry):
            with self.captureOnCommitCallbacks(execute=True):
                elections_services.tally_election(election=election)

        election.refresh_from_db()

        with election.public_audit_file.open("rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))

        tally_completed_event = next(
            event for event in payload["audit_log"] if event["event_type"] == "tally_completed"
        )
        self.assertEqual(tally_completed_event["timestamping"]["rekor_log_id"], "uuid-tally-completed")
        self.assertEqual(tally_completed_event["timestamping"]["rekor_log_index"], 7)

    def test_public_export_endpoints_redirect_to_stored_artifacts_when_tallied(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact endpoints election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
            chain_version=1,
        )
        c1 = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[c1.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        elections_services.tally_election(election=election)
        election.refresh_from_db()

        ballots_resp = self.client.get(reverse("election-public-ballots", args=[election.id]))
        self.assertEqual(ballots_resp.status_code, 302)
        self.assertIn(f"/elections/{election.id}/", str(ballots_resp["Location"]))

        audit_resp = self.client.get(reverse("election-public-audit", args=[election.id]))
        self.assertEqual(audit_resp.status_code, 302)
        self.assertIn(f"/elections/{election.id}/", str(audit_resp["Location"]))

    def test_persisted_public_audit_artifact_hides_sensitive_close_counts(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact redaction election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            chain_version=1,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_closed",
            payload={
                "chain_head": "d" * 64,
                "credentials_affected": 5,
                "emails_scrubbed": 4,
            },
            is_public=True,
        )

        elections_services.persist_public_election_artifacts(election=election)
        election.refresh_from_db()

        with election.public_audit_file.open("rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))

        self.assertEqual(payload["audit_log"][0]["event_type"], "election_closed")
        self.assertEqual(payload["audit_log"][0]["payload"].get("chain_head"), "d" * 64)
        self.assertNotIn("credentials_affected", payload["audit_log"][0]["payload"])
        self.assertNotIn("emails_scrubbed", payload["audit_log"][0]["payload"])

    def test_persisted_public_audit_artifact_hides_start_operational_fields(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact start redaction election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            chain_version=1,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="election_started",
            payload={
                "actor": "admin",
                "eligible_voters": 12,
                "emailed": 10,
                "skipped": 1,
                "failures": 1,
                "genesis_chain_hash": "e" * 64,
                "candidates": [
                    {
                        "id": 1,
                        "freeipa_username": "alice",
                        "tiebreak_uuid": "00000000-0000-0000-0000-000000000001",
                    },
                ],
            },
            is_public=True,
        )

        elections_services.persist_public_election_artifacts(election=election)
        election.refresh_from_db()

        with election.public_audit_file.open("rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))

        self.assertEqual(payload["audit_log"][0]["event_type"], "election_started")
        self.assertEqual(
            payload["audit_log"][0]["payload"],
            {
                "genesis_chain_hash": "e" * 64,
                "candidates": [
                    {
                        "id": 1,
                        "freeipa_username": "alice",
                        "tiebreak_uuid": "00000000-0000-0000-0000-000000000001",
                    },
                ],
            },
        )
        self.assertNotIn("actor", json.dumps(payload))
        self.assertNotIn("eligible_voters", json.dumps(payload))
        self.assertNotIn("failures", json.dumps(payload))

    def test_persisted_public_audit_artifact_hides_rekor_failure_error_type(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact rekor redaction election",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.tallied,
            chain_version=1,
        )

        AuditLogEntry.objects.create(
            election=election,
            event_type="rekor_attestation_failed",
            payload={"error_type": "ConnectionError"},
            is_public=True,
        )

        elections_services.persist_public_election_artifacts(election=election)
        election.refresh_from_db()

        with election.public_audit_file.open("rb") as fh:
            payload = json.loads(fh.read().decode("utf-8"))

        self.assertEqual(payload["audit_log"][0]["event_type"], "rekor_attestation_failed")
        self.assertEqual(payload["audit_log"][0]["payload"], {})
        self.assertNotIn("ConnectionError", json.dumps(payload))

    def test_persist_public_election_artifacts_sets_shared_publication_bundle_metadata(self) -> None:
        election, _candidate, _manifest_digest, _anchor_hash = self._create_coherent_v2_election(
            include_ballot=True,
        )
        published_at = timezone.make_aware(datetime.datetime(2026, 4, 11, 10, 15, 0))

        with patch("django.utils.timezone.now", return_value=published_at):
            elections_services.persist_public_election_artifacts(election=election)

        election.refresh_from_db()
        self.assertEqual(election.artifacts_generated_at, published_at)

        with election.public_ballots_file.open("rb") as fh:
            ballots_payload = json.loads(fh.read().decode("utf-8"))
        with election.public_audit_file.open("rb") as fh:
            audit_payload = json.loads(fh.read().decode("utf-8"))

        expected_bundle = {"published_at": "2026-04-11T10:15:00Z"}
        self.assertEqual(ballots_payload["publication_bundle"], expected_bundle)
        self.assertEqual(audit_payload["publication_bundle"], expected_bundle)

    def test_build_public_audit_export_includes_v2_root_metadata(self) -> None:
        election, _candidate, manifest_digest, anchor_hash = self._create_coherent_v2_election()

        payload = elections_services.build_public_audit_export(election=election)

        self.assertEqual(payload["chain_version"], 2)
        self.assertEqual(payload["chain_root_kind"], "config_anchor_v2")
        self.assertNotIn("chain_root_hash", payload)
        self.assertNotIn("chain_anchor_hash", payload)
        self.assertEqual(payload["genesis_hash"], anchor_hash)
        self.assertEqual(payload["config_manifest_sha256"], manifest_digest)
        self.assertEqual(payload["audit_log"][0]["payload"]["config_manifest_sha256"], manifest_digest)
        self.assertNotIn("chain_root_hash", payload["audit_log"][0]["payload"])

    def test_build_public_ballots_export_uses_genesis_hash_as_only_top_level_origin_for_v1(self) -> None:
        now = timezone.now()
        election = Election.objects.create(
            name="Artifact election (v1 origin metadata)",
            description="",
            start_datetime=now - datetime.timedelta(days=2),
            end_datetime=now - datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.closed,
            chain_version=1,
        )
        candidate = Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="nominator",
        )

        genesis_hash = election_genesis_chain_hash(election.id)
        ballot_hash = Ballot.compute_hash(
            election_id=election.id,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            nonce="0" * 32,
        )
        chain_hash = compute_chain_hash(previous_chain_hash=genesis_hash, ballot_hash=ballot_hash)
        Ballot.objects.create(
            election=election,
            credential_public_id="cred-1",
            ranking=[candidate.id],
            weight=1,
            ballot_hash=ballot_hash,
            previous_chain_hash=genesis_hash,
            chain_hash=chain_hash,
        )

        payload = elections_services.build_public_ballots_export(election=election)

        self.assertEqual(payload["chain_version"], 1)
        self.assertEqual(payload["chain_root_kind"], "legacy_genesis")
        self.assertEqual(payload["genesis_hash"], genesis_hash)
        self.assertNotIn("chain_root_hash", payload)

    def test_persist_public_election_artifacts_rejects_inconsistent_v2_manifest_state(self) -> None:
        for mutation in ("manifest", "digest", "anchor"):
            election, _candidate, manifest_digest, anchor_hash = self._create_coherent_v2_election(
                include_ballot=True,
            )

            if mutation == "manifest":
                tampered_manifest = json.loads(json.dumps(election.config_manifest))
                tampered_manifest["election"]["name"] = "Tampered"
                Election.objects.filter(pk=election.pk).update(config_manifest=tampered_manifest)
            elif mutation == "digest":
                Election.objects.filter(pk=election.pk).update(config_manifest_sha256="0" * 64)
            else:
                self.assertNotEqual(anchor_hash, "f" * 64)
                Election.objects.filter(pk=election.pk).update(chain_anchor_hash="f" * 64)

            election.refresh_from_db()
            with self.subTest(mutation=mutation):
                with self.assertRaises(ValueError):
                    elections_services.persist_public_election_artifacts(election=election)

            # Ensure the helper values are actually coherent before tampering to avoid false positives.
            self.assertEqual(len(manifest_digest), 64)

    def test_build_public_ballots_export_rejects_v2_manifest_missing_ranked_candidate(self) -> None:
        election, candidate, _manifest_digest, _anchor_hash = self._create_coherent_v2_election(
            status=Election.Status.closed,
            include_ballot=True,
        )
        tampered_manifest = json.loads(json.dumps(election.config_manifest))
        tampered_manifest["candidates"] = []
        tampered_digest = config_manifest_sha256(tampered_manifest)
        tampered_anchor = election_chain_anchor_hash(
            election_id=election.id,
            config_manifest_sha256=tampered_digest,
        )
        Election.objects.filter(pk=election.pk).update(
            config_manifest=tampered_manifest,
            config_manifest_sha256=tampered_digest,
            chain_anchor_hash=tampered_anchor,
        )
        election.refresh_from_db()

        with self.assertRaisesRegex(ValueError, str(candidate.id)):
            elections_services.build_public_ballots_export(election=election)
