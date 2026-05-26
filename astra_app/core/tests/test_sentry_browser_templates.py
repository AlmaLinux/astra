import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from core.context_processors import build_info
from core.freeipa.user import FreeIPAUser
from core.models import Election


class SentryBrowserContextTests(SimpleTestCase):
    def test_sentry_browser_entrypoint_exists(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.assertTrue((repo_root / "frontend/src/entrypoints/sentryBrowser.ts").exists())

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_build_info_exposes_sentry_browser_bundle_and_config(self) -> None:
        with patch("core.context_processors.sentry_sdk.get_traceparent", return_value="trace-parent"), patch(
            "core.context_processors.sentry_sdk.get_baggage",
            return_value="sentry-sample=value",
        ):
            context = build_info(SimpleNamespace())

        self.assertEqual(
            context["sentry_browser_bundle_src"],
            "src/entrypoints/sentryBrowser.ts",
        )
        self.assertEqual(
            context["sentry_browser_config"],
            {
                "dsn": "https://public@example.ingest.sentry.io/1",
                "environment": "staging",
                "release": "build-123",
                "sendDefaultPii": False,
                "tracesSampleRate": 0.25,
                "replaysSessionSampleRate": 0.0,
                "replaysOnErrorSampleRate": 0.0,
                "tunnel": "/_ci/envelope/",
            },
        )
        self.assertEqual(context["sentry_trace"], "trace-parent")
        self.assertEqual(context["sentry_baggage"], "sentry-sample=value")

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_build_info_browser_config_disables_default_pii(self) -> None:
        context = build_info(SimpleNamespace())

        config = context["sentry_browser_config"]

        assert config is not None
        self.assertIn("sendDefaultPii", config)
        self.assertFalse(config["sendDefaultPii"])

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_build_info_exposes_replay_sample_rates(self) -> None:
        context = build_info(SimpleNamespace())

        config = context["sentry_browser_config"]

        assert config is not None
        self.assertEqual(config["replaysSessionSampleRate"], 0.0)
        self.assertEqual(config["replaysOnErrorSampleRate"], 0.0)

    @override_settings(SENTRY_DSN="")
    def test_build_info_omits_sentry_browser_config_without_dsn(self) -> None:
        context = build_info(SimpleNamespace())

        self.assertEqual(context["sentry_browser_bundle_src"], "")
        self.assertIsNone(context["sentry_browser_config"])
        self.assertEqual(context["sentry_trace"], "")
        self.assertEqual(context["sentry_baggage"], "")


class SentryBrowserTemplateTests(TestCase):
    def _login_as_freeipa(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _assert_rendered_page_disables_sentry_replay(self, response) -> None:
        self.assertContains(response, 'id="sentry-browser-config"')
        self.assertContains(response, "window.Sentry && window.Sentry.init")
        self.assertContains(response, '<meta name="sentry-replay-disabled" content="true">', html=True)
        self.assertContains(response, 'data-sentry-replay-disabled=""')
        self.assertNotContains(response, "window.Sentry.replayIntegration")

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_groups_page_includes_sentry_browser_bundle_and_tunnel_config(self) -> None:
        username = "admin"
        self._login_as_freeipa(username)

        freeipa_user = FreeIPAUser(
            username,
            {
                "uid": [username],
                "givenname": ["A"],
                "sn": ["Dmin"],
                "mail": ["admin@example.com"],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=freeipa_user), patch(
            "core.context_processors.sentry_sdk.get_traceparent",
            return_value="trace-parent",
        ), patch(
            "core.context_processors.sentry_sdk.get_baggage",
            return_value="sentry-sample=value",
        ):
            response = self.client.get("/groups/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'src="http://localhost:5173/src/entrypoints/sentryBrowser.ts"',
        )
        self.assertContains(response, 'id="sentry-browser-config"')
        self.assertContains(response, '<meta name="sentry-trace" content="trace-parent">', html=True)
        self.assertContains(response, '<meta name="baggage" content="sentry-sample=value">', html=True)
        self.assertContains(response, "window.Sentry && window.Sentry.init")
        self.assertContains(response, "window.Sentry.feedbackIntegration")
        self.assertContains(response, "window.Sentry.replayIntegration")
        self.assertContains(response, "window.Sentry.browserSessionIntegration")
        self.assertContains(response, "maskAllText: false")
        self.assertContains(response, "maskAllInputs: true")
        self.assertContains(response, "blockAllMedia: false")
        self.assertContains(response, '"environment": "staging"')
        self.assertContains(response, '"release": "build-123"')
        self.assertContains(response, '"sendDefaultPii": false')
        self.assertContains(response, '"tracesSampleRate": 0.25')
        self.assertContains(response, '"replaysSessionSampleRate": 0.0')
        self.assertContains(response, '"replaysOnErrorSampleRate": 0.0')
        self.assertContains(response, '"tunnel": "/_ci/envelope/"')
        self.assertContains(response, 'data-sentry-feedback-link=""')
        self.assertContains(response, '>Contact Support<', html=False)
        self.assertContains(response, 'href="mailto:astra@almalinux.org"', html=False)
        self.assertNotContains(response, '>Support<', html=False)
        self.assertNotContains(response, 'Report a bug')

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_login_page_disables_sentry_capture_and_replay(self) -> None:
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<meta name="sentry-replay-disabled" content="true">', html=True)
        self.assertContains(response, 'data-sentry-replay-disabled=""')
        self.assertContains(response, "window.Sentry.feedbackIntegration")
        self.assertNotContains(response, "window.Sentry.replayIntegration")

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_settings_security_shell_runtime_disables_sentry_replay(self) -> None:
        self._login_as_freeipa("alice")
        otp_client = SimpleNamespace(otptoken_find=lambda **_kwargs: {"result": []})
        fake_user = SimpleNamespace(
            username="alice",
            email="alice@example.org",
            is_authenticated=True,
            groups_list=[],
            _user_data={
                "givenname": ["Alice"],
                "sn": ["User"],
                "cn": ["Alice User"],
                "mail": ["alice@example.org"],
                "fasstatusnote": ["US"],
            },
        )

        with (
            patch("core.views_settings._get_full_user", autospec=True, return_value=fake_user),
            patch("core.views_settings.has_enabled_agreements", autospec=True, return_value=False),
            patch("core.views_settings._get_freeipa_client", autospec=True, return_value=otp_client),
        ):
            response = self.client.get("/settings/?tab=security")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-settings-root=""')
        self._assert_rendered_page_disables_sentry_replay(response)

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_otp_sync_runtime_disables_sentry_replay(self) -> None:
        response = self.client.get("/otp/sync/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-auth-recovery-otp-sync-root=""')
        self._assert_rendered_page_disables_sentry_replay(response)

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_election_vote_runtime_disables_sentry_replay(self) -> None:
        self._login_as_freeipa("voter")

        now = timezone.now()
        election = Election.objects.create(
            name="Board election",
            description="",
            start_datetime=now - datetime.timedelta(days=1),
            end_datetime=now + datetime.timedelta(days=1),
            number_of_seats=1,
            status=Election.Status.open,
        )
        voter = FreeIPAUser("voter", {"uid": ["voter"], "memberof_group": []})

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=voter),
            patch("core.views_elections.vote.block_action_without_coc", return_value=None),
            patch(
                "core.views_elections.vote._election_vote_page_context",
                return_value={
                    "election": election,
                    "candidates": [],
                    "voter_votes": None,
                    "voter_vote_breakdown": [],
                    "can_submit_vote": False,
                },
            ) as page_context,
        ):
            response = self.client.get(f"/elections/{election.id}/vote/")

        self.assertEqual(response.status_code, 200)
        page_context.assert_not_called()
        self.assertContains(response, "data-election-vote-root")
        self._assert_rendered_page_disables_sentry_replay(response)

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_REPLAY_SESSION_SAMPLE_RATE=0.0,
        SENTRY_REPLAY_ON_ERROR_SAMPLE_RATE=0.0,
    )
    def test_password_reset_request_runtime_disables_sentry_replay(self) -> None:
        response = self.client.get("/password-reset/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-auth-recovery-password-reset-root=""')
        self._assert_rendered_page_disables_sentry_replay(response)

    @override_settings(DEFAULT_FROM_EMAIL="Astra Support <support-test@example.com>")
    def test_login_page_support_link_omits_mailto_email_for_anonymous_user(self) -> None:
        response = self.client.get("/login/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-sentry-feedback-footer=""')
        self.assertContains(response, 'data-sentry-feedback-hidden="true"')
        self.assertContains(response, 'data-sentry-feedback-link=""')
        self.assertContains(response, '>Contact Support<', html=False)
        self.assertNotContains(response, 'href="mailto:support-test@example.com"', html=False)
        self.assertNotContains(response, 'support-test@example.com')
        self.assertNotContains(response, 'btn btn-link', html=False)


class SentryBlockedTemplateMarkerTests(SimpleTestCase):
    def test_login_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/login.html").read_text(encoding="utf-8")

        self.assertIn('<meta name="sentry-replay-disabled" content="true">', source)
        self.assertIn('data-sentry-replay-disabled=""', source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_register_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/register.html").read_text(encoding="utf-8")

        self.assertIn('<meta name="sentry-replay-disabled" content="true">', source)
        self.assertIn('data-register-root=""', source)
        self.assertIn('data-sentry-replay-disabled=""', source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_settings_email_validation_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/settings_email_validation.html").read_text(
            encoding="utf-8"
        )

        self.assertIn('<meta name="sentry-replay-disabled" content="true">', source)
        self.assertIn('data-settings-email-validation-root=""', source)
        self.assertIn('data-sentry-replay-disabled=""', source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_user_profile_does_not_mark_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/user_profile.html").read_text(encoding="utf-8")

        self.assertIn("data-user-profile-root", source)
        self.assertNotIn('<meta name="sentry-replay-disabled" content="true">', source)
        self.assertNotIn('data-sentry-replay-disabled=""', source)
        self.assertNotIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_membership_request_templates_do_not_mark_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        unblocked_templates = {
            "membership_request.html": 'data-membership-request-form-root=""',
            "membership_request_detail.html": 'data-membership-request-detail-root=""',
            "membership_audit_log.html": '<table class="table table-striped mb-0">',
            "membership_audit_log_vue.html": 'data-membership-audit-log-root',
        }

        for template_name, root_marker in unblocked_templates.items():
            with self.subTest(template=template_name):
                source = (repo_root / f"astra_app/core/templates/core/{template_name}").read_text(
                    encoding="utf-8"
                )

                self.assertIn(root_marker, source)
                self.assertNotIn('<meta name="sentry-replay-disabled" content="true">', source)
                self.assertNotIn('data-sentry-replay-disabled=""', source)
                self.assertNotIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_settings_shell_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/settings_shell.html").read_text(encoding="utf-8")

        self.assertIn('data-settings-root=""', source)
        self.assertIn('data-sentry-replay-disabled=""', source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_election_vote_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/election_vote.html").read_text(encoding="utf-8")

        self.assertIn("data-election-vote-root", source)
        self.assertIn("data-sentry-replay-disabled", source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_sync_token_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/sync_token.html").read_text(encoding="utf-8")

        self.assertIn('data-auth-recovery-otp-sync-root=""', source)
        self.assertIn('data-sentry-replay-disabled=""', source)
        self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)

    def test_auth_recovery_templates_mark_capture_as_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        blocked_templates = {
            "password_reset_request.html": 'data-auth-recovery-password-reset-root=""',
            "password_reset_confirm.html": 'data-auth-recovery-password-reset-confirm-root=""',
            "password_expired.html": 'data-auth-recovery-password-expired-root=""',
        }

        for template_name, root_marker in blocked_templates.items():
            with self.subTest(template=template_name):
                source = (repo_root / f"astra_app/core/templates/core/{template_name}").read_text(encoding="utf-8")

                self.assertIn('<meta name="sentry-replay-disabled" content="true">', source)
                self.assertIn(root_marker, source)
                self.assertIn('data-sentry-replay-disabled=""', source)
                self.assertIn("{% block sentry_replay_integration %}{% endblock %}", source)


class SentryCaddyConfigTests(SimpleTestCase):
    blocked_scanner_path_matcher = (
        "path /.env /.git/config /phpinfo.php /vendor/phpunit* "
        "/wp-admin /wp-admin/* /wp-login.php /xmlrpc.php"
    )

    def test_caddy_configs_forward_sentry_trace_headers(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        for relative_path in ("infra/systemd/Caddyfile", "infra/systemd/Caddyfile.j2"):
            source = (repo_root / relative_path).read_text(encoding="utf-8")
            self.assertIn("header_up sentry-trace", source)
            self.assertIn("header_up baggage", source)

    def test_caddy_configs_block_scanner_paths_with_tightened_wordpress_matchers(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        tracked_source = (repo_root / "infra/systemd/Caddyfile").read_text(encoding="utf-8")
        self.assertIn(self.blocked_scanner_path_matcher, tracked_source)
        self.assertIn("abort @blocked_scanners", tracked_source)

        templated_source = (repo_root / "infra/systemd/Caddyfile.j2").read_text(encoding="utf-8")
        self.assertIn(
            """{{ http_addrs | unique | join(\", \") }} {
    @blocked_scanners {
        path /.env /.git/config /phpinfo.php /vendor/phpunit* /wp-admin /wp-admin/* /wp-login.php /xmlrpc.php
    }
    abort @blocked_scanners

    redir https://{host}{uri} permanent
}""",
            templated_source,
        )
        self.assertIn(
            """{{ https_addrs | unique | join(\", \") }} {
    # Staging-friendly HTTPS without needing a public DNS name you control.
    # Clients must trust Caddy's internal CA (or use curl -k).
    tls internal

    @blocked_scanners {
        path /.env /.git/config /phpinfo.php /vendor/phpunit* /wp-admin /wp-admin/* /wp-login.php /xmlrpc.php
    }
    abort @blocked_scanners

    reverse_proxy 127.0.0.1:8001 127.0.0.1:8002 {
        header_up X-Forwarded-For {http.request.remote.host}
        header_up sentry-trace {http.request.header.sentry-trace}
        header_up baggage {http.request.header.baggage}
    }
}""",
            templated_source,
        )
        self.assertIn(
            """:80 {
    @blocked_scanners {
        path /.env /.git/config /phpinfo.php /vendor/phpunit* /wp-admin /wp-admin/* /wp-login.php /xmlrpc.php
    }
    abort @blocked_scanners

    redir https://{host}{uri} permanent
}""",
            templated_source,
        )
        self.assertIn(
            """:443 {
    # Staging-friendly HTTPS without needing a public DNS name.
    # Clients must trust Caddy's internal CA (or use curl -k).
    tls internal

    @blocked_scanners {
        path /.env /.git/config /phpinfo.php /vendor/phpunit* /wp-admin /wp-admin/* /wp-login.php /xmlrpc.php
    }
    abort @blocked_scanners

    reverse_proxy 127.0.0.1:8001 127.0.0.1:8002 {
        header_up X-Forwarded-For {http.request.remote.host}
        header_up sentry-trace {http.request.header.sentry-trace}
        header_up baggage {http.request.header.baggage}
    }
}""",
            templated_source,
        )