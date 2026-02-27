
from unittest.mock import patch

from django.test import SimpleTestCase
from python_freeipa import exceptions

from core.freeipa.group import FreeIPAGroup


class FreeIPAGroupSaveFASAttrsTests(SimpleTestCase):
    def test_save_does_not_use_unknown_fas_option_kwargs(self) -> None:
        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "fasurl": ["https://old.example.org"],
                "fasmailinglist": ["old@example.org"],
                "fasdiscussionurl": ["https://discussion.example.org/c/old"],
                "fasircchannel": ["#old"],
                "description": ["Old desc"],
                "objectclass": ["fasGroup"],
            },
        )

        group.fas_url = "https://new.example.org"
        group.fas_mailing_list = "new@example.org"
        group.fas_discussion_url = "https://discussion.example.org/c/new"
        group.fas_irc_channels = ["#new", "#new-dev"]
        group.description = "New desc"

        class _FakeClient:
            def group_mod(self, cn: str, **kwargs):
                # The python_freeipa wrapper doesn't know FAS extension options, so
                # passing them as explicit keyword arguments raises UnknownOption.
                for k in kwargs:
                    if k.startswith("o_fas"):
                        raise exceptions.UnknownOption(f"Unknown option: {k}")
                return {"result": {}}

        fake_client = _FakeClient()

        with (
            patch(
                "core.freeipa.group._with_freeipa_service_client_retry",
                side_effect=lambda _get_client, fn: fn(fake_client),
            ),
            patch("core.freeipa.group._invalidate_group_cache"),
            patch("core.freeipa.group._invalidate_groups_list_cache"),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            # Should not raise UnknownOption.
            group.save()

    def test_save_uses_name_value_delattr_for_fasircchannel(self) -> None:
        group = FreeIPAGroup(
            "fas1",
            {
                "cn": ["fas1"],
                "fasircchannel": ["irc:/#old"],
                "objectclass": ["fasGroup"],
            },
        )

        # Change the set of channels to force a delta update.
        group.fas_irc_channels = ["irc:/#old", "irc:/#new"]

        class _FakeClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, object]]] = []

            def group_mod(self, cn: str, **kwargs):
                self.calls.append((cn, dict(kwargs)))

                delattrs = kwargs.get("o_delattr")
                if delattrs is not None:
                    for item in delattrs:
                        assert "=" in str(item), f"delattr must be name=value, got: {item!r}"
                return {"result": {}}

        fake_client = _FakeClient()

        with (
            patch(
                "core.freeipa.group._with_freeipa_service_client_retry",
                side_effect=lambda _get_client, fn: fn(fake_client),
            ),
            patch("core.freeipa.group._invalidate_group_cache"),
            patch("core.freeipa.group._invalidate_groups_list_cache"),
            patch("core.freeipa.group.FreeIPAGroup.get", return_value=group),
        ):
            group.save()

        self.assertTrue(fake_client.calls)
        cn, kwargs = fake_client.calls[-1]
        self.assertEqual(cn, "fas1")
        self.assertIn("o_addattr", kwargs)
