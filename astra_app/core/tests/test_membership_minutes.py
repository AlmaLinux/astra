import datetime

from django.test import RequestFactory, TestCase
from django.utils import timezone

from core.membership_minutes import build_minutes_data
from core.membership_request_workflow import put_membership_request_on_hold
from core.models import (
    MembershipLog,
    MembershipRequest,
    MembershipType,
    MembershipTypeCategory,
)
from core.views_membership_admin import (
    MEMBERSHIP_MINUTES_MAX_RANGE_DAYS,
    _parse_membership_minutes_range,
)

UTC = datetime.UTC


def _dt(year: int, month: int, day: int, hour: int = 12) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=UTC)


class MembershipMinutesTestBase(TestCase):
    @classmethod
    def setUpTestData(cls) -> None:
        specs = [
            ("individual", True, False, 0, [("individual", "Individual")]),
            ("mirror", False, True, 1, [("mirror", "Mirror")]),
            (
                "sponsorship",
                False,
                True,
                2,
                [("gold", "Gold Sponsor"), ("platinum", "Platinum Sponsor")],
            ),
        ]
        cls.types: dict[str, MembershipType] = {}
        for category_id, is_individual, is_organization, sort_order, types in specs:
            MembershipTypeCategory.objects.update_or_create(
                pk=category_id,
                defaults={
                    "is_individual": is_individual,
                    "is_organization": is_organization,
                    "sort_order": sort_order,
                },
            )
            for code, name in types:
                membership_type, _ = MembershipType.objects.update_or_create(
                    code=code,
                    defaults={
                        "name": name,
                        "group_cn": f"almalinux-{code}",
                        "category_id": category_id,
                        "sort_order": 0,
                        "enabled": True,
                    },
                )
                cls.types[code] = membership_type

    def _make_request(
        self,
        *,
        username: str,
        type_code: str,
        requested_at: datetime.datetime,
        decided_at: datetime.datetime | None = None,
        status: str = MembershipRequest.Status.pending,
    ) -> MembershipRequest:
        membership_request = MembershipRequest.objects.create(
            requested_username=username,
            membership_type=self.types[type_code],
            status=status,
            decided_at=decided_at,
        )
        MembershipRequest.objects.filter(pk=membership_request.pk).update(requested_at=requested_at)
        membership_request.refresh_from_db()
        return membership_request

    def _log(
        self,
        *,
        membership_request: MembershipRequest,
        action: str,
        created_at: datetime.datetime,
        rejection_reason: str = "",
    ) -> MembershipLog:
        log = MembershipLog.objects.create(
            actor_username="reviewer",
            target_username=membership_request.requested_username,
            membership_type=membership_request.membership_type,
            membership_request=membership_request,
            action=action,
            rejection_reason=rejection_reason,
        )
        MembershipLog.objects.filter(pk=log.pk).update(created_at=created_at)
        return log


