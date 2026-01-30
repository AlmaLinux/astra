from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import patch

from django.test import TestCase

from core.backends import FreeIPAFASAgreement


@dataclass(slots=True)
class _DummyClient:
    calls: list[tuple[str, list[object], dict[str, object]]]
    agreement_users: set[str]
    agreement_groups: set[str]

    def _request(self, method: str, args: list[object], params: dict[str, object]) -> dict[str, object]:
        self.calls.append((method, list(args), dict(params)))

        if method == "fasagreement_add_user":
            user = str(params.get("user") or params.get("users") or "").strip()
            if user:
                self.agreement_users.add(user)
            return {"result": {"ok": True}}

        if method == "fasagreement_add_group":
            group = str(params.get("group") or params.get("groups") or "").strip()
            if group:
                self.agreement_groups.add(group)
            return {"result": {"ok": True}}

        if method == "fasagreement_remove_group":
            group = str(params.get("group") or params.get("groups") or "").strip()
            if group:
                self.agreement_groups.discard(group)
            return {"result": {"ok": True}}

        if method == "fasagreement_show":
            cn = str(args[0])
            return {
                "result": {
                    "cn": [cn],
                    "ipaenabledflag": ["TRUE"],
                    "memberuser_user": sorted(self.agreement_users),
                    "member_group": sorted(self.agreement_groups),
                    "description": [""],
                }
            }

        raise AssertionError(f"Unexpected method: {method}")


class FreeIPAFASAgreementAddUserPersistsTests(TestCase):
    def test_add_user_calls_freeipa_with_scalar_user_and_verifies_persistence(self) -> None:
        client = _DummyClient(calls=[], agreement_users=set(), agreement_groups=set())
        agreement = FreeIPAFASAgreement("FPCA", {"cn": ["FPCA"], "ipaenabledflag": ["TRUE"], "memberuser_user": []})

        with patch.object(FreeIPAFASAgreement, "get_client", return_value=client):
            agreement.add_user("alice")

        # Must call the FreeIPA plugin command with a *scalar* user option
        # (Noggin does this; some servers do not accept a list here).
        assert ("fasagreement_add_user", ["FPCA"], {"user": "alice"}) in client.calls

        # Must re-fetch the agreement and ensure the user is present so we don't
        # report success when the change was ignored server-side.
        methods = [m for (m, _args, _params) in client.calls]
        assert "fasagreement_show" in methods

    def test_add_and_remove_group_calls_freeipa_with_scalar_group_and_verifies_persistence(self) -> None:
        client = _DummyClient(calls=[], agreement_users=set(), agreement_groups=set())
        agreement = FreeIPAFASAgreement(
            "FPCA",
            {"cn": ["FPCA"], "ipaenabledflag": ["TRUE"], "member_group": [], "memberuser_user": []},
        )

        with patch.object(FreeIPAFASAgreement, "get_client", return_value=client):
            agreement.add_group("developers")
            agreement.remove_group("developers")

        assert ("fasagreement_add_group", ["FPCA"], {"group": "developers"}) in client.calls
        assert ("fasagreement_remove_group", ["FPCA"], {"group": "developers"}) in client.calls

        methods = [m for (m, _args, _params) in client.calls]
        # Both operations should verify by re-fetching the agreement.
        assert methods.count("fasagreement_show") >= 2
