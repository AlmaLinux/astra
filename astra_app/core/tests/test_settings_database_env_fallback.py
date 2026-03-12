import os
import subprocess
import sys
import textwrap
import unittest


class TestSettingsDatabaseEnvFallback(unittest.TestCase):
    def test_cache_backend_defaults_to_database_cache(self) -> None:
        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
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
            }
        )

        code = textwrap.dedent(
            """
            import os
            import sys

            sys.path.insert(0, os.path.join(os.getcwd(), "astra_app"))

            import config.settings as settings

            cache_default = settings.CACHES["default"]
            print(cache_default["BACKEND"])
            print(cache_default["LOCATION"])
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
        self.assertGreaterEqual(len(lines), 2)
        self.assertEqual(lines[-2], "django.core.cache.backends.db.DatabaseCache")
        self.assertEqual(lines[-1], "astra_cache")

    def test_database_host_env_vars_used_without_database_url(self) -> None:
        # Validate that settings can be configured via discrete DATABASE_* env vars,
        # which is important for ECS where DATABASE_PASSWORD is injected as a secret.
        env = os.environ.copy()
        env.pop("DATABASE_URL", None)
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
            }
        )

        code = textwrap.dedent(
            """
            import os
            import sys

            # Ensure we can import the Django project packages from the repo checkout.
            sys.path.insert(0, os.path.join(os.getcwd(), "astra_app"))

            import config.settings as settings

            print(settings.DATABASES["default"]["HOST"])
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
        # settings.py may emit debug output; assert the final line is the value.
        self.assertEqual(result.stdout.strip().splitlines()[-1], "db.example.internal")