class BuildMinutesDataTests(MembershipMinutesTestBase):
    def test_reproduces_example_shape(self) -> None:
        start = datetime.date(2026, 10, 25)
        end = datetime.date(2026, 11, 18)

        # Two individuals declined during the window.
        r_declined_a = self._make_request(
            username="alice", type_code="individual", requested_at=_dt(2026, 10, 26),
            decided_at=_dt(2026, 10, 30), status=MembershipRequest.Status.rejected,
        )
        self._log(membership_request=r_declined_a, action=MembershipLog.Action.requested, created_at=_dt(2026, 10, 26))
        self._log(
            membership_request=r_declined_a, action=MembershipLog.Action.rejected,
            created_at=_dt(2026, 10, 30), rejection_reason="Reason 282",
        )

        r_declined_b = self._make_request(
            username="bob", type_code="individual", requested_at=_dt(2026, 11, 1),
            decided_at=_dt(2026, 11, 5), status=MembershipRequest.Status.rejected,
        )
        self._log(membership_request=r_declined_b, action=MembershipLog.Action.requested, created_at=_dt(2026, 11, 1))
        self._log(
            membership_request=r_declined_b, action=MembershipLog.Action.rejected,
            created_at=_dt(2026, 11, 5), rejection_reason="Reason 342",
        )

        # One individual accepted.
        r_accepted_ind = self._make_request(
            username="carol", type_code="individual", requested_at=_dt(2026, 10, 27),
            decided_at=_dt(2026, 10, 28), status=MembershipRequest.Status.approved,
        )
        self._log(membership_request=r_accepted_ind, action=MembershipLog.Action.requested, created_at=_dt(2026, 10, 27))
        self._log(membership_request=r_accepted_ind, action=MembershipLog.Action.approved, created_at=_dt(2026, 10, 28))

        # One mirror put on hold (RFI) within the window.
        r_mirror_rfi = self._make_request(
            username="dave", type_code="mirror", requested_at=_dt(2026, 10, 29),
            status=MembershipRequest.Status.on_hold,
        )
        self._log(membership_request=r_mirror_rfi, action=MembershipLog.Action.requested, created_at=_dt(2026, 10, 29))
        self._log(
            membership_request=r_mirror_rfi, action=MembershipLog.Action.on_hold,
            created_at=_dt(2026, 10, 31), rejection_reason="RFI mirror",
        )

        # One sponsor requested BEFORE the window (earlier pending), RFI in window.
        r_sponsor_rfi = self._make_request(
            username="eve", type_code="gold", requested_at=_dt(2026, 10, 1),
            status=MembershipRequest.Status.on_hold,
        )
        self._log(membership_request=r_sponsor_rfi, action=MembershipLog.Action.requested, created_at=_dt(2026, 10, 1))
        self._log(
            membership_request=r_sponsor_rfi, action=MembershipLog.Action.on_hold,
            created_at=_dt(2026, 10, 26), rejection_reason="RFI sponsor",
        )

        # One sponsor accepted within the window.
        r_sponsor_accepted = self._make_request(
            username="frank", type_code="platinum", requested_at=_dt(2026, 11, 2),
            decided_at=_dt(2026, 11, 6), status=MembershipRequest.Status.approved,
        )
        self._log(membership_request=r_sponsor_accepted, action=MembershipLog.Action.requested, created_at=_dt(2026, 11, 2))
        self._log(membership_request=r_sponsor_accepted, action=MembershipLog.Action.approved, created_at=_dt(2026, 11, 6))

        data = build_minutes_data(start, end)

        self.assertEqual(data["summary"], {
            "received_or_updated": 5,
            "earlier_pending": 1,
            "rfi": 2,
            "declined": 2,
            "accepted": 2,
            "pending": 0,
        })

        categories = {category["code"]: category for category in data["categories"]}
        self.assertEqual([c["code"] for c in data["categories"]], ["individual", "mirror", "sponsorship"])

        individual = categories["individual"]
        self.assertEqual(individual["rfi"], [])
        self.assertEqual([r["id"] for r in individual["declined"]], [r_declined_a.pk, r_declined_b.pk])
        self.assertEqual(individual["declined"][0]["reason"], "Reason 282")
        self.assertEqual([r["id"] for r in individual["accepted"]], [r_accepted_ind.pk])

        mirror = categories["mirror"]
        self.assertEqual([r["id"] for r in mirror["rfi"]], [r_mirror_rfi.pk])
        self.assertEqual(mirror["rfi"][0]["reason"], "RFI mirror")

        sponsorship = categories["sponsorship"]
        self.assertEqual([r["id"] for r in sponsorship["rfi"]], [r_sponsor_rfi.pk])
        self.assertEqual(sponsorship["rfi"][0]["membership_type_name"], "Gold Sponsor")
        self.assertEqual([r["id"] for r in sponsorship["accepted"]], [r_sponsor_accepted.pk])
        self.assertEqual(sponsorship["accepted"][0]["membership_type_name"], "Platinum Sponsor")
        self.assertTrue(sponsorship["accepted"][0]["url"].endswith(f"/membership/request/{r_sponsor_accepted.pk}/"))

    def test_earlier_pending_still_pending_counts_but_not_listed(self) -> None:
        start = datetime.date(2026, 6, 1)
        end = datetime.date(2026, 6, 30)

        req = self._make_request(
            username="grace", type_code="individual", requested_at=_dt(2026, 5, 1),
            status=MembershipRequest.Status.pending,
        )
        self._log(membership_request=req, action=MembershipLog.Action.requested, created_at=_dt(2026, 5, 1))

        data = build_minutes_data(start, end)
        self.assertEqual(data["summary"]["earlier_pending"], 1)
        self.assertEqual(data["summary"]["received_or_updated"], 0)
        self.assertEqual(data["summary"]["pending"], 1)
        for category in data["categories"]:
            self.assertEqual(category["rfi"], [])
            self.assertEqual(category["declined"], [])
            self.assertEqual(category["accepted"], [])

    def test_decided_before_window_excluded(self) -> None:
        start = datetime.date(2026, 6, 1)
        end = datetime.date(2026, 6, 30)

        req = self._make_request(
            username="heidi", type_code="individual", requested_at=_dt(2026, 4, 1),
            decided_at=_dt(2026, 4, 5), status=MembershipRequest.Status.rejected,
        )
        self._log(membership_request=req, action=MembershipLog.Action.requested, created_at=_dt(2026, 4, 1))
        self._log(membership_request=req, action=MembershipLog.Action.rejected, created_at=_dt(2026, 4, 5))

        data = build_minutes_data(start, end)
        self.assertEqual(data["summary"]["received_or_updated"], 0)
        self.assertEqual(data["summary"]["earlier_pending"], 0)
        self.assertEqual(data["summary"]["declined"], 0)

    def test_ignored_request_excluded_from_scope(self) -> None:
        start = datetime.date(2026, 6, 1)
        end = datetime.date(2026, 6, 30)

        req = self._make_request(
            username="ivan", type_code="individual", requested_at=_dt(2026, 6, 2),
            decided_at=_dt(2026, 6, 3), status=MembershipRequest.Status.ignored,
        )
        self._log(membership_request=req, action=MembershipLog.Action.requested, created_at=_dt(2026, 6, 2))
        self._log(membership_request=req, action=MembershipLog.Action.ignored, created_at=_dt(2026, 6, 3))

        data = build_minutes_data(start, end)
        self.assertEqual(data["summary"]["received_or_updated"], 0)
        self.assertEqual(data["summary"]["accepted"], 0)

    def test_window_boundaries_are_inclusive(self) -> None:
        start = datetime.date(2026, 6, 10)
        end = datetime.date(2026, 6, 20)

        on_start = self._make_request(
            username="judy", type_code="individual", requested_at=_dt(2026, 6, 10, 0),
            decided_at=_dt(2026, 6, 10, 0), status=MembershipRequest.Status.approved,
        )
        self._log(membership_request=on_start, action=MembershipLog.Action.requested, created_at=_dt(2026, 6, 10, 0))
        self._log(membership_request=on_start, action=MembershipLog.Action.approved, created_at=_dt(2026, 6, 10, 0))

        on_end = self._make_request(
            username="ken", type_code="individual", requested_at=_dt(2026, 6, 20, 23),
            decided_at=_dt(2026, 6, 20, 23), status=MembershipRequest.Status.approved,
        )
        self._log(membership_request=on_end, action=MembershipLog.Action.requested, created_at=_dt(2026, 6, 20, 23))
        self._log(membership_request=on_end, action=MembershipLog.Action.approved, created_at=_dt(2026, 6, 20, 23))

        before = self._make_request(
            username="leo", type_code="individual", requested_at=_dt(2026, 6, 9, 23),
            decided_at=_dt(2026, 6, 9, 23), status=MembershipRequest.Status.approved,
        )
        self._log(membership_request=before, action=MembershipLog.Action.requested, created_at=_dt(2026, 6, 9, 23))
        self._log(membership_request=before, action=MembershipLog.Action.approved, created_at=_dt(2026, 6, 9, 23))

        data = build_minutes_data(start, end)
        self.assertEqual(data["summary"]["received_or_updated"], 2)
        self.assertEqual(data["summary"]["accepted"], 2)


