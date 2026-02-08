
import os
from unittest import TestCase
from unittest.mock import patch

from astra_app.core.build_info import get_build_sha


class BuildInfoTests(TestCase):
    def test_build_info_reads_env(self) -> None:
        with patch.dict(os.environ, {"ASTRA_BUILD_SHA": "abc1234"}):
            self.assertEqual(get_build_sha(), "abc1234")

    def test_build_info_missing_env_returns_empty(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_build_sha(), "")
