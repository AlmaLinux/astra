
from types import SimpleNamespace
from unittest.mock import patch

from django.template import Context, Template
from django.test import RequestFactory, TestCase

from core.backends import FreeIPAUser, clear_current_viewer_username, set_current_viewer_username


class UserGridTemplateTagTests(TestCase):
    def test_user_grid_paginates_30_per_page_from_users_list(self) -> None:
        users = [
            SimpleNamespace(username=f"user{i:03d}", get_full_name=lambda: "")
            for i in range(65)
        ]

        request = RequestFactory().get("/users/", {"page": "2"})

        tpl = Template(
            ""
            "{% load core_user_grid %}"
            "{% user_grid users=users %}"
            ""
        )
        html = tpl.render(Context({"request": request, "users": users}))

        self.assertIn('href="/user/user028/"', html)
        self.assertNotIn('href="/user/user027/"', html)

    def test_user_grid_paginates_30_per_page_for_group_members(self) -> None:
        members = [f"user{i:03d}" for i in range(65)]
        group = SimpleNamespace(cn="fas1", members=members)

        request = RequestFactory().get("/group/fas1/", {"page": "2"})

        def _fake_user_get(username: str) -> FreeIPAUser:
            return FreeIPAUser(username, {"uid": [username], "givenname": [""], "sn": [""], "mail": [""]})

        tpl = Template(
            ""
            "{% load core_user_grid %}"
            "{% user_grid group=group %}"
            ""
        )

        with patch("core.templatetags.core_user_widget.FreeIPAUser.get", side_effect=_fake_user_get):
            html = tpl.render(Context({"request": request, "group": group}))

        self.assertIn('href="/user/user028/"', html)
        self.assertNotIn('href="/user/user027/"', html)

    def test_user_grid_without_args_uses_freeipauser_all(self) -> None:
        users = [
            SimpleNamespace(username="alice", get_full_name=lambda: "Alice User"),
            SimpleNamespace(username="bob", get_full_name=lambda: "Bob User"),
        ]
        request = RequestFactory().get("/users/")

        tpl = Template(
            ""
            "{% load core_user_grid %}"
            "{% user_grid %}"
            ""
        )

        with patch("core.templatetags.core_user_grid.FreeIPAUser.all", return_value=users):
            html = tpl.render(Context({"request": request}))

        self.assertIn('href="/user/alice/"', html)
        self.assertIn('href="/user/bob/"', html)

    def test_user_widget_anonymizes_private_user_for_non_self_viewer(self) -> None:
        request = RequestFactory().get("/group/example/")
        request.user = SimpleNamespace(is_authenticated=True, get_username=lambda: "alice")

        set_current_viewer_username("alice")
        try:
            bob = FreeIPAUser(
                "bob",
                {
                    "uid": ["bob"],
                    "givenname": ["Bob"],
                    "sn": ["User"],
                    "mail": ["bob@example.org"],
                    "fasIsPrivate": ["TRUE"],
                },
            )
        finally:
            clear_current_viewer_username()

        tpl = Template("" "{% load core_user_widget %}{% user 'bob' %}" "")

        with patch("core.templatetags.core_user_widget.FreeIPAUser.get", return_value=bob):
            html = tpl.render(Context({"request": request}))

        self.assertIn('href="/user/bob/"', html)
        self.assertNotIn("Bob User", html)
