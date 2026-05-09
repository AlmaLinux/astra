from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from core.context_processors import build_info
from core.freeipa.user import FreeIPAUser


class SentryBrowserContextTests(SimpleTestCase):
    def test_sentry_browser_entrypoint_exists(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        self.assertTrue((repo_root / "frontend/src/entrypoints/sentryBrowser.ts").exists())

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
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
                "tunnel": "/_ci/envelope/",
            },
        )
        self.assertEqual(context["sentry_trace"], "trace-parent")
        self.assertEqual(context["sentry_baggage"], "sentry-sample=value")

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
    )
    def test_build_info_browser_config_disables_default_pii(self) -> None:
        context = build_info(SimpleNamespace())

        config = context["sentry_browser_config"]

        assert config is not None
        self.assertIn("sendDefaultPii", config)
        self.assertFalse(config["sendDefaultPii"])

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

    @override_settings(
        SENTRY_DSN="https://public@example.ingest.sentry.io/1",
        SENTRY_ENVIRONMENT="staging",
        SENTRY_RELEASE="build-123",
        SENTRY_TRACES_SAMPLE_RATE=0.25,
    )
    def test_profile_page_includes_sentry_browser_bundle_and_tunnel_config(self) -> None:
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
            response = self.client.get(f"/user/{username}/")

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
        self.assertContains(response, "window.Sentry.browserSessionIntegration")
        self.assertContains(response, '"environment": "staging"')
        self.assertContains(response, '"release": "build-123"')
        self.assertContains(response, '"sendDefaultPii": false')
        self.assertContains(response, '"tracesSampleRate": 0.25')
        self.assertContains(response, '"tunnel": "/_ci/envelope/"')
        self.assertContains(response, 'data-sentry-feedback-link=""')
        self.assertContains(response, 'data-sentry-feedback-footer=""')
        self.assertContains(response, '>Report a bug<', html=False)
        self.assertContains(response, 'data-sentry-feedback-hidden="true"')


class SentryBlockedTemplateMarkerTests(SimpleTestCase):
    def test_settings_shell_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/settings_shell.html").read_text(encoding="utf-8")

        self.assertIn('data-settings-root=""', source)
        self.assertIn('data-sentry-capture-disabled=""', source)

    def test_election_vote_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/election_vote.html").read_text(encoding="utf-8")

        self.assertIn("data-election-vote-root", source)
        self.assertIn("data-sentry-capture-disabled", source)

    def test_sync_token_marks_root_as_sentry_capture_disabled(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        source = (repo_root / "astra_app/core/templates/core/sync_token.html").read_text(encoding="utf-8")

        self.assertIn('data-auth-recovery-otp-sync-root=""', source)
        self.assertIn('data-sentry-capture-disabled=""', source)

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

                self.assertIn('<meta name="sentry-capture-disabled" content="true">', source)
                self.assertIn(root_marker, source)
                self.assertIn('data-sentry-capture-disabled=""', source)


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