class PutOnHoldPersistsRfiReasonTests(MembershipMinutesTestBase):
    def test_on_hold_log_stores_rfi_message(self) -> None:
        req = self._make_request(
            username="mallory", type_code="individual", requested_at=_dt(2026, 3, 1),
            status=MembershipRequest.Status.pending,
        )

        log, error = put_membership_request_on_hold(
            membership_request=req,
            actor_username="reviewer",
            rfi_message="Please send your GPG key.",
            send_rfi_email=False,
            application_url="https://example.test/app/",
        )

        self.assertIsNone(error)
        self.assertEqual(log.action, MembershipLog.Action.on_hold)
        self.assertEqual(log.rejection_reason, "Please send your GPG key.")


class ParseMembershipMinutesRangeTests(TestCase):
    def _parse(self, start: str, end: str) -> tuple[datetime.date, datetime.date]:
        request = RequestFactory().get("/membership/minutes/data/", {"start": start, "end": end})
        return _parse_membership_minutes_range(request)

    def test_valid_range(self) -> None:
        today = timezone.localdate()
        start = (today - datetime.timedelta(days=10)).isoformat()
        parsed_start, parsed_end = self._parse(start, today.isoformat())
        self.assertEqual(parsed_end, today)

    def test_rejects_future_end_date(self) -> None:
        today = timezone.localdate()
        tomorrow = (today + datetime.timedelta(days=1)).isoformat()
        with self.assertRaises(ValueError) as ctx:
            self._parse(today.isoformat(), tomorrow)
        self.assertIn("future", str(ctx.exception).lower())

    def test_range_too_large_message_includes_cap(self) -> None:
        today = timezone.localdate()
        start = (today - datetime.timedelta(days=MEMBERSHIP_MINUTES_MAX_RANGE_DAYS + 1)).isoformat()
        with self.assertRaises(ValueError) as ctx:
            self._parse(start, today.isoformat())
        self.assertIn(str(MEMBERSHIP_MINUTES_MAX_RANGE_DAYS), str(ctx.exception))

    def test_requires_both_dates(self) -> None:
        with self.assertRaises(ValueError):
            self._parse("", "2026-01-01")
