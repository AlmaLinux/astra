
from types import SimpleNamespace
from unittest.mock import patch

from django.template import Context, Template
from django.test import TestCase


class GroupWidgetDescriptionTests(TestCase):
    def test_group_widget_renders_description_and_count(self) -> None:
        group = SimpleNamespace(
            cn="demo",
            description="This is a very long description that should be truncated in the widget.",
            fas_group=True,
            members=["alice"],
            member_groups=[],
            sponsors=[],
            sponsor_groups=[],
        )

        with patch("core.backends.FreeIPAGroup.get", return_value=group):
            tpl = Template("{% load core_group_widget %}{% group 'demo' %}")
            html = tpl.render(Context({"request": None}))

        self.assertIn("widget-group", html)
        self.assertIn("demo", html)
        self.assertIn("This is a very long description", html)
        self.assertIn("badge", html)
