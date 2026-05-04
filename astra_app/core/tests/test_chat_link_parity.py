import json
from pathlib import Path

from django.test import SimpleTestCase, override_settings

from core.chatnicknames import build_chat_channel_link, build_chat_nickname_link


def _parity_fixture_path() -> Path:
    return Path(__file__).resolve().parents[3] / "frontend" / "src" / "shared" / "__tests__" / "fixtures" / "chatLinkParityCases.json"


def _load_parity_fixture() -> dict[str, object]:
    return json.loads(_parity_fixture_path().read_text(encoding="utf-8"))


@override_settings(
    CHAT_NETWORKS={
        "irc": {"default_server": "irc.libera.chat"},
        "matrix": {"default_server": "matrix.org"},
        "mattermost": {
            "default_server": "chat.almalinux.org",
            "default_team": "almalinux",
        },
    },
    CHAT_MATRIX_TO_ARGS="web-instance[element.io]=app.element.io",
)
class ChatLinkParityTests(SimpleTestCase):
    def test_python_builders_match_the_shared_parity_fixture(self) -> None:
        fixture = _load_parity_fixture()
        cases = fixture["cases"]

        self.assertIsInstance(cases, list)

        for case in cases:
            self.assertIsInstance(case, dict)
            raw = case["raw"]
            kind = case["kind"]
            expected = case["expected"]

            self.assertIsInstance(raw, str)
            self.assertIn(kind, {"nickname", "channel"})

            link = (
                build_chat_nickname_link(raw)
                if kind == "nickname"
                else build_chat_channel_link(raw)
            )

            with self.subTest(case=case["id"]):
                if expected is None:
                    self.assertIsNone(link)
                    continue

                self.assertIsNotNone(link)
                self.assertEqual(
                    {
                        "href": link.href,
                        "title": link.title,
                        "display": link.display,
                        "external": link.external,
                    },
                    expected,
                )