import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class AuthProfileE2EScriptTests(unittest.TestCase):
    maxDiff = None
    BROWSER_SENTINEL_NAME = ".astra-playwright-chromium-version"

    @staticmethod
    def _repo_root() -> Path:
        return Path(__file__).resolve().parents[3]

    @classmethod
    def _script_path(cls) -> Path:
        return cls._repo_root() / "scripts" / "auth-profile-e2e.sh"

    def _write_fake_command(self, directory: Path, name: str, content: str) -> None:
        path = directory / name
        path.write_text(content, encoding="utf-8")
        path.chmod(0o755)

    def _make_fake_environment(
        self,
        *,
        ps_output: str,
        browser_cache_entries: tuple[str, ...] = ("chromium-1234",),
        browser_sentinel_version: str | None = None,
        playwright_version: str = "1.55.0",
        stale_dependencies: bool = False,
    ) -> tuple[dict[str, str], Path]:
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(temp_dir)], check=False))

        bin_dir = temp_dir / "bin"
        bin_dir.mkdir()
        frontend_dir = temp_dir / "frontend"
        frontend_dir.mkdir()
        node_modules_dir = frontend_dir / "node_modules"
        node_modules_dir.mkdir()
        node_modules_bin_dir = node_modules_dir / ".bin"
        node_modules_bin_dir.mkdir()
        playwright_package_dir = node_modules_dir / "playwright"
        playwright_package_dir.mkdir()
        browser_cache_dir = temp_dir / "ms-playwright"
        browser_cache_dir.mkdir()
        for browser_cache_entry in browser_cache_entries:
            (browser_cache_dir / browser_cache_entry).mkdir(parents=True)
        if browser_sentinel_version is not None:
            (browser_cache_dir / self.BROWSER_SENTINEL_NAME).write_text(
                browser_sentinel_version,
                encoding="utf-8",
            )
        log_path = temp_dir / "commands.log"
        log_path.write_text("", encoding="utf-8")

        (frontend_dir / "package.json").write_text('{"name":"frontend"}', encoding="utf-8")
        package_lock_contents = '{"name":"frontend","lockfileVersion":3}'
        package_lock_path = frontend_dir / "package-lock.json"
        package_lock_path.write_text(package_lock_contents, encoding="utf-8")
        playwright_bin_path = node_modules_bin_dir / "playwright"
        playwright_bin_path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        playwright_bin_path.chmod(0o755)
        (playwright_package_dir / "package.json").write_text(
            f'{{"version":"{playwright_version}"}}',
            encoding="utf-8",
        )
        if stale_dependencies:
            os.utime(playwright_bin_path, (1, 1))
            os.utime(package_lock_path, None)

        self._write_fake_command(
            bin_dir,
            "podman",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'podman\\t%s\\n' \"$*\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "printf '%s' \"${ASTRA_E2E_TEST_PS_OUTPUT:-}\"\n",
        )
        self._write_fake_command(
            bin_dir,
            "podman-compose",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'podman-compose\\t%s\\n' \"$*\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "case \"$*\" in\n"
            "  *\" exec -T web python manage.py auth_profile_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"auth-profile\\\", \\\"status\\\": \\\"reset\\\"}\\n'\n"
            "    ;;\n"
            "esac\n",
        )
        self._write_fake_command(
            bin_dir,
            "curl",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'curl\\t%s\\n' \"$*\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "printf '{\"status\":\"ready\",\"database\":\"ok\"}\\n'\n",
        )
        self._write_fake_command(
            bin_dir,
            "npm",
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf 'npm\\t%s\\n' \"$*\" >> \"$ASTRA_E2E_TEST_LOG\"\n",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{bin_dir}:{env['PATH']}",
                "ASTRA_E2E_TEST_LOG": str(log_path),
                "ASTRA_E2E_TEST_PS_OUTPUT": ps_output,
                "ASTRA_E2E_FRONTEND_DIR": str(frontend_dir),
                "ASTRA_E2E_BROWSER_CACHE_DIR": str(browser_cache_dir),
            }
        )
        return env, log_path

    def _run_script(self, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self._script_path()), *args],
            cwd=self._repo_root(),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_output_describes_default_and_down_commands(self) -> None:
        result = subprocess.run(
            [str(self._script_path()), "--help"],
            cwd=self._repo_root(),
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"help command failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Usage:", result.stdout)
        self.assertIn("down", result.stdout)
        self.assertIn("reset", result.stdout)
        self.assertIn("--headed", result.stdout)
        self.assertIn("--ui", result.stdout)

    def test_default_run_starts_stack_when_missing_and_runs_reset_and_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="")

        result = self._run_script(env)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        expected_fragments = [
            "podman\tps --filter label=io.podman.compose.project=astra-e2e --filter label=io.podman.compose.service=web --format {{.ID}}",
            "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d db minio minio_init web",
            "curl\t--retry 30 --retry-all-errors --retry-delay 2 -fsS http://127.0.0.1:18000/readyz",
            "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml exec -T web python manage.py auth_profile_reset",
            "npm\trun e2e:auth-profile",
        ]

        self.assertEqual(
            [line for line in log_lines if any(fragment in line for fragment in expected_fragments)],
            expected_fragments,
        )
        self.assertNotIn("npm\tci", log_lines)

    def test_default_run_reuses_running_stack_without_restarting_it(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertTrue(any("exec -T web python manage.py auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_install_runs_browser_install_when_version_sentinel_is_missing(self) -> None:
        env, log_path = self._make_fake_environment(
            ps_output="",
            browser_cache_entries=("chromium-1234",),
            browser_sentinel_version=None,
        )

        result = self._run_script(env, "install")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("npm\trun e2e:install", log_lines)
        self.assertFalse(any(line.startswith("podman") for line in log_lines), log_lines)
        self.assertFalse(any(line.startswith("curl") for line in log_lines), log_lines)

    def test_install_skips_browser_install_when_version_sentinel_matches(self) -> None:
        env, log_path = self._make_fake_environment(
            ps_output="",
            browser_cache_entries=("chromium-1234",),
            browser_sentinel_version="1.55.0",
        )

        result = self._run_script(env, "install")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertNotIn("npm\trun e2e:install", log_lines)
        self.assertFalse(any(line.startswith("podman") for line in log_lines), log_lines)
        self.assertFalse(any(line.startswith("curl") for line in log_lines), log_lines)

    def test_install_refreshes_frontend_dependencies_when_lockfile_is_newer(self) -> None:
        env, log_path = self._make_fake_environment(
            ps_output="",
            browser_cache_entries=("chromium-1234",),
            browser_sentinel_version="1.55.0",
            stale_dependencies=True,
        )

        result = self._run_script(env, "install")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertIn("npm\tci", log_lines)

    def test_up_command_starts_stack_without_reset_or_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="")

        result = self._run_script(env, "up")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertTrue(any(line.startswith("curl\t") for line in log_lines), log_lines)
        self.assertFalse(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_reset_command_reuses_stack_and_resets_without_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "reset")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_run_no_reset_skips_reset_and_still_runs_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--no-reset")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_default_run_forwards_headed_and_ui_flags_to_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "--headed", "--ui")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:auth-profile -- --headed --ui", log_lines)

    def test_run_no_reset_forwards_ui_flag_to_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--no-reset", "--ui")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:auth-profile -- --ui", log_lines)

    def test_non_run_commands_reject_playwright_flags_without_side_effects(self) -> None:
        for args in (("up", "--headed"), ("reset", "--ui"), ("install", "--headed"), ("down", "--ui")):
            with self.subTest(args=args):
                env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

                result = self._run_script(env, *args)

                self.assertEqual(
                    result.returncode,
                    1,
                    msg=f"script unexpectedly succeeded for {args}:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
                )
                self.assertIn(
                    "Playwright flags are only supported for the default run command",
                    result.stderr,
                )
                self.assertIn("Usage:", result.stderr)
                self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_down_command_stops_the_e2e_stack_without_running_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "down")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            log_lines,
            [
                "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml down",
            ],
        )