import os
import subprocess
import sys
import textwrap
import unittest


class TestSettingsSentrySdkInit(unittest.TestCase):
    def test_sentry_sdk_is_initialized_when_dsn_is_set(self) -> None:
        env = os.environ.copy()
        env.update(
            {
                "DEBUG": "0",
                "SECRET_KEY": "test-secret-key-not-insecure-37-chars",
                "ALLOWED_HOSTS": "example.com",
                "FREEIPA_SERVICE_PASSWORD": "password",
                "AWS_STORAGE_BUCKET_NAME": "astra-media",
                "AWS_S3_DOMAIN": "http://localhost:9000",
                "DATABASE_HOST": "db.example.internal",
                "DATABASE_PORT": "5432",
                "DATABASE_NAME": "astra",
                "DATABASE_USER": "astra",
                "DATABASE_PASSWORD": "supersecret",
                "SENTRY_DSN": "http://public@example.invalid/1",
            }
        )
        env.pop("DATABASE_URL", None)

        code = textwrap.dedent(
            """
            import os
            import sys
            import types

            sys.path.insert(0, os.path.join(os.getcwd(), "astra_app"))

            # Provide a fake sentry_sdk module so this test doesn't depend on the
            # package being installed. The settings import should still call init().
            sentry_sdk = types.ModuleType("sentry_sdk")
            sentry_sdk_integrations = types.ModuleType("sentry_sdk.integrations")
            sentry_sdk_integrations_django = types.ModuleType("sentry_sdk.integrations.django")
            sentry_sdk_integrations_logging = types.ModuleType("sentry_sdk.integrations.logging")

            class DjangoIntegration:
                pass

            class LoggingIntegration:
                def __init__(self, *args, **kwargs):
                    pass

            def init(*, dsn=None, **kwargs):
                print("sentry-init")
                print(dsn)
                print(f"traces_sample_rate={kwargs.get('traces_sample_rate')!r}")
                print(f"send_client_reports={kwargs.get('send_client_reports')!r}")
                print(f"auto_session_tracking={kwargs.get('auto_session_tracking')!r}")

            sentry_sdk.init = init
            sentry_sdk_integrations_django.DjangoIntegration = DjangoIntegration
            sentry_sdk_integrations_logging.LoggingIntegration = LoggingIntegration

            sys.modules["sentry_sdk"] = sentry_sdk
            sys.modules["sentry_sdk.integrations"] = sentry_sdk_integrations
            sys.modules["sentry_sdk.integrations.django"] = sentry_sdk_integrations_django
            sys.modules["sentry_sdk.integrations.logging"] = sentry_sdk_integrations_logging

            import config.settings  # noqa: F401

            print("ok")
            """
        ).strip()

        result = subprocess.run(
            [sys.executable, "-c", code],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"settings import failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        lines = [line for line in result.stdout.strip().splitlines() if line]
        self.assertIn("sentry-init", lines)
        self.assertIn("http://public@example.invalid/1", lines)
        self.assertEqual(lines[-1], "ok")
