import json
import os
import subprocess
import sys
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

    @classmethod
    def _organizations_reset_state_dir(cls) -> Path:
        return cls._repo_root() / ".e2e-reset-state"

    @classmethod
    def _elections_reset_state_dir(cls) -> Path:
        return cls._repo_root() / ".e2e-reset-state"

    @classmethod
    def _self_service_reset_state_dir(cls) -> Path:
        return cls._repo_root() / ".e2e-reset-state"

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
            "  *\" exec -T web python manage.py membership_committee_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"membership-committee\\\", \\\"status\\\": \\\"reset\\\"}\\n'\n"
            "    ;;\n"
            "  *\" exec -T web python manage.py organizations_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"organizations\\\", \\\"status\\\": \\\"reset\\\", \\\"claim_routes\\\": {\\\"organizations-claim-happy-path\\\": \\\"/organizations/claim/happy-token/\\\", \\\"organizations-claim-already-claimed\\\": \\\"/organizations/claim/already-claimed-token/\\\"}, \\\"actors\\\": {\\\"representative_observer\\\": {\\\"username\\\": \\\"regular11\\\"}, \\\"claim_happy_actor\\\": {\\\"username\\\": \\\"regular12\\\"}, \\\"claim_rejection_actor\\\": {\\\"username\\\": \\\"regular13\\\"}}}\\n'\n"
            "    ;;\n"
            "  *\" exec -T web python manage.py elections_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"elections\\\", \\\"status\\\": \\\"reset\\\", \\\"actors\\\": {\\\"viewer\\\": {\\\"username\\\": \\\"regular16\\\"}, \\\"manager\\\": {\\\"username\\\": \\\"regular17\\\"}}, \\\"elections\\\": {\\\"open_list_election\\\": {\\\"id\\\": 101, \\\"route\\\": \\\"/elections/101/\\\"}, \\\"past_list_election\\\": {\\\"id\\\": 102, \\\"route\\\": \\\"/elections/102/\\\"}, \\\"draft_manager_election\\\": {\\\"id\\\": 103, \\\"route\\\": \\\"/elections/103/edit/\\\"}, \\\"manager_open_election\\\": {\\\"id\\\": 104, \\\"route\\\": \\\"/elections/104/\\\"}, \\\"detail_open_election\\\": {\\\"id\\\": 101, \\\"route\\\": \\\"/elections/101/\\\"}, \\\"detail_tallied_election\\\": {\\\"id\\\": 202, \\\"route\\\": \\\"/elections/202/\\\"}}, \\\"receipts\\\": {\\\"verify_closed_receipt\\\": {\\\"ballot_hash\\\": \\\"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\\\", \\\"verification_state\\\": \\\"closed\\\"}, \\\"verify_tallied_receipt\\\": {\\\"ballot_hash\\\": \\\"tttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt\\\", \\\"verification_state\\\": \\\"tallied\\\"}, \\\"verify_superseded_receipt\\\": {\\\"ballot_hash\\\": \\\"ssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss\\\", \\\"verification_state\\\": \\\"superseded\\\"}}, \\\"routes\\\": {\\\"ballot_verify\\\": \\\"/elections/ballot/verify/\\\", \\\"open_detail\\\": \\\"/elections/101/\\\", \\\"tallied_detail\\\": \\\"/elections/202/\\\"}}\\n'\n"
            "    ;;\n"
            "  *\" exec -T web python manage.py groups_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"groups\\\", \\\"status\\\": \\\"reset\\\"}\\n'\n"
            "    ;;\n"
            "  *\" exec -T web python manage.py membership_selfservice_reset\"*)\n"
            "    printf '{\\\"scenario\\\": \\\"membership-self-service\\\", \\\"status\\\": \\\"reset\\\", \\\"routes\\\": {\\\"create\\\": \\\"/membership/request/\\\", \\\"profiles\\\": {\\\"regular04\\\": \\\"/user/regular04/\\\"}, \\\"settings_membership\\\": {\\\"regular03\\\": \\\"/settings/?tab=membership\\\"}}, \\\"settings\\\": {\\\"membership\\\": {\\\"actor_username\\\": \\\"regular03\\\", \\\"route\\\": \\\"/settings/?tab=membership\\\", \\\"active_membership_alias\\\": \\\"regular03_active_mirror_membership\\\", \\\"active_membership\\\": {\\\"membership_type_code\\\": \\\"mirror\\\"}, \\\"ordered_history_aliases\\\": [\\\"regular03_history_expiry_changed\\\", \\\"regular03_history_approved\\\", \\\"regular03_history_requested\\\"], \\\"history_rows\\\": {\\\"regular03_history_expiry_changed\\\": {\\\"action\\\": \\\"expiry_changed\\\"}}}}, \\\"requests\\\": {\\\"resubmit_on_hold\\\": {\\\"detail_route\\\": \\\"/membership/request/44/\\\"}}}\\n'\n"
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
            "printf 'npm\\t%s\\n' \"$*\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "if [[ -n \"${ASTRA_E2E_RESET_STATE_FILE:-}\" ]]; then\n"
            "  printf 'npm_env\\tASTRA_E2E_RESET_STATE_FILE=%s\\n' \"$ASTRA_E2E_RESET_STATE_FILE\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "  if [[ -f \"$ASTRA_E2E_RESET_STATE_FILE\" ]]; then\n"
            f"    \"{sys.executable}\" - <<'PY' >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "payload = json.loads(Path(os.environ[\"ASTRA_E2E_RESET_STATE_FILE\"]).read_text(encoding=\"utf-8\"))\n"
            "print(f\"npm_env_claim_route\\t{payload['claim_routes']['organizations-claim-happy-path']}\")\n"
            "PY\n"
            "  fi\n"
            "fi\n"
            "if [[ -n \"${ASTRA_E2E_ELECTIONS_RESET_STATE_FILE:-}\" ]]; then\n"
            "  printf 'npm_env\\tASTRA_E2E_ELECTIONS_RESET_STATE_FILE=%s\\n' \"$ASTRA_E2E_ELECTIONS_RESET_STATE_FILE\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "  if [[ -f \"$ASTRA_E2E_ELECTIONS_RESET_STATE_FILE\" ]]; then\n"
            f"    \"{sys.executable}\" - <<'PY' >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "payload = json.loads(Path(os.environ[\"ASTRA_E2E_ELECTIONS_RESET_STATE_FILE\"]).read_text(encoding=\"utf-8\"))\n"
            "print(f\"npm_env_elections_route\\t{payload['routes']['ballot_verify']}\")\n"
            "PY\n"
            "  fi\n"
            "fi\n"
            "if [[ -n \"${ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE:-}\" ]]; then\n"
            "  printf 'npm_env\\tASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE=%s\\n' \"$ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE\" >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "  if [[ -f \"$ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE\" ]]; then\n"
            f"    \"{sys.executable}\" - <<'PY' >> \"$ASTRA_E2E_TEST_LOG\"\n"
            "import json\n"
            "import os\n"
            "from pathlib import Path\n"
            "payload = json.loads(Path(os.environ[\"ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE\"]).read_text(encoding=\"utf-8\"))\n"
            "print(f\"npm_env_self_service_route\\t{payload['settings']['membership']['route']}\")\n"
            "PY\n"
            "  fi\n"
            "fi\n"
            "if [[ -n \"${ASTRA_E2E_TEST_NPM_FAIL_MATCH:-}\" && \"$*\" == *\"$ASTRA_E2E_TEST_NPM_FAIL_MATCH\"* && \"${ASTRA_E2E_TEST_NPM_EXIT_CODE:-0}\" != \"0\" ]]; then\n"
            "  exit \"$ASTRA_E2E_TEST_NPM_EXIT_CODE\"\n"
            "fi\n",
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

    def test_frontend_default_e2e_script_stays_on_green_auth_surface(self) -> None:
        package_json = json.loads((self._repo_root() / "frontend" / "package.json").read_text(encoding="utf-8"))
        scripts = package_json["scripts"]

        self.assertEqual(scripts["e2e"], "playwright test e2e/auth --project=chromium")
        self.assertEqual(scripts["e2e:raw"], "playwright test")
        self.assertEqual(scripts["e2e:wave1"], "playwright test e2e/auth --project=chromium")
        self.assertEqual(
            scripts["e2e:membership-committee"],
            "playwright test e2e/membership/committee-queue.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:membership-invitations"],
            "playwright test e2e/membership/account-invitations.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:organizations"],
            "playwright test e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:groups"],
            "playwright test e2e/groups/list-detail.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:groups:management:evidence"],
            "playwright test e2e/groups/management.evidence.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:elections"],
            "playwright test e2e/elections/list-detail.spec.ts e2e/elections/routes-shell.spec.ts e2e/elections/ballot-verify.spec.ts --project=chromium --workers=1",
        )
        self.assertEqual(
            scripts["e2e:elections:evidence"],
            "playwright test e2e/elections/lifecycle.evidence.spec.ts --project=chromium --workers=1",
        )
        self.assertEqual(
            scripts["e2e:membership-settings"],
            "playwright test e2e/membership/settings-membership.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:mail-tools"],
            "playwright test e2e/mail-tools.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:shell-routes"],
            "playwright test e2e/shell-routes.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:reports-admin"],
            "playwright test e2e/reports-admin.spec.ts --project=chromium",
        )
        self.assertEqual(
            scripts["e2e:membership-self-service:evidence"],
            "playwright test e2e/membership/self-service-entry.evidence.spec.ts e2e/membership/self-service-detail.evidence.spec.ts --project=chromium",
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
        self.assertIn("list-projects", result.stdout)
        self.assertIn("cleanup-stale", result.stdout)
        self.assertIn("reset", result.stdout)
        self.assertIn("--headed", result.stdout)
        self.assertIn("--ui", result.stdout)
        self.assertIn("--no-rebuild", result.stdout)
        self.assertIn("membership-committee", result.stdout)
        self.assertIn("membership-invitations", result.stdout)
        self.assertIn("organizations", result.stdout)
        self.assertIn("groups", result.stdout)
        self.assertIn("groups-management-evidence", result.stdout)
        self.assertIn("elections", result.stdout)
        self.assertIn("membership-settings", result.stdout)
        self.assertIn("mail-tools", result.stdout)
        self.assertIn("shell-routes", result.stdout)
        self.assertIn("reports-admin", result.stdout)
        self.assertIn("committee-pending-bulk-accept", result.stdout)
        self.assertIn("invitations-pending-bulk-resend", result.stdout)
        self.assertIn("organizations-claim-happy-path", result.stdout)
        self.assertIn("groups-detail-leaders-pagination", result.stdout)
        self.assertIn("elections-detail-tallied-results", result.stdout)
        self.assertIn("membership-settings-shell", result.stdout)
        self.assertIn("mail-tools-send-mail-workflow", result.stdout)
        self.assertIn("shell-routes-users-search-and-static-links", result.stdout)
        self.assertIn("reports-admin-audit-sponsors-stats", result.stdout)
        self.assertIn("membership-profile-pending-links", result.stdout)
        self.assertIn("no groups-specific reset-state file", result.stdout)
        self.assertIn("ASTRA_E2E_RESET_STATE_FILE", result.stdout)
        self.assertIn("ASTRA_E2E_ELECTIONS_RESET_STATE_FILE", result.stdout)
        self.assertIn("ASTRA_E2E_ELECTIONS_RESET_STATE_PATH", result.stdout)
        self.assertIn("ASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE", result.stdout)
        self.assertIn("ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH", result.stdout)
        self.assertIn("When no theme, scenario, or spec path is supplied", result.stdout)
        self.assertIn("suite under Chromium.", result.stdout)

    def test_run_theme_mail_tools_runs_auth_reset_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "mail-tools")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:mail-tools")

        self.assertLess(auth_reset_index, playwright_index)
        self.assertEqual(sum("membership_committee_reset" in line for line in log_lines), 0, log_lines)
        self.assertEqual(sum("account_invitations_reset" in line for line in log_lines), 0, log_lines)
        self.assertEqual(sum("organizations_reset" in line for line in log_lines), 0, log_lines)

    def test_run_theme_shell_routes_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "shell-routes")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        committee_reset_index = next(index for index, line in enumerate(log_lines) if "membership_committee_reset" in line)
        invitations_reset_index = next(index for index, line in enumerate(log_lines) if "account_invitations_reset" in line)
        organizations_reset_index = next(index for index, line in enumerate(log_lines) if "organizations_reset" in line)
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:shell-routes")

        self.assertLess(auth_reset_index, committee_reset_index)
        self.assertLess(committee_reset_index, invitations_reset_index)
        self.assertLess(invitations_reset_index, organizations_reset_index)
        self.assertLess(organizations_reset_index, playwright_index)

    def test_run_theme_reports_admin_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "reports-admin")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        committee_reset_index = next(index for index, line in enumerate(log_lines) if "membership_committee_reset" in line)
        organizations_reset_index = next(index for index, line in enumerate(log_lines) if "organizations_reset" in line)
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:reports-admin")

        self.assertLess(auth_reset_index, committee_reset_index)
        self.assertLess(committee_reset_index, organizations_reset_index)
        self.assertLess(organizations_reset_index, playwright_index)
        self.assertEqual(sum("account_invitations_reset" in line for line in log_lines), 0, log_lines)

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
            "npm\trun e2e:auth",
        ]

        self.assertEqual(
            [line for line in log_lines if any(fragment in line for fragment in expected_fragments)],
            expected_fragments,
        )
        self.assertNotIn("npm\tci", log_lines)

    def test_default_run_without_selectors_stays_on_auth_only_surface(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()

        self.assertIn("npm\trun e2e:auth", log_lines)
        self.assertNotIn("npm\trun e2e:raw -- --project=chromium", log_lines)
        self.assertFalse(any("membership_committee_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("account_invitations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("organizations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)

    def test_default_run_recreates_web_when_starting_a_stopped_stack(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="")

        result = self._run_script(env, "run", "--theme", "membership-settings")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        startup_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d db minio minio_init web"
        )
        rebuild_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --force-recreate --no-deps web"
        )
        membership_reset_index = next(
            index for index, line in enumerate(log_lines) if "membership_selfservice_reset" in line
        )

        self.assertLess(startup_index, rebuild_index)
        self.assertLess(rebuild_index, membership_reset_index)

    def test_default_run_reuses_running_stack_and_recreates_web_service(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env)

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertIn(
            "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --force-recreate --no-deps web",
            log_lines,
        )
        self.assertFalse(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertTrue(any("exec -T web python manage.py auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_committee_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("account_invitations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("organizations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any(line == "npm\trun e2e:auth" for line in log_lines), log_lines)

    def test_run_no_rebuild_reuses_running_stack_without_recreating_web_service(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--no-rebuild")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(
            any("up -d --build --force-recreate --no-deps web" in line for line in log_lines),
            log_lines,
        )
        self.assertTrue(any("exec -T web python manage.py auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any(line == "npm\trun e2e:auth" for line in log_lines), log_lines)

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
        startup_index = next(
            index for index, line in enumerate(log_lines) if " up -d db minio minio_init web" in line
        )
        rebuild_index = next(
            index
            for index, line in enumerate(log_lines)
            if line
            == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --force-recreate --no-deps web"
        )
        ready_index = next(index for index, line in enumerate(log_lines) if line.startswith("curl\t"))

        self.assertLess(startup_index, rebuild_index)
        self.assertLess(rebuild_index, ready_index)
        self.assertFalse(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_up_no_rebuild_starts_stack_without_recreating_web_service(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="")

        result = self._run_script(env, "up", "--no-rebuild")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertFalse(
            any("up -d --build --force-recreate --no-deps web" in line for line in log_lines),
            log_lines,
        )
        self.assertTrue(any(line.startswith("curl\t") for line in log_lines), log_lines)

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
        rebuild_index = next(
            index
            for index, line in enumerate(log_lines)
            if line
            == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml up -d --build --force-recreate --no-deps web"
        )
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)

        self.assertLess(rebuild_index, auth_reset_index)
        self.assertFalse(any(line == "npm\trun e2e:auth-profile" for line in log_lines), log_lines)

    def test_reset_no_rebuild_reuses_stack_without_recreating_web_service(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "reset", "--no-rebuild")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any(" up -d db minio minio_init web" in line for line in log_lines), log_lines)
        self.assertFalse(
            any("up -d --build --force-recreate --no-deps web" in line for line in log_lines),
            log_lines,
        )
        self.assertTrue(any("exec -T web python manage.py auth_profile_reset" in line for line in log_lines), log_lines)

    def test_run_no_reset_skips_reset_and_still_runs_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--no-reset")

        self.assertEqual(result.returncode, 1)
        self.assertIn("--no-reset is only supported for auth-only runs", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_theme_auth_uses_auth_reset_and_auth_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "auth")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:auth", log_lines)

    def test_run_theme_membership_self_service_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "membership-self-service")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        membership_reset_index = next(index for index, line in enumerate(log_lines) if "membership_selfservice_reset" in line)
        playwright_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "npm\trun e2e:membership-self-service:evidence"
        )
        reset_state_line = next(
            line for line in log_lines if line.startswith("npm_env\tASTRA_E2E_SELF_SERVICE_RESET_STATE_FILE=")
        )
        settings_route_line = next(line for line in log_lines if line.startswith("npm_env_self_service_route\t"))
        reset_state_path = Path(reset_state_line.split("=", 1)[1])
        self.assertLess(auth_reset_index, membership_reset_index)
        self.assertLess(membership_reset_index, playwright_index)
        self.assertEqual(settings_route_line, "npm_env_self_service_route\t/settings/?tab=membership")
        self.assertFalse(reset_state_path.exists())

    def test_run_theme_membership_settings_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "membership-settings")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        membership_reset_index = next(index for index, line in enumerate(log_lines) if "membership_selfservice_reset" in line)
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:membership-settings")
        self.assertLess(auth_reset_index, membership_reset_index)
        self.assertLess(membership_reset_index, playwright_index)

    def test_run_theme_membership_self_service_cleans_up_generated_reset_state_file_after_playwright_failure(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        env["ASTRA_E2E_TEST_NPM_FAIL_MATCH"] = "run e2e:membership-self-service:evidence"
        env["ASTRA_E2E_TEST_NPM_EXIT_CODE"] = "11"
        before_paths = set(self._self_service_reset_state_dir().glob("membership-selfservice-reset-state.*.json"))

        result = self._run_script(env, "run", "--theme", "membership-self-service")

        self.assertEqual(result.returncode, 11)

        after_paths = set(self._self_service_reset_state_dir().glob("membership-selfservice-reset-state.*.json"))
        self.assertEqual(after_paths - before_paths, set(), log_path.read_text(encoding="utf-8"))

    def test_run_theme_membership_settings_honors_caller_supplied_reset_state_path_without_cleanup(self) -> None:
        env, _ = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(temp_dir)], check=False))
        caller_owned_path = temp_dir / "caller-owned-self-service-reset.json"
        env["ASTRA_E2E_SELF_SERVICE_RESET_STATE_PATH"] = str(caller_owned_path)

        result = self._run_script(env, "run", "--theme", "membership-settings")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertTrue(caller_owned_path.exists())
        payload = json.loads(caller_owned_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["scenario"], "membership-self-service")
        self.assertEqual(payload["settings"]["membership"]["route"], "/settings/?tab=membership")

    def test_run_theme_membership_committee_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "membership-committee")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        committee_reset_index = next(index for index, line in enumerate(log_lines) if "membership_committee_reset" in line)
        playwright_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "npm\trun e2e:membership-committee"
        )
        self.assertLess(auth_reset_index, committee_reset_index)
        self.assertLess(committee_reset_index, playwright_index)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)

    def test_run_theme_membership_invitations_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "membership-invitations")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        invitations_reset_index = next(index for index, line in enumerate(log_lines) if "account_invitations_reset" in line)
        playwright_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "npm\trun e2e:membership-invitations"
        )
        self.assertLess(auth_reset_index, invitations_reset_index)
        self.assertLess(invitations_reset_index, playwright_index)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)

    def test_run_theme_organizations_runs_ordered_resets_and_exports_reset_state(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "organizations")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        organizations_reset_index = next(index for index, line in enumerate(log_lines) if "organizations_reset" in line)
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:organizations")
        reset_state_line = next(line for line in log_lines if line.startswith("npm_env\tASTRA_E2E_RESET_STATE_FILE="))
        claim_route_line = next(line for line in log_lines if line.startswith("npm_env_claim_route\t"))
        reset_state_path = Path(reset_state_line.split("=", 1)[1])

        self.assertLess(auth_reset_index, organizations_reset_index)
        self.assertLess(organizations_reset_index, playwright_index)
        self.assertEqual(claim_route_line, "npm_env_claim_route\t/organizations/claim/happy-token/")
        self.assertFalse(reset_state_path.exists())

    def test_run_theme_elections_runs_ordered_resets_and_exports_reset_state(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "elections")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        elections_reset_index = next(index for index, line in enumerate(log_lines) if "elections_reset" in line)
        restart_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml restart web"
        )
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:elections")
        reset_state_line = next(
            line for line in log_lines if line.startswith("npm_env\tASTRA_E2E_ELECTIONS_RESET_STATE_FILE=")
        )
        elections_route_line = next(line for line in log_lines if line.startswith("npm_env_elections_route\t"))
        reset_state_path = Path(reset_state_line.split("=", 1)[1])

        self.assertLess(auth_reset_index, elections_reset_index)
        self.assertLess(elections_reset_index, restart_index)
        self.assertLess(restart_index, playwright_index)
        self.assertLess(elections_reset_index, playwright_index)
        self.assertEqual(elections_route_line, "npm_env_elections_route\t/elections/ballot/verify/")
        self.assertFalse(reset_state_path.exists())

    def test_run_theme_groups_runs_ordered_resets_and_theme_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "groups")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        groups_reset_index = next(index for index, line in enumerate(log_lines) if "groups_reset" in line)
        restart_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml restart web"
        )
        playwright_index = next(index for index, line in enumerate(log_lines) if line == "npm\trun e2e:groups")

        self.assertLess(auth_reset_index, groups_reset_index)
        self.assertLess(groups_reset_index, restart_index)
        self.assertLess(restart_index, playwright_index)
        self.assertLess(groups_reset_index, playwright_index)
        self.assertIn('"scenario": "groups"', result.stdout)
        self.assertIn('"status": "reset"', result.stdout)
        self.assertFalse(any("ASTRA_E2E_RESET_STATE_FILE" in line for line in log_lines), log_lines)

    def test_run_theme_elections_uses_unique_reset_state_path_per_invocation(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        first_result = self._run_script(env, "run", "--theme", "elections")
        second_result = self._run_script(env, "run", "--theme", "elections")

        self.assertEqual(first_result.returncode, 0, msg=first_result.stderr)
        self.assertEqual(second_result.returncode, 0, msg=second_result.stderr)

        reset_state_lines = [
            line
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("npm_env\tASTRA_E2E_ELECTIONS_RESET_STATE_FILE=")
        ]

        self.assertEqual(len(reset_state_lines), 2, reset_state_lines)
        first_path = Path(reset_state_lines[0].split("=", 1)[1])
        second_path = Path(reset_state_lines[1].split("=", 1)[1])
        self.assertNotEqual(first_path, second_path)

    def test_run_theme_organizations_uses_unique_reset_state_path_per_invocation(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        first_result = self._run_script(env, "run", "--theme", "organizations")
        second_result = self._run_script(env, "run", "--theme", "organizations")

        self.assertEqual(
            first_result.returncode,
            0,
            msg=f"first organizations run failed:\nstdout:\n{first_result.stdout}\nstderr:\n{first_result.stderr}",
        )
        self.assertEqual(
            second_result.returncode,
            0,
            msg=f"second organizations run failed:\nstdout:\n{second_result.stdout}\nstderr:\n{second_result.stderr}",
        )

        reset_state_lines = [
            line
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("npm_env\tASTRA_E2E_RESET_STATE_FILE=")
        ]

        self.assertEqual(len(reset_state_lines), 2, reset_state_lines)
        first_path = Path(reset_state_lines[0].split("=", 1)[1])
        second_path = Path(reset_state_lines[1].split("=", 1)[1])

        self.assertNotEqual(first_path, second_path)

    def test_run_theme_organizations_cleans_up_generated_reset_state_file_after_success(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        before_paths = set(self._organizations_reset_state_dir().glob("organizations-reset-state.*.json"))

        result = self._run_script(env, "run", "--theme", "organizations")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        after_paths = set(self._organizations_reset_state_dir().glob("organizations-reset-state.*.json"))
        self.assertEqual(after_paths - before_paths, set(), log_path.read_text(encoding="utf-8"))

    def test_run_theme_organizations_cleans_up_generated_reset_state_file_after_playwright_failure(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        env["ASTRA_E2E_TEST_NPM_FAIL_MATCH"] = "run e2e:organizations"
        env["ASTRA_E2E_TEST_NPM_EXIT_CODE"] = "7"
        before_paths = set(self._organizations_reset_state_dir().glob("organizations-reset-state.*.json"))

        result = self._run_script(env, "run", "--theme", "organizations")

        self.assertEqual(result.returncode, 7)

        after_paths = set(self._organizations_reset_state_dir().glob("organizations-reset-state.*.json"))
        self.assertEqual(after_paths - before_paths, set(), log_path.read_text(encoding="utf-8"))

    def test_run_theme_elections_cleans_up_generated_reset_state_file_after_success(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        before_paths = set(self._elections_reset_state_dir().glob("elections-reset-state.*.json"))

        result = self._run_script(env, "run", "--theme", "elections")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        after_paths = set(self._elections_reset_state_dir().glob("elections-reset-state.*.json"))
        self.assertEqual(after_paths - before_paths, set(), log_path.read_text(encoding="utf-8"))

    def test_run_theme_elections_cleans_up_generated_reset_state_file_after_playwright_failure(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        env["ASTRA_E2E_TEST_NPM_FAIL_MATCH"] = "run e2e:elections"
        env["ASTRA_E2E_TEST_NPM_EXIT_CODE"] = "9"
        before_paths = set(self._elections_reset_state_dir().glob("elections-reset-state.*.json"))

        result = self._run_script(env, "run", "--theme", "elections")

        self.assertEqual(result.returncode, 9)

        after_paths = set(self._elections_reset_state_dir().glob("elections-reset-state.*.json"))
        self.assertEqual(after_paths - before_paths, set(), log_path.read_text(encoding="utf-8"))

    def test_run_theme_elections_honors_caller_supplied_reset_state_path_without_cleanup(self) -> None:
        env, _ = self._make_fake_environment(ps_output="astra-e2e_web_1\n")
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(temp_dir)], check=False))
        caller_owned_path = temp_dir / "caller-owned-elections-reset.json"
        env["ASTRA_E2E_ELECTIONS_RESET_STATE_PATH"] = str(caller_owned_path)

        result = self._run_script(env, "run", "--theme", "elections")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertTrue(caller_owned_path.exists())
        payload = json.loads(caller_owned_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["scenario"], "elections")
        self.assertEqual(payload["routes"]["ballot_verify"], "/elections/ballot/verify/")

    def test_run_three_green_themes_deduplicates_resets_and_runs_union_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(
            env,
            "run",
            "--theme",
            "auth",
            "--theme",
            "membership-committee",
            "--theme",
            "membership-invitations",
        )

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("auth_profile_reset" in line for line in log_lines), 1, log_lines)
        self.assertEqual(sum("membership_committee_reset" in line for line in log_lines), 1, log_lines)
        self.assertEqual(sum("account_invitations_reset" in line for line in log_lines), 1, log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/auth e2e/membership/committee-queue.spec.ts e2e/membership/account-invitations.spec.ts",
            log_lines,
        )

    def test_run_green_themes_use_raw_union_without_self_service_evidence(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "auth", "--theme", "membership-committee")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("auth_profile_reset" in line for line in log_lines), 1, log_lines)
        self.assertEqual(sum("membership_committee_reset" in line for line in log_lines), 1, log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/auth e2e/membership/committee-queue.spec.ts",
            log_lines,
        )

    def test_run_green_union_with_membership_invitations_uses_raw_union_and_deduplicated_resets(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "auth", "--theme", "membership-invitations")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("auth_profile_reset" in line for line in log_lines), 1, log_lines)
        self.assertEqual(sum("account_invitations_reset" in line for line in log_lines), 1, log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/auth e2e/membership/account-invitations.spec.ts",
            log_lines,
        )

    def test_run_green_union_with_organizations_uses_raw_union_and_deduplicated_resets(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "auth", "--theme", "organizations")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(sum("auth_profile_reset" in line for line in log_lines), 1, log_lines)
        self.assertEqual(sum("organizations_reset" in line for line in log_lines), 1, log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/auth e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts",
            log_lines,
        )

    def test_run_green_union_with_groups_runs_groups_reset_last_and_uses_raw_union(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "organizations", "--theme", "groups")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        organizations_reset_index = next(index for index, line in enumerate(log_lines) if "organizations_reset" in line)
        groups_reset_index = next(index for index, line in enumerate(log_lines) if "groups_reset" in line)
        restart_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml restart web"
        )
        playwright_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "npm\trun e2e:raw -- --project=chromium e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts e2e/groups/list-detail.spec.ts"
        )

        self.assertLess(auth_reset_index, organizations_reset_index)
        self.assertLess(organizations_reset_index, groups_reset_index)
        self.assertLess(groups_reset_index, restart_index)
        self.assertLess(restart_index, playwright_index)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts e2e/groups/list-detail.spec.ts",
            log_lines,
        )

    def test_run_green_union_with_organizations_and_elections_exports_isolated_reset_state_files(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "organizations", "--theme", "elections")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        auth_reset_index = next(index for index, line in enumerate(log_lines) if "auth_profile_reset" in line)
        organizations_reset_index = next(index for index, line in enumerate(log_lines) if "organizations_reset" in line)
        elections_reset_index = next(index for index, line in enumerate(log_lines) if "elections_reset" in line)
        restart_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "podman-compose\t-p astra-e2e --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml restart web"
        )
        playwright_index = next(
            index
            for index, line in enumerate(log_lines)
            if line == "npm\trun e2e:raw -- --project=chromium e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts e2e/elections/list-detail.spec.ts e2e/elections/routes-shell.spec.ts e2e/elections/ballot-verify.spec.ts"
        )
        organizations_reset_state = next(
            line for line in log_lines if line.startswith("npm_env\tASTRA_E2E_RESET_STATE_FILE=")
        )
        elections_reset_state = next(
            line for line in log_lines if line.startswith("npm_env\tASTRA_E2E_ELECTIONS_RESET_STATE_FILE=")
        )
        organizations_path = Path(organizations_reset_state.split("=", 1)[1])
        elections_path = Path(elections_reset_state.split("=", 1)[1])

        self.assertLess(auth_reset_index, organizations_reset_index)
        self.assertLess(organizations_reset_index, elections_reset_index)
        self.assertLess(elections_reset_index, restart_index)
        self.assertLess(restart_index, playwright_index)
        self.assertNotEqual(organizations_path, elections_path)
        self.assertFalse(organizations_path.exists())
        self.assertFalse(elections_path.exists())
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/organizations/list-detail.spec.ts e2e/organizations/claim.spec.ts e2e/elections/list-detail.spec.ts e2e/elections/routes-shell.spec.ts e2e/elections/ballot-verify.spec.ts",
            log_lines,
        )

    def test_run_rejects_self_service_mixed_with_green_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "auth", "--theme", "membership-self-service")

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Evidence-only themes cannot be combined with green themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_rejects_membership_settings_mixed_with_self_service_raw_spec_paths(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(
            env,
            "run",
            "frontend/e2e/membership/settings-membership.spec.ts",
            "frontend/e2e/membership/self-service-entry.evidence.spec.ts",
        )

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Evidence-only themes cannot be combined with green themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_rejects_membership_settings_mixed_with_other_green_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(
            env,
            "run",
            "--theme",
            "membership-settings",
            "--theme",
            "membership-committee",
        )

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("membership-settings cannot be combined with other themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_scenario_maps_to_membership_self_service_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "membership-rescind-pending-individual")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:membership-self-service:evidence -- --grep membership-rescind-pending-individual",
            log_lines,
        )

    def test_run_membership_settings_scenario_maps_to_membership_settings_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "membership-settings-shell")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:membership-settings -- --grep membership-settings-shell", log_lines)

    def test_run_committee_scenario_maps_to_membership_committee_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "committee-pending-bulk-accept")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("membership_committee_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:membership-committee -- --grep committee-pending-bulk-accept",
            log_lines,
        )

    def test_run_invitation_scenario_maps_to_membership_invitations_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "invitations-pending-bulk-resend")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("account_invitations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:membership-invitations -- --grep invitations-pending-bulk-resend",
            log_lines,
        )

    def test_run_organizations_scenario_maps_to_organizations_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "organizations-claim-happy-path")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("organizations_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:organizations -- --grep organizations-claim-happy-path",
            log_lines,
        )

    def test_run_groups_scenario_maps_to_groups_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "groups-detail-leaders-pagination")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:groups -- --grep groups-detail-leaders-pagination",
            log_lines,
        )

    def test_run_theme_groups_management_evidence_runs_groups_reset_and_evidence_command(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "groups-management-evidence")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:groups:management:evidence", log_lines)

    def test_run_elections_scenario_maps_to_elections_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "elections-detail-tallied-results")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:elections -- --grep elections-detail-tallied-results",
            log_lines,
        )

    def test_run_raw_auth_spec_path_uses_unscoped_e2e_raw_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/auth/login.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/auth/login.spec.ts",
            log_lines,
        )

    def test_run_raw_committee_spec_path_maps_to_committee_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/membership/committee-queue.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("membership_committee_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/membership/committee-queue.spec.ts",
            log_lines,
        )

    def test_run_raw_invitation_spec_path_maps_to_invitation_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/membership/account-invitations.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("account_invitations_reset" in line for line in log_lines), log_lines)
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/membership/account-invitations.spec.ts",
            log_lines,
        )

    def test_run_raw_organization_claim_spec_path_maps_to_organizations_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/organizations/claim.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("organizations_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/organizations/claim.spec.ts",
            log_lines,
        )

    def test_run_raw_groups_spec_path_maps_to_groups_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/groups/list-detail.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/groups/list-detail.spec.ts",
            log_lines,
        )

    def test_run_raw_groups_management_evidence_spec_path_uses_evidence_surface(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/groups/management.evidence.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("groups_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:groups:management:evidence", log_lines)

    def test_run_rejects_groups_management_evidence_mixed_with_green_raw_spec(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(
            env,
            "run",
            "frontend/e2e/auth/login.spec.ts",
            "frontend/e2e/groups/management.evidence.spec.ts",
        )

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Evidence-only themes cannot be combined with green themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_raw_elections_spec_path_maps_to_elections_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/elections/list-detail.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/elections/list-detail.spec.ts",
            log_lines,
        )

    def test_run_raw_elections_routes_shell_spec_path_maps_to_elections_theme_and_runs_unscoped_script(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/elections/routes-shell.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertIn(
            "npm\trun e2e:raw -- --project=chromium e2e/elections/routes-shell.spec.ts",
            log_lines,
        )

    def test_run_raw_elections_lifecycle_evidence_spec_path_uses_evidence_surface_and_exports_reset_state(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/elections/lifecycle.evidence.spec.ts")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("auth_profile_reset" in line for line in log_lines), log_lines)
        self.assertTrue(any("elections_reset" in line for line in log_lines), log_lines)
        self.assertTrue(
            any(line.startswith("npm_env\tASTRA_E2E_ELECTIONS_RESET_STATE_FILE=") for line in log_lines),
            log_lines,
        )
        self.assertIn("npm\trun e2e:elections:evidence", log_lines)

    def test_run_rejects_elections_evidence_mixed_with_green_raw_spec(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/auth/login.spec.ts", "frontend/e2e/elections/lifecycle.evidence.spec.ts")

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Evidence-only themes cannot be combined with green themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_rejects_unapproved_raw_organization_spec_path(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/organizations/form.spec.ts")

        self.assertEqual(result.returncode, 1)
        self.assertIn("Unsupported raw spec path: frontend/e2e/organizations/form.spec.ts", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_rejects_retired_monolithic_self_service_raw_spec_path(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "frontend/e2e/membership/self-service-request.spec.ts")

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Unsupported raw spec path: frontend/e2e/membership/self-service-request.spec.ts",
            result.stderr,
        )
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_rejects_mixed_raw_spec_paths_when_one_is_evidence_only(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(
            env,
            "run",
            "frontend/e2e/auth/login.spec.ts",
            "frontend/e2e/membership/self-service-entry.evidence.spec.ts",
        )

        self.assertEqual(
            result.returncode,
            1,
            msg=f"script unexpectedly succeeded:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertIn("Evidence-only themes cannot be combined with green themes", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

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
        self.assertFalse(any("membership_selfservice_reset" in line for line in log_lines), log_lines)
        self.assertIn("npm\trun e2e:auth -- --headed --ui", log_lines)

    def test_run_no_reset_forwards_ui_flag_to_playwright(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--no-reset", "--ui")

        self.assertEqual(result.returncode, 1)
        self.assertIn("--no-reset is only supported for auth-only runs", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_no_reset_rejects_organizations_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "organizations", "--no-reset")

        self.assertEqual(result.returncode, 1)
        self.assertIn("--no-reset is only supported for auth-only runs", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_no_reset_rejects_organizations_scenario(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--scenario", "organizations-claim-happy-path", "--no-reset")

        self.assertEqual(result.returncode, 1)
        self.assertIn("--no-reset is only supported for auth-only runs", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

    def test_run_no_reset_rejects_groups_theme(self) -> None:
        env, log_path = self._make_fake_environment(ps_output="astra-e2e_web_1\n")

        result = self._run_script(env, "run", "--theme", "groups", "--no-reset")

        self.assertEqual(result.returncode, 1)
        self.assertIn("--no-reset is only supported for auth-only runs", result.stderr)
        self.assertEqual(log_path.read_text(encoding="utf-8"), "")

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

    def test_list_projects_prints_detected_current_and_stale_e2e_projects(self) -> None:
        env, log_path = self._make_fake_environment(
            ps_output=(
                "astra-e2e_web_1\n"
                "astra-e2e_db_1\n"
                "astra-e2e-qafinal352_db_1\n"
                "astra-e2e-qafinal353_minio_1\n"
                "astra-e2e-qarecheck350_web_1\n"
                "unrelated_container\n"
            )
        )

        result = self._run_script(env, "list-projects")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "astra-e2e",
                "astra-e2e-qafinal352",
                "astra-e2e-qafinal353",
                "astra-e2e-qarecheck350",
            ],
        )
        self.assertEqual(
            log_path.read_text(encoding="utf-8").splitlines(),
            ["podman\tps --all --format {{.Names}}"],
        )

    def test_cleanup_stale_stops_only_non_default_e2e_projects(self) -> None:
        env, log_path = self._make_fake_environment(
            ps_output=(
                "astra-e2e_web_1\n"
                "astra-e2e-qafinal352_db_1\n"
                "astra-e2e-qafinal353_minio_1\n"
                "astra-e2e-qafinal353_web_1\n"
                "astra-e2e-qarecheck350_web_1\n"
            )
        )

        result = self._run_script(env, "cleanup-stale")

        self.assertEqual(
            result.returncode,
            0,
            msg=f"script failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )

        log_lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            log_lines,
            [
                "podman\tps --all --format {{.Names}}",
                "podman-compose\t-p astra-e2e-qafinal352 --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml down",
                "podman-compose\t-p astra-e2e-qafinal353 --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml down",
                "podman-compose\t-p astra-e2e-qarecheck350 --env-file .env.e2e -f docker-compose.yml -f docker-compose.e2e.yml down",
            ],
        )
        self.assertEqual(
            result.stdout.splitlines(),
            [
                "Removing stale E2E project: astra-e2e-qafinal352",
                "Removing stale E2E project: astra-e2e-qafinal353",
                "Removing stale E2E project: astra-e2e-qarecheck350",
            ],
        )