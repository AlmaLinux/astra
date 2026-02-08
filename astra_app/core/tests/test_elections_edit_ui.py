
import datetime
import re
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.backends import FreeIPAGroup, FreeIPAMisconfiguredError, FreeIPAUser
from core.models import (
    Candidate,
    Election,
    ExclusionGroup,
    ExclusionGroupCandidate,
    FreeIPAPermissionGrant,
    Membership,
    MembershipType,
)
from core.permissions import ASTRA_ADD_ELECTION


class ElectionsListNewElectionButtonTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_elections_list_hides_new_button_without_permission(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "displayname": ["Viewer User"], "memberof_group": []})

        now = timezone.now()
        Election.objects.create(
            name="Some election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(reverse("elections"))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, reverse("election-edit", args=[0]))

    def test_elections_list_shows_new_button_with_permission(self) -> None:
        self._login_as_freeipa_user("admin")

        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        Election.objects.create(
            name="Some election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )

        resp = self.client.get(reverse("elections"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("election-edit", args=[0]))


class ElectionEditPermissionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_new_election_requires_permission(self) -> None:
        self._login_as_freeipa_user("viewer")
        resp = self.client.get(reverse("election-edit", args=[0]))
        # We expect either 403 or redirect-to-users; either blocks access.
        self.assertIn(resp.status_code, {302, 403})

    def test_eligible_users_search_requires_permission_returns_json_403(self) -> None:
        self._login_as_freeipa_user("viewer")

        viewer = FreeIPAUser("viewer", {"uid": ["viewer"], "memberof_group": []})
        with patch("core.backends.FreeIPAUser.get", return_value=viewer):
            resp = self.client.get(
                reverse("election-eligible-users-search", args=[0]),
                data={"q": "al"},
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json().get("error"), "Permission denied.")

    def test_new_election_allows_permissioned_user(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )
        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

    def test_new_election_shows_email_template_variables_without_saving(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

        # The compose widget should not start in a state where it has no variable examples.
        self.assertNotContains(resp, "No variables available yet.")
        self.assertContains(resp, "{{ username }}")
        self.assertContains(resp, "{{ election_name }}")

        # Preview endpoint should use election_id=0 in create mode.
        self.assertContains(resp, 'data-compose-preview-url="/elections/0/email/render-preview/"')

        # Empty values should render as placeholders in the example column.
        self.assertContains(resp, "-election_name-")
        self.assertContains(resp, "-election_description-")

    def test_new_election_details_card_shows_draft_badge_and_hides_status_field(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

        self.assertContains(resp, "Election details")
        self.assertContains(resp, ">draft</span>")
        self.assertNotContains(resp, "<label>Status</label>")

    def test_new_election_hides_start_button_until_saved(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

        # In create mode (unsaved election), showing a disabled "Start" button is confusing.
        # We should instead guide the user to save the draft first.
        self.assertContains(resp, "Save the draft to enable starting the election")
        self.assertNotContains(resp, 'data-target="#start-election-modal"')
        self.assertNotContains(resp, 'id="start-election-modal"')


class ElectionDraftDeletionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_draft_election_is_deletable(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft to delete",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        resp = self.client.get(reverse("election-edit", args=[election.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Delete Draft")
        self.assertNotContains(resp, "return confirm(")
        self.assertContains(resp, 'data-target="#delete-draft-election-modal"')
        self.assertContains(resp, 'id="delete-draft-election-modal"')

        resp2 = self.client.post(
            reverse("election-edit", args=[election.id]),
            data={"action": "delete_draft"},
            follow=False,
        )
        self.assertEqual(resp2.status_code, 302)
        self.assertFalse(Election.objects.filter(pk=election.pk).exists())

    def test_new_election_post_save_draft_creates_election(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        resp = self.client.post(
            reverse("election-edit", args=[0]),
            data={
                "action": "save_draft",
                "name": "New draft",
                "description": "",
                "url": "",
                "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                "number_of_seats": "1",
                "quorum": "50",
                "email_template_id": "",
                "subject": "",
                "html_content": "",
                "text_content": "",
                # Candidate formset: empty extra form.
                "candidates-TOTAL_FORMS": "1",
                "candidates-INITIAL_FORMS": "0",
                "candidates-MIN_NUM_FORMS": "0",
                "candidates-MAX_NUM_FORMS": "1000",
                "candidates-0-id": "",
                "candidates-0-freeipa_username": "",
                "candidates-0-nominated_by": "",
                "candidates-0-description": "",
                "candidates-0-url": "",
                "candidates-0-DELETE": "",
                # No exclusion groups.
                "groups-TOTAL_FORMS": "0",
                "groups-INITIAL_FORMS": "0",
                "groups-MIN_NUM_FORMS": "0",
                "groups-MAX_NUM_FORMS": "1000",
            },
            follow=False,
        )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(Election.objects.filter(name="New draft", status=Election.Status.draft).count(), 1)

    def test_new_election_post_rejects_zero_seats(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        resp = self.client.post(
            reverse("election-edit", args=[0]),
            data={
                "action": "save_draft",
                "name": "Invalid draft",
                "description": "",
                "url": "",
                "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                "number_of_seats": "0",
                "quorum": "50",
                "email_template_id": "",
                "subject": "",
                "html_content": "",
                "text_content": "",
                "candidates-TOTAL_FORMS": "1",
                "candidates-INITIAL_FORMS": "0",
                "candidates-MIN_NUM_FORMS": "0",
                "candidates-MAX_NUM_FORMS": "1000",
                "candidates-0-id": "",
                "candidates-0-freeipa_username": "",
                "candidates-0-nominated_by": "",
                "candidates-0-description": "",
                "candidates-0-url": "",
                "candidates-0-DELETE": "",
                "groups-TOTAL_FORMS": "0",
                "groups-INITIAL_FORMS": "0",
                "groups-MIN_NUM_FORMS": "0",
                "groups-MAX_NUM_FORMS": "1000",
            },
            follow=False,
        )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Number of seats")
        self.assertContains(resp, "Ensure this value is greater than or equal to 1")

    @override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
    def test_save_draft_rejects_self_nomination_and_does_not_save_candidate(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alice",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=10))

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": []},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if cn != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        with patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group):
            resp = self.client.post(
                reverse("election-edit", args=[0]),
                data={
                    "action": "save_draft",
                    "name": "Invalid self nomination",
                    "description": "",
                    "url": "",
                    "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                    "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                    "number_of_seats": "1",
                    "quorum": "10",
                    "eligible_group_cn": "",
                    "email_template_id": "",
                    "subject": "",
                    "html_content": "",
                    "text_content": "",
                    "candidates-TOTAL_FORMS": "1",
                    "candidates-INITIAL_FORMS": "0",
                    "candidates-MIN_NUM_FORMS": "0",
                    "candidates-MAX_NUM_FORMS": "1000",
                    "candidates-0-id": "",
                    "candidates-0-freeipa_username": "alice",
                    "candidates-0-nominated_by": "alice",
                    "candidates-0-description": "",
                    "candidates-0-url": "",
                    "candidates-0-DELETE": "",
                    "groups-TOTAL_FORMS": "0",
                    "groups-INITIAL_FORMS": "0",
                    "groups-MIN_NUM_FORMS": "0",
                    "groups-MAX_NUM_FORMS": "1000",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Candidates cannot nominate themselves.")
        self.assertEqual(Candidate.objects.count(), 0)

    def test_new_election_includes_scripts_in_correct_order_for_select2(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        jquery_i = html.find("admin/js/vendor/jquery/jquery.js")
        select2_i = html.find("admin/js/vendor/select2/select2.full.js")
        election_js_i = html.find("core/js/election_edit.js")

        self.assertNotEqual(jquery_i, -1)
        self.assertNotEqual(select2_i, -1)
        self.assertNotEqual(election_js_i, -1)
        self.assertLess(jquery_i, select2_i)
        self.assertLess(select2_i, election_js_i)

    def test_new_election_renders_seats_and_quorum_inputs(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertIn('id="id_number_of_seats"', html)
        self.assertIn('min="1"', html)
        self.assertIn('step="1"', html)
        self.assertIn('class="form-control smallNumber"', html)

    def test_new_election_renders_eligible_group_select2(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        self.assertIn('id="id_eligible_group_cn"', html)
        self.assertIn('alx-select2', html)
        self.assertIn('data-ajax-url="/groups/search/"', html)

        self.assertIn('id="id_quorum"', html)
        self.assertIn('min="0"', html)
        self.assertIn('max="100"', html)
        self.assertIn('step="1"', html)
        self.assertIn('class="form-control smallNumber"', html)

    def test_new_election_empty_form_has_ajax_url_for_dynamic_rows(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        resp = self.client.get(reverse("election-edit", args=[0]))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        # New rows are built from the <template id="candidates-empty-form"> contents.
        self.assertIn('id="candidates-empty-form"', html)
        self.assertIn("data-ajax-url", html)

    def test_draft_edit_page_uses_nomination_search_for_nominator_dropdown(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
            eligible_group_cn="restricted",
        )

        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="bob",
            description="",
            url="",
        )

        resp = self.client.get(reverse("election-edit", args=[election.id]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        m = re.search(r'name="candidates-0-nominated_by"[^>]*data-ajax-url="([^"]+)"', html)
        self.assertIsNotNone(m)
        self.assertIn("/nomination-users/search/", m.group(1))

    def test_draft_edit_page_uses_group_filtered_search_for_candidate_dropdown(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
            eligible_group_cn="restricted",
        )

        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="bob",
            description="",
            url="",
        )

        resp = self.client.get(reverse("election-edit", args=[election.id]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        m = re.search(r'name="candidates-0-freeipa_username"[^>]*data-ajax-url="([^"]+)"', html)
        self.assertIsNotNone(m)
        self.assertIn("/eligible-users/search/", m.group(1))



class ElectionEditCandidateSelect2LabelTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_draft_edit_page_prefills_candidate_fields_with_full_name(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="bob",
            description="",
            url="",
        )

        def _fake_user(username: str) -> FreeIPAUser | None:
            if username == "admin":
                return FreeIPAUser("admin", {"uid": ["admin"], "displayname": ["Admin"], "memberof_group": []})
            if username == "alice":
                return FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": []})
            if username == "bob":
                return FreeIPAUser("bob", {"uid": ["bob"], "displayname": ["Bob Example"], "memberof_group": []})
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_fake_user):
            resp = self.client.get(reverse("election-edit", args=[election.id]))

        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode("utf-8")

        self.assertIn('value="alice"', html)
        self.assertIn(">Alice Example (alice)<", html)

        self.assertIn('value="bob"', html)
        self.assertIn(">Bob Example (bob)<", html)
class ElectionEditExclusionGroupsSelectTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_groups_empty_form_includes_candidate_options(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        Candidate.objects.create(
            election=election,
            freeipa_username="alex",
            nominated_by="alex",
            description="",
            url="",
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="andreberry",
            nominated_by="alex",
            description="",
            url="",
        )

        resp = self.client.get(reverse("election-edit", args=[election.id]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        start = html.find('id="groups-empty-form"')
        self.assertNotEqual(start, -1)

        end = html.find("</template>", start)
        self.assertNotEqual(end, -1)

        tmpl = html[start:end]
        self.assertIn('value="alex"', tmpl)
        self.assertIn('value="andreberry"', tmpl)

    def test_groups_empty_form_prefills_candidate_labels_with_full_name(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )

        Candidate.objects.create(
            election=election,
            freeipa_username="alice",
            nominated_by="alice",
            description="",
            url="",
        )

        def _fake_user(username: str) -> FreeIPAUser | None:
            if username == "admin":
                return FreeIPAUser("admin", {"uid": ["admin"], "displayname": ["Admin"], "memberof_group": []})
            if username == "alice":
                return FreeIPAUser("alice", {"uid": ["alice"], "displayname": ["Alice Example"], "memberof_group": []})
            return None

        with patch("core.backends.FreeIPAUser.get", side_effect=_fake_user):
            resp = self.client.get(reverse("election-edit", args=[election.id]))

        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        start = html.find('id="groups-empty-form"')
        self.assertNotEqual(start, -1)

        end = html.find("</template>", start)
        self.assertNotEqual(end, -1)

        tmpl = html[start:end]
        self.assertIn('value="alice"', tmpl)
        self.assertIn(">Alice Example (alice)<", tmpl)

    def test_groups_empty_form_has_ajax_url_for_candidate_search(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        election = Election.objects.create(
            name="Draft",
            description="",
            url="",
            start_datetime=now + datetime.timedelta(days=10),
            end_datetime=now + datetime.timedelta(days=11),
            number_of_seats=1,
            status=Election.Status.draft,
        )
        Candidate.objects.create(
            election=election,
            freeipa_username="alex",
            nominated_by="alex",
            description="",
            url="",
        )

        resp = self.client.get(reverse("election-edit", args=[election.id]))
        self.assertEqual(resp.status_code, 200)

        html = resp.content.decode("utf-8")
        start = html.find('id="groups-empty-form"')
        self.assertNotEqual(start, -1)

        end = html.find("</template>", start)
        self.assertNotEqual(end, -1)

        tmpl = html[start:end]
        self.assertNotIn("data-ajax-url", tmpl)
        self.assertNotIn("/candidates/search/", tmpl)
        self.assertNotIn("alx-select2", tmpl)


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionEditCreateModeGroupSelectionTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def test_create_mode_save_draft_allows_group_candidate_selection_before_first_save(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        mt = MembershipType.objects.create(
            code="voter",
            name="Voter",
            votes=1,
            isIndividual=True,
            enabled=True,
        )
        membership = Membership.objects.create(
            target_username="alex",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=30))

        nominator_membership = Membership.objects.create(
            target_username="sam",
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=nominator_membership.pk).update(created_at=now - datetime.timedelta(days=30))

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": []},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if str(cn) != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        with patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group):
            resp = self.client.post(
                reverse("election-edit", args=[0]),
                data={
                    "action": "save_draft",
                    "name": "New draft",
                    "description": "",
                    "url": "",
                    "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                    "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                    "number_of_seats": "1",
                    "quorum": "50",
                    "email_template_id": "",
                    "subject": "",
                    "html_content": "",
                    "text_content": "",
                    # Candidate formset
                    "candidates-TOTAL_FORMS": "1",
                    "candidates-INITIAL_FORMS": "0",
                    "candidates-MIN_NUM_FORMS": "0",
                    "candidates-MAX_NUM_FORMS": "1000",
                    "candidates-0-id": "",
                    "candidates-0-freeipa_username": "alex",
                    "candidates-0-nominated_by": "sam",
                    "candidates-0-description": "",
                    "candidates-0-url": "",
                    "candidates-0-DELETE": "",
                    # Exclusion groups formset
                    "groups-TOTAL_FORMS": "1",
                    "groups-INITIAL_FORMS": "0",
                    "groups-MIN_NUM_FORMS": "0",
                    "groups-MAX_NUM_FORMS": "1000",
                    "groups-0-id": "",
                    "groups-0-name": "Employees of X",
                    "groups-0-max_elected": "1",
                    "groups-0-candidate_usernames": ["alex"],
                    "groups-0-DELETE": "",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 302)
        election = Election.objects.get(name="New draft")
        self.assertEqual(Candidate.objects.filter(election=election, freeipa_username="alex").count(), 1)
        group = ExclusionGroup.objects.get(election=election, name="Employees of X")
        candidate = Candidate.objects.get(election=election, freeipa_username="alex")
        self.assertEqual(
            ExclusionGroupCandidate.objects.filter(exclusion_group=group, candidate=candidate).count(),
            1,
        )


@override_settings(ELECTION_ELIGIBILITY_MIN_MEMBERSHIP_AGE_DAYS=1)
class ElectionCommitteeDisqualificationDraftTests(TestCase):
    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _create_membership(self, *, username: str, now: datetime.datetime) -> None:
        mt, _created = MembershipType.objects.update_or_create(
            code="voter",
            defaults={
                "name": "Voter",
                "votes": 1,
                "isIndividual": True,
                "enabled": True,
            },
        )
        membership = Membership.objects.create(
            target_username=username,
            membership_type=mt,
            expires_at=now + datetime.timedelta(days=365),
        )
        Membership.objects.filter(pk=membership.pk).update(created_at=now - datetime.timedelta(days=30))

    def test_save_draft_rejects_committee_member_candidate(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        self._create_membership(username="alice", now=now)
        self._create_membership(username="bob", now=now)

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["alice"]},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if str(cn) != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        def _get_user(username: str) -> FreeIPAUser | None:
            return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

        with (
            patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.post(
                reverse("election-edit", args=[0]),
                data={
                    "action": "save_draft",
                    "name": "New draft",
                    "description": "",
                    "url": "",
                    "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                    "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                    "number_of_seats": "1",
                    "quorum": "50",
                    "email_template_id": "",
                    "subject": "",
                    "html_content": "",
                    "text_content": "",
                    # Candidate formset
                    "candidates-TOTAL_FORMS": "1",
                    "candidates-INITIAL_FORMS": "0",
                    "candidates-MIN_NUM_FORMS": "0",
                    "candidates-MAX_NUM_FORMS": "1000",
                    "candidates-0-id": "",
                    "candidates-0-freeipa_username": "alice",
                    "candidates-0-nominated_by": "bob",
                    "candidates-0-description": "",
                    "candidates-0-url": "",
                    "candidates-0-DELETE": "",
                    # Exclusion groups formset
                    "groups-TOTAL_FORMS": "0",
                    "groups-INITIAL_FORMS": "0",
                    "groups-MIN_NUM_FORMS": "0",
                    "groups-MAX_NUM_FORMS": "1000",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Election committee members cannot be candidates")
        self.assertFalse(Candidate.objects.filter(freeipa_username="alice").exists())

    def test_save_draft_rejects_committee_member_nominator(self) -> None:
        self._login_as_freeipa_user("admin")
        FreeIPAPermissionGrant.objects.create(
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="admin",
            permission=ASTRA_ADD_ELECTION,
        )

        now = timezone.now()
        self._create_membership(username="alice", now=now)
        self._create_membership(username="bob", now=now)

        committee_group = FreeIPAGroup(
            settings.FREEIPA_ELECTION_COMMITTEE_GROUP,
            {"member_user": ["bob"]},
        )

        def _get_group(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
            if str(cn) != committee_group.cn:
                raise FreeIPAMisconfiguredError("Unknown group")
            return committee_group

        def _get_user(username: str) -> FreeIPAUser | None:
            return FreeIPAUser(username, {"uid": [username], "memberof_group": []})

        with (
            patch("core.backends.get_freeipa_group_for_elections", side_effect=_get_group),
            patch("core.backends.FreeIPAUser.get", side_effect=_get_user),
        ):
            resp = self.client.post(
                reverse("election-edit", args=[0]),
                data={
                    "action": "save_draft",
                    "name": "New draft",
                    "description": "",
                    "url": "",
                    "start_datetime": (now + datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M"),
                    "end_datetime": (now + datetime.timedelta(days=11)).strftime("%Y-%m-%dT%H:%M"),
                    "number_of_seats": "1",
                    "quorum": "50",
                    "email_template_id": "",
                    "subject": "",
                    "html_content": "",
                    "text_content": "",
                    # Candidate formset
                    "candidates-TOTAL_FORMS": "1",
                    "candidates-INITIAL_FORMS": "0",
                    "candidates-MIN_NUM_FORMS": "0",
                    "candidates-MAX_NUM_FORMS": "1000",
                    "candidates-0-id": "",
                    "candidates-0-freeipa_username": "alice",
                    "candidates-0-nominated_by": "bob",
                    "candidates-0-description": "",
                    "candidates-0-url": "",
                    "candidates-0-DELETE": "",
                    # Exclusion groups formset
                    "groups-TOTAL_FORMS": "0",
                    "groups-INITIAL_FORMS": "0",
                    "groups-MIN_NUM_FORMS": "0",
                    "groups-MAX_NUM_FORMS": "1000",
                },
                follow=False,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Election committee members cannot nominate candidates")
        self.assertFalse(Candidate.objects.filter(freeipa_username="alice").exists())
