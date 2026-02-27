
from unittest.mock import patch

from django.test import TestCase
from python_freeipa import exceptions

from core.freeipa.group import FreeIPAGroup


class FreeIPAGroupNoOpSaveTests(TestCase):
    def test_group_save_noop_does_not_raise(self) -> None:
        group = FreeIPAGroup("testgroup", {"cn": ["testgroup"], "description": ["desc"]})

        def fake_retry(_get_client, fn):
            class DummyClient:
                def group_mod(self, _cn: str, **_updates: object):
                    raise exceptions.BadRequest("no modifications to be performed", 400)

            return fn(DummyClient())

        with (
            patch("core.freeipa.group._with_freeipa_service_client_retry", side_effect=fake_retry),
            patch("core.freeipa.group._invalidate_group_cache"),
            patch("core.freeipa.group._invalidate_groups_list_cache"),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            group.save()
