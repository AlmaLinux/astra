
import inspect
import json
import subprocess
import textwrap
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.freeipa.user import FreeIPAUser
from core.membership_notes import CUSTOS
from core.models import FreeIPAPermissionGrant, MembershipRequest, MembershipType, Note
from core.permissions import (
    ASTRA_ADD_MEMBERSHIP,
    ASTRA_CHANGE_MEMBERSHIP,
    ASTRA_DELETE_MEMBERSHIP,
    ASTRA_VIEW_MEMBERSHIP,
)
from core.tests.utils_test_data import ensure_core_categories


class MembershipNotesAjaxTests(TestCase):
    def setUp(self) -> None:
        super().setUp()
        ensure_core_categories()

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_ADD_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.group,
            principal_name=settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP,
        )

        MembershipType.objects.update_or_create(
            code="individual",
            defaults={
                "name": "Individual",
                "group_cn": "almalinux-individual",
                "category_id": "individual",
                "sort_order": 0,
                "enabled": True,
            },
        )

    def _login_as_freeipa_user(self, username: str) -> None:
        session = self.client.session
        session["_freeipa_username"] = username
        session.save()

    def _reviewer_user(self) -> FreeIPAUser:
        return FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

    def _aggregate_target_token(self, *, username: str, email: str = "") -> str:
        from core.tokens import make_membership_notes_aggregate_target_token

        return make_membership_notes_aggregate_target_token(
            {
                "target_type": "user",
                "target": username,
                "email": email,
            }
        )

    def _group_html(self, html: str, username: str, *, next_username: str | None = None) -> str:
        start_marker = f'data-membership-notes-group-username="{username}"'
        start_index = html.find(start_marker)
        self.assertNotEqual(start_index, -1)
        if next_username is None:
            return html[start_index:]

        end_marker = f'data-membership-notes-group-username="{next_username}"'
        end_index = html.find(end_marker, start_index + len(start_marker))
        self.assertNotEqual(end_index, -1)
        return html[start_index:end_index]

    def _render_notes_html_via_ajax(self, membership_request_id: int) -> str:
        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[membership_request_id]),
                data={
                    "note_action": "message",
                    "message": "render",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        return str(payload.get("html", ""))

    def _fetch_request_notes_detail_payload(self, membership_request_id: int) -> dict[str, object]:
        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-request-notes", args=[membership_request_id]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _fetch_request_notes_summary_payload(self, membership_request_id: int) -> dict[str, object]:
        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-request-notes-summary", args=[membership_request_id]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _fetch_request_detail_payload(self, membership_request_id: int) -> dict[str, object]:
        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-request-detail", args=[membership_request_id]),
                HTTP_ACCEPT="application/json",
            )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _plain_user(self, username: str, *, memberof_group: list[str] | None = None) -> FreeIPAUser:
        return FreeIPAUser(
            username,
            {
                "uid": [username],
                "mail": [f"{username}@example.com"],
                "memberof_group": list(memberof_group or []),
            },
        )

    def _membership_request_detail_response(
        self,
        *,
        viewer_username: str,
        membership_request_id: int,
        expected_status: int = 200,
    ):
        membership_request = MembershipRequest.objects.get(pk=membership_request_id)
        self._login_as_freeipa_user(viewer_username)

        def lookup_user(username: str, *, respect_privacy: bool = True):
            if username == "reviewer":
                return self._reviewer_user()
            if username == viewer_username:
                return self._plain_user(viewer_username)
            return self._plain_user(username)

        with patch("core.freeipa.user.FreeIPAUser.get", side_effect=lookup_user):
            response = self.client.get(reverse("membership-request-detail", args=[membership_request.pk]))
        self.assertEqual(response.status_code, expected_status)
        return response

    def _run_membership_notes_node_scenario(self, test_body: str) -> None:
        script_path = Path(settings.BASE_DIR) / "core/static/core/js/membership_notes.js"
        source = script_path.read_text(encoding="utf-8")
        node_script = textwrap.dedent(
            f"""
                        const vm = require('vm');

                        class EventTargetStub {{
                            constructor() {{
                                this._handlers = Object.create(null);
                            }}

                            addEventListener(type, handler) {{
                                this._handlers[type] = this._handlers[type] || [];
                                this._handlers[type].push(handler);
                            }}

                            dispatchEvent(event) {{
                                event.target = event.target || this;
                                event.currentTarget = this;
                                (this._handlers[event.type] || []).forEach((handler) => handler.call(this, event));
                                return !event.defaultPrevented;
                            }}
                        }}

                        class ClassListStub {{
                            constructor(element) {{
                                this.element = element;
                            }}

                            _tokens() {{
                                return String(this.element.className || '').split(/\s+/).filter(Boolean);
                            }}

                            contains(token) {{
                                return this._tokens().includes(token);
                            }}

                            add(token) {{
                                if (!this.contains(token)) {{
                                    this.element.className = this._tokens().concat([token]).join(' ');
                                }}
                            }}

                            remove(token) {{
                                this.element.className = this._tokens().filter((item) => item !== token).join(' ');
                            }}
                        }}

                        class ElementStub extends EventTargetStub {{
                            constructor(document, options = {{}}) {{
                                super();
                                this.ownerDocument = document;
                                this.id = options.id || '';
                                this.tagName = String(options.tagName || 'div').toUpperCase();
                                this.attributes = Object.assign({{}}, options.attributes || {{}});
                                this.className = options.className || '';
                                this.children = [];
                                this.parentNode = null;
                                this.innerHTML = options.innerHTML || '';
                                this.textContent = options.textContent || '';
                                this.value = options.value || '';
                                this.disabled = !!options.disabled;
                                this.style = {{}};
                                this.dataset = {{}};
                                Object.keys(this.attributes).forEach((key) => {{
                                    if (key.startsWith('data-')) {{
                                        const dataKey = key.slice(5).replace(/-([a-z])/g, (_m, chr) => chr.toUpperCase());
                                        this.dataset[dataKey] = this.attributes[key];
                                    }}
                                }});
                                this.classList = new ClassListStub(this);
                            }}

                            appendChild(child) {{
                                child.parentNode = this;
                                this.children.push(child);
                                return child;
                            }}

                            setAttribute(name, value) {{
                                this.attributes[name] = String(value);
                                if (name === 'id') this.id = String(value);
                                if (name === 'class') this.className = String(value);
                                if (name.startsWith('data-')) {{
                                    const dataKey = name.slice(5).replace(/-([a-z])/g, (_m, chr) => chr.toUpperCase());
                                    this.dataset[dataKey] = String(value);
                                }}
                            }}

                            getAttribute(name) {{
                                if (name === 'id') return this.id;
                                if (name === 'class') return this.className;
                                return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
                            }}

                            querySelector(selector) {{
                                return this.querySelectorAll(selector)[0] || null;
                            }}

                            querySelectorAll(selector) {{
                                const results = [];
                                const attrMatch = selector.match(/^\[([^=]+)=\"([^\"]+)\"\]$/);
                                const nameMatch = selector.match(/^input\[name=\"([^\"]+)\"\]$/);
                                const tagMatch = selector.match(/^[a-z]+$/i);

                                function visit(node) {{
                                    let matched = false;
                                    if (selector.startsWith('#')) {{
                                        matched = node.id === selector.slice(1);
                                    }} else if (attrMatch) {{
                                        matched = node.getAttribute(attrMatch[1]) === attrMatch[2];
                                    }} else if (nameMatch) {{
                                        matched = node.tagName === 'INPUT' && node.getAttribute('name') === nameMatch[1];
                                    }} else if (tagMatch) {{
                                        matched = node.tagName === selector.toUpperCase();
                                    }}
                                    if (matched) results.push(node);
                                    node.children.forEach(visit);
                                }}

                                this.children.forEach(visit);
                                return results;
                            }}

                            closest(selector) {{
                                let node = this;
                                while (node) {{
                                    if (selector === '.card-tools' && String(node.className || '').split(/\s+/).includes('card-tools')) {{
                                        return node;
                                    }}
                                    node = node.parentNode;
                                }}
                                return null;
                            }}
                        }}

                        class DocumentStub extends EventTargetStub {{
                            constructor() {{
                                super();
                                this._elementsById = Object.create(null);
                            }}

                            register(element) {{
                                if (element.id) this._elementsById[element.id] = element;
                                return element;
                            }}

                            getElementById(id) {{
                                return this._elementsById[id] || null;
                            }}

                            createElement(tagName) {{
                                return new ElementStub(this, {{ tagName }});
                            }}
                        }}

                        function makeEvent(type, overrides = {{}}) {{
                            return Object.assign({{
                                type,
                                bubbles: true,
                                cancelable: true,
                                defaultPrevented: false,
                                preventDefault() {{ this.defaultPrevented = true; }},
                            }}, overrides);
                        }}

                        function assert(condition, message) {{
                            if (!condition) throw new Error(message);
                        }}

                        const document = new DocumentStub();
                        const container = document.register(new ElementStub(document, {{
                            id: 'membership-notes-container-77',
                            attributes: {{
                                'data-membership-notes-container': '77',
                                'data-membership-notes-default-open': '1',
                                'data-membership-notes-summary-url': '/summary',
                                'data-membership-notes-detail-url': '/detail',
                                'data-membership-notes-details-loaded': 'false',
                            }},
                        }}));
                        const card = document.register(new ElementStub(document, {{
                            id: 'membership-notes-card-77',
                            className: 'card card-primary card-outline direct-chat direct-chat-primary mb-0',
                        }}));
                        const header = document.register(new ElementStub(document, {{
                            id: 'membership-notes-header-77',
                            className: 'card-header',
                            attributes: {{ 'data-membership-notes-header': '77' }},
                        }}));
                        const cardTools = new ElementStub(document, {{ className: 'card-tools' }});
                        const collapseBtn = new ElementStub(document, {{
                            tagName: 'button',
                            attributes: {{ 'data-membership-notes-collapse': '77' }},
                        }});
                        const countBadge = new ElementStub(document, {{ tagName: 'span', attributes: {{ 'data-membership-notes-count': '77' }} }});
                        const approvalsBadge = new ElementStub(document, {{ tagName: 'span', attributes: {{ 'data-membership-notes-approvals': '77' }} }});
                        const disapprovalsBadge = new ElementStub(document, {{ tagName: 'span', attributes: {{ 'data-membership-notes-disapprovals': '77' }} }});
                        const messages = new ElementStub(document, {{ tagName: 'div', attributes: {{ 'data-membership-notes-messages': '77' }}, innerHTML: '<div class="text-muted small">Loading notes...</div>' }});
                        const modals = new ElementStub(document, {{ tagName: 'div', attributes: {{ 'data-membership-notes-modals': '77' }} }});
                        const footer = new ElementStub(document, {{ tagName: 'div' }});
                        const form = document.register(new ElementStub(document, {{
                            id: 'membership-notes-form-77',
                            tagName: 'form',
                            attributes: {{ 'data-membership-notes-form': '77' }},
                        }}));
                        const nextInput = new ElementStub(document, {{ tagName: 'input', attributes: {{ name: 'next' }}, value: '/membership/request/77/' }});
                        const actionInput = new ElementStub(document, {{ tagName: 'input', attributes: {{ name: 'note_action' }}, value: 'message' }});
                        const textarea = document.register(new ElementStub(document, {{ id: 'membership-notes-message-77', tagName: 'textarea' }}));
                        const submitButton = new ElementStub(document, {{ tagName: 'button' }});
                        const errorBox = document.register(new ElementStub(document, {{ id: 'membership-notes-error-77', className: 'd-none' }}));
                        const errorText = document.register(new ElementStub(document, {{ id: 'membership-notes-error-text-77' }}));
                        const errorClose = new ElementStub(document, {{ tagName: 'button', attributes: {{ 'data-membership-notes-error-close': '77' }} }});

                        container.appendChild(card);
                        card.appendChild(header);
                        header.appendChild(cardTools);
                        cardTools.appendChild(countBadge);
                        cardTools.appendChild(approvalsBadge);
                        cardTools.appendChild(disapprovalsBadge);
                        cardTools.appendChild(collapseBtn);
                        card.appendChild(messages);
                        card.appendChild(footer);
                        footer.appendChild(errorBox);
                        footer.appendChild(errorText);
                        footer.appendChild(errorClose);
                        footer.appendChild(form);
                        form.appendChild(nextInput);
                        form.appendChild(actionInput);
                        form.appendChild(textarea);
                        form.appendChild(submitButton);
                        container.appendChild(modals);

                        collapseBtn.addEventListener('click', function () {{
                            if (card.classList.contains('collapsed-card')) {{
                                card.classList.remove('collapsed-card');
                                card.dispatchEvent(makeEvent('expanded.lte.cardwidget'));
                                return;
                            }}
                            card.classList.add('collapsed-card');
                            card.dispatchEvent(makeEvent('collapsed.lte.cardwidget'));
                        }});

                        const localStorageData = Object.create(null);
                        const window = {{
                            document,
                            localStorage: {{
                                getItem(key) {{ return Object.prototype.hasOwnProperty.call(localStorageData, key) ? localStorageData[key] : null; }},
                                setItem(key, value) {{ localStorageData[key] = String(value); }},
                            }},
                            fetchQueue: [],
                            fetchCalls: [],
                            fetch(url) {{
                                window.fetchCalls.push({{ url }});
                                if (!window.fetchQueue.length) throw new Error('Missing fetch payload for ' + url);
                                const nextPayload = window.fetchQueue.shift();
                                if (nextPayload && nextPayload.__fetchError) {{
                                    return Promise.reject(new Error(String(nextPayload.__fetchError)));
                                }}
                                if (nextPayload && Object.prototype.hasOwnProperty.call(nextPayload, 'ok')) {{
                                    return Promise.resolve({{
                                        ok: !!nextPayload.ok,
                                        json() {{
                                            return Promise.resolve(nextPayload.payload || {{}});
                                        }},
                                    }});
                                }}
                                return Promise.resolve({{ ok: true, json() {{ return Promise.resolve(nextPayload); }} }});
                            }},
                            setTimeout,
                            clearTimeout,
                            Date,
                            CustomEvent: function CustomEvent(type, init) {{ return makeEvent(type, init || {{}}); }},
                        }};

                        const context = vm.createContext({{ window, document, console, setTimeout, clearTimeout }});
                        vm.runInContext({json.dumps(source)}, context);

                        async function flushAsync() {{
                            await Promise.resolve();
                            await new Promise((resolve) => setTimeout(resolve, 0));
                            await new Promise((resolve) => setTimeout(resolve, 0));
                        }}

                        async function runScenario() {{
                            {test_body}
                        }}

                        runScenario().catch((error) => {{
                            console.error(error && error.stack ? error.stack : String(error));
                            process.exit(1);
                        }});
                        """
        )

        result = subprocess.run(
            ["node"],
            input=node_script,
            capture_output=True,
            text=True,
            check=False,
            cwd=str(settings.BASE_DIR),
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "membership_notes.js scenario failed"
            self.fail(message)

    def test_note_add_returns_json_and_updated_html_for_ajax(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"ok": True, "message": "Note added."})

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        detail_json = json.dumps(detail_payload)
        self.assertIn("Hello via ajax", detail_json)
        self.assertIn("reviewer", detail_json)

        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="Hello via ajax",
            ).exists()
        )

    def test_other_user_bubbles_get_deterministic_inline_bubble_style(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp1 = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Self note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp1.status_code, 200)
        payload1 = json.loads(resp1.content)
        self.assertTrue(payload1.get("ok"))

        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content="Other note",
            action={},
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp2 = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Another self note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp2.status_code, 200)
        payload2 = json.loads(resp2.content)
        self.assertTrue(payload2.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        groups = detail_payload["groups"]
        other_group = next(group for group in groups if group["username"] == "someone_else")
        other_entry = other_group["entries"][0]
        self.assertEqual(other_entry["kind"], "message")
        self.assertIn("--bubble-bg:", str(other_entry.get("bubble_style") or ""))

    def test_custos_notes_render_with_distinct_style_and_avatar(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        # Pre-seed a system note so the rendered widget includes it.
        Note.objects.create(
            membership_request=req,
            username=CUSTOS,
            content="system note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        custos_group = next(group for group in detail_payload["groups"] if group["username"] == CUSTOS)
        self.assertEqual(custos_group["avatar_kind"], "custos")
        self.assertEqual(custos_group["is_custos"], True)
        self.assertIn("almalinux-logo.svg", str(custos_group.get("avatar_url") or ""))
        custos_entry = custos_group["entries"][0]
        self.assertIn("--bubble-bg: #e9ecef", str(custos_entry.get("bubble_style") or ""))

    def test_mirror_validation_notes_render_multiline_with_bold_result_values(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username=CUSTOS,
            content=(
                "Mirror validation summary\n"
                "Domain: reachable\n"
                "Mirror status: up-to-date\n"
                "AlmaLinux mirror network: registered\n"
                "GitHub pull request: valid; touches mirrors.d/mirror.example.org.yml"
            ),
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        detail_json = json.dumps(detail_payload)
        self.assertIn("Mirror validation summary<br>", detail_json)
        self.assertIn("Domain: <strong>reachable</strong>", detail_json)
        self.assertIn("Mirror status: <strong>up-to-date</strong>", detail_json)
        self.assertIn("AlmaLinux mirror network: <strong>registered</strong>", detail_json)
        self.assertIn(
            "GitHub pull request: <strong>valid; touches mirrors.d/mirror.example.org.yml</strong>",
            detail_json,
        )

    def test_regular_notes_render_safe_markdown_subset(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content=(
                "**Bold** and *italic*\n\n"
                "- first item\n"
                "- second item\n\n"
                "<script>alert('x')</script>"
            ),
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        detail_json = json.dumps(detail_payload)
        self.assertIn("<strong>Bold</strong>", detail_json)
        self.assertIn("<em>italic</em>", detail_json)
        self.assertIn("<ul>", detail_json)
        self.assertIn("<li>first item</li>", detail_json)
        self.assertIn("<li>second item</li>", detail_json)
        self.assertIn("&lt;script&gt;alert", detail_json)
        self.assertNotIn("<script>alert", detail_json)

    def test_regular_note_html_remains_escaped(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(
            membership_request=req,
            username="someone_else",
            content="<em>not safe</em>",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        detail_json = json.dumps(detail_payload)
        self.assertIn("&lt;em&gt;not safe&lt;/em&gt;", detail_json)
        self.assertNotIn("<em>not safe</em>", detail_json)

    def test_consecutive_actions_by_same_user_within_minute_are_grouped(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        now = timezone.now()
        base = now - timedelta(minutes=5)
        n1 = Note.objects.create(
            membership_request=req,
            username="alex",
            content=None,
            action={"type": "request_on_hold"},
        )
        n2 = Note.objects.create(
            membership_request=req,
            username="alex",
            content=None,
            action={"type": "contacted"},
        )
        Note.objects.filter(pk=n1.pk).update(timestamp=base)
        Note.objects.filter(pk=n2.pk).update(timestamp=base + timedelta(seconds=30))

        self._login_as_freeipa_user("reviewer")
        reviewer = FreeIPAUser(
            "reviewer",
            {
                "uid": ["reviewer"],
                "mail": ["reviewer@example.com"],
                "memberof_group": [settings.FREEIPA_MEMBERSHIP_COMMITTEE_GROUP],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "Hello via ajax",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        alex_group = next(group for group in detail_payload["groups"] if group["username"] == "alex")
        self.assertEqual(len(alex_group["entries"]), 2)
        self.assertEqual(alex_group["entries"][0]["kind"], "action")
        self.assertEqual(alex_group["entries"][1]["kind"], "action")
        self.assertIn("Request on hold", str(alex_group["entries"][0].get("label") or ""))
        self.assertIn("User contacted", str(alex_group["entries"][1].get("label") or ""))

    def test_view_only_user_cannot_submit_vote_actions(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "approve",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                action={"type": "vote", "value": "approve"},
            ).exists()
        )

    def test_view_only_user_cannot_submit_plain_message_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "plain note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                content="plain note",
            ).exists()
        )

    def test_view_only_user_cannot_submit_plain_aggregate_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")
        viewer = FreeIPAUser(
            "viewer",
            {
                "uid": ["viewer"],
                "mail": ["viewer@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=viewer):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "aggregate note",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="viewer",
                content="aggregate note",
            ).exists()
        )

    def test_manage_user_forged_aggregate_action_returns_deterministic_ajax_deny(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "vote_approve",
                    "message": "forged aggregate action",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid note action."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                content="forged aggregate action",
            ).exists()
        )

    def test_no_membership_permissions_user_cannot_submit_plain_detail_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "detail note denied",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="detail note denied",
            ).exists()
        )

    def test_no_membership_permissions_user_cannot_submit_plain_aggregate_notes_with_deterministic_403(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "note_action": "message",
                    "message": "aggregate note denied",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json(), {"error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="aggregate note denied",
            ).exists()
        )

    def test_no_membership_permissions_detail_ajax_does_not_leak_request_existence(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        missing_pk = req.pk + 999

        self._login_as_freeipa_user("no_permissions")
        no_permissions_user = FreeIPAUser(
            "no_permissions",
            {
                "uid": ["no_permissions"],
                "mail": ["no-permissions@example.com"],
                "memberof_group": [],
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=no_permissions_user):
            existing_resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "message",
                    "message": "probe note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            missing_resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[missing_pk]),
                data={
                    "note_action": "message",
                    "message": "probe note",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(existing_resp.status_code, 403)
        self.assertEqual(existing_resp.json(), {"error": "Permission denied."})
        self.assertEqual(missing_resp.status_code, 403)
        self.assertEqual(missing_resp.json(), {"error": "Permission denied."})
        self.assertFalse(
            Note.objects.filter(
                membership_request=req,
                username="no_permissions",
                content="probe note",
            ).exists()
        )

    def test_any_single_manage_permission_can_submit_plain_detail_note_without_view_permission(self) -> None:
        for index, permission in enumerate(
            (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP),
            start=1,
        ):
            username = f"manager{index}"
            request_username = f"alice-manage-{index}"
            req = MembershipRequest.objects.create(requested_username=request_username, membership_type_id="individual")

            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            )

            self._login_as_freeipa_user(username)
            manager = FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

            content = f"detail note {permission}"
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
                resp = self.client.post(
                    reverse("api-membership-request-notes-add", args=[req.pk]),
                    data={
                        "note_action": "message",
                        "message": content,
                        "next": reverse("membership-requests"),
                    },
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(resp.status_code, 200, msg=permission)
            self.assertTrue(resp.json().get("ok"), msg=permission)
            self.assertTrue(
                Note.objects.filter(
                    membership_request=req,
                    username=username,
                    content=content,
                ).exists(),
                msg=permission,
            )

    def test_any_single_manage_permission_can_submit_plain_aggregate_note_without_view_permission(self) -> None:
        for index, permission in enumerate(
            (ASTRA_ADD_MEMBERSHIP, ASTRA_CHANGE_MEMBERSHIP, ASTRA_DELETE_MEMBERSHIP),
            start=1,
        ):
            username = f"aggregate_manager{index}"
            target_username = f"aggregate-target-{index}"
            req = MembershipRequest.objects.create(requested_username=target_username, membership_type_id="individual")

            FreeIPAPermissionGrant.objects.get_or_create(
                permission=permission,
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            )

            self._login_as_freeipa_user(username)
            manager = FreeIPAUser(
                username,
                {
                    "uid": [username],
                    "mail": [f"{username}@example.com"],
                    "memberof_group": [],
                },
            )

            content = f"aggregate note {permission}"
            with patch("core.freeipa.user.FreeIPAUser.get", return_value=manager):
                resp = self.client.post(
                    reverse("api-membership-notes-aggregate-add"),
                    data={
                        "target_type": "user",
                        "target": target_username,
                        "note_action": "message",
                        "message": content,
                        "compact": "1",
                        "next": reverse("membership-requests"),
                    },
                    HTTP_X_REQUESTED_WITH="XMLHttpRequest",
                )

            self.assertEqual(resp.status_code, 200, msg=permission)
            self.assertTrue(resp.json().get("ok"), msg=permission)
            self.assertTrue(
                Note.objects.filter(
                    membership_request=req,
                    username=username,
                    content=content,
                ).exists(),
                msg=permission,
            )

    def test_aggregate_ajax_context_does_not_force_membership_can_view(self) -> None:
        req = MembershipRequest.objects.create(requested_username="aggregate-target", membership_type_id="individual")

        manager_username = "aggregate_context_manager"
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_CHANGE_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name=manager_username,
        )

        self._login_as_freeipa_user(manager_username)
        manager = FreeIPAUser(
            manager_username,
            {
                "uid": [manager_username],
                "mail": [f"{manager_username}@example.com"],
                "memberof_group": [],
            },
        )

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=manager),
            patch("core.views_membership.committee.membership_review_permissions"),
        ):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": req.requested_username,
                    "note_action": "message",
                    "message": "context probe note",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username=manager_username,
                content="context probe note",
            ).exists()
        )

    def test_aggregate_malformed_org_target_returns_deterministic_400_without_persistence(self) -> None:
        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer):
            resp = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "org",
                    "target": "not-an-int",
                    "note_action": "message",
                    "message": "bad org target",
                    "compact": "1",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json(), {"error": "Invalid target."})
        self.assertFalse(
            Note.objects.filter(
                username="reviewer",
                content="bad org target",
            ).exists()
        )

    def test_aggregate_notes_summary_api_returns_counts_for_user_target(self) -> None:
        first_request = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        second_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )
        Note.objects.create(membership_request=first_request, username="reviewer", action={"type": "vote", "value": "approve"})
        Note.objects.create(membership_request=second_request, username="reviewer", content="Aggregate note", action={})

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-notes-aggregate-summary") + "?target_type=user&target=alice",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 2,
                "approvals": 1,
                "disapprovals": 0,
                "current_user_vote": "approve",
            },
        )

    def test_aggregate_notes_summary_api_does_not_use_detail_read_context(self) -> None:
        first_request = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        second_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )
        Note.objects.create(
            membership_request=first_request,
            username="reviewer",
            action={"type": "vote", "value": "disapprove"},
        )
        Note.objects.create(
            membership_request=first_request,
            username="reviewer",
            content="Need another look",
        )
        Note.objects.create(
            membership_request=second_request,
            username="reviewer",
            action={"type": "vote", "value": "approve"},
        )
        Note.objects.create(
            membership_request=second_request,
            username="other-reviewer",
            action={"type": "vote", "value": "disapprove"},
        )

        self._login_as_freeipa_user("reviewer")

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()),
            patch(
                "core.views_membership.committee._membership_notes_read_context",
                side_effect=AssertionError("aggregate notes summary must not use the detail read context"),
            ),
            patch(
                "core.views_membership.committee.build_note_details",
                side_effect=AssertionError("aggregate notes summary must not use detail builders"),
            ),
        ):
            response = self.client.get(
                reverse("api-membership-notes-aggregate-summary") + "?target_type=user&target=alice",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "note_count": 4,
                "approvals": 1,
                "disapprovals": 1,
                "current_user_vote": "approve",
            },
        )

    def test_aggregate_notes_api_returns_grouped_json_without_request_links(self) -> None:
        first_request = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        second_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            status=MembershipRequest.Status.approved,
        )
        Note.objects.create(membership_request=first_request, username="reviewer", content="First aggregate note", action={})
        Note.objects.create(membership_request=second_request, username="reviewer", content="Second aggregate note", action={})

        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-notes-aggregate") + "?target_type=user&target=alice",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["groups"]), 2)
        self.assertEqual(payload["groups"][0]["membership_request_id"], first_request.pk)
        self.assertNotIn("membership_request_url", payload["groups"][0])
        self.assertEqual(payload["groups"][1]["membership_request_id"], second_request.pk)
        self.assertEqual(
            payload["groups"][1]["entries"][0]["rendered_html"],
            "Second aggregate note",
        )

    def test_aggregate_notes_api_rejects_invalid_org_target_with_json_400(self) -> None:
        self._login_as_freeipa_user("reviewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            response = self.client.get(
                reverse("api-membership-notes-aggregate") + "?target_type=org&target=not-an-int",
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid target."})

    def test_membership_request_detail_renders_notes_root_urls_for_reviewers(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        response = self._membership_request_detail_response(
            viewer_username="reviewer",
            membership_request_id=req.pk,
        )
        detail_payload = self._fetch_request_detail_payload(req.pk)

        self.assertContains(response, 'data-membership-request-detail-root=""')
        self.assertContains(response, reverse("api-membership-request-detail", args=[req.pk]))
        self.assertNotIn("notes", detail_payload)
        self.assertContains(
            response,
            f'data-membership-request-detail-note-summary-url="{reverse("api-membership-request-notes-summary", args=[req.pk])}"',
        )
        self.assertContains(
            response,
            f'data-membership-request-detail-note-detail-url="{reverse("api-membership-request-notes", args=[req.pk])}"',
        )
        self.assertContains(
            response,
            f'data-membership-request-detail-note-add-url="{reverse("api-membership-request-notes-add", args=[req.pk])}"',
        )

    def test_membership_request_detail_marks_manage_user_as_can_write_and_vote(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        response = self._membership_request_detail_response(
            viewer_username="reviewer",
            membership_request_id=req.pk,
        )
        payload = self._fetch_request_detail_payload(req.pk)

        self.assertNotIn("notes", payload)
        self.assertContains(response, 'data-membership-request-detail-notes-can-view="true"')
        self.assertContains(response, 'data-membership-request-detail-notes-can-write="true"')
        self.assertContains(response, 'data-membership-request-detail-notes-can-vote="true"')

    def test_request_resubmitted_diff_values_are_escaped_and_preserve_linebreaks_in_api_payload(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Why join?": "<script>alert(2)</script>\nNew line 2"}],
        )
        note = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [{"Why join?": '<img src=x onerror="alert(1)">\nOld line 2'}],
            },
        )

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        entry = next(
            entry
            for group in detail_payload["groups"]
            for entry in group["entries"]
            if entry["kind"] == "action" and entry.get("note_id") == note.pk
        )

        self.assertEqual(entry["request_resubmitted_diff_rows"][0]["question"], "Why join?")
        self.assertEqual(
            entry["request_resubmitted_diff_rows"][0]["old_value"],
            '<img src=x onerror="alert(1)">\nOld line 2',
        )
        self.assertEqual(
            entry["request_resubmitted_diff_rows"][0]["new_value"],
            "<script>alert(2)</script>\nNew line 2",
        )

    def test_request_notes_apis_allow_read_only_viewers_to_read(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Visible note")
        FreeIPAPermissionGrant.objects.get_or_create(
            permission=ASTRA_VIEW_MEMBERSHIP,
            principal_type=FreeIPAPermissionGrant.PrincipalType.user,
            principal_name="viewer",
        )

        self._login_as_freeipa_user("viewer")

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._plain_user("viewer")):
            summary_response = self.client.get(
                reverse("api-membership-request-notes-summary", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )
            detail_response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(summary_response.status_code, 200)
        self.assertEqual(summary_response.json()["note_count"], 1)
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("Visible note", json.dumps(detail_response.json()))

    def test_membership_request_detail_hides_notes_root_without_view_permission(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        response = self._membership_request_detail_response(
            viewer_username="viewer_no_access",
            membership_request_id=req.pk,
            expected_status=404,
        )

        self.assertNotContains(response, 'data-membership-request-detail-root', status_code=404)
        self.assertNotContains(response, reverse("api-membership-request-notes-summary", args=[req.pk]), status_code=404)
        self.assertNotContains(response, reverse("api-membership-request-notes", args=[req.pk]), status_code=404)

    def test_membership_request_detail_hides_server_rendered_note_history(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Visible note")

        with patch(
            "core.views_membership.committee.Note.objects.filter",
            side_effect=AssertionError("request detail notes shell must not query Note rows during render"),
        ):
            response = self._membership_request_detail_response(
                viewer_username="reviewer",
                membership_request_id=req.pk,
            )
            payload = self._fetch_request_detail_payload(req.pk)

        self.assertContains(response, 'data-membership-request-detail-root=""')
        self.assertNotIn("notes", payload)
        self.assertContains(response, 'data-membership-request-detail-notes-can-view="true"')
        self.assertContains(response, 'data-membership-request-detail-notes-can-write="true"')
        self.assertContains(response, 'data-membership-request-detail-notes-can-vote="true"')
        self.assertNotContains(response, "Visible note")

    def test_membership_request_detail_defaults_to_api_shell_without_direct_query_fallback(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")
        Note.objects.create(membership_request=req, username="reviewer", content="Visible note")

        with patch(
            "core.views_membership.committee.Note.objects.filter",
            side_effect=AssertionError("legacy direct note read should not run"),
        ):
            response = self._membership_request_detail_response(
                viewer_username="reviewer",
                membership_request_id=req.pk,
            )
            payload = self._fetch_request_detail_payload(req.pk)

        self.assertContains(response, reverse("api-membership-request-detail", args=[req.pk]))
        self.assertNotIn("notes", payload)
        self.assertContains(
            response,
            f'data-membership-request-detail-note-summary-url="{reverse("api-membership-request-notes-summary", args=[req.pk])}"',
        )
        self.assertContains(
            response,
            f'data-membership-request-detail-note-detail-url="{reverse("api-membership-request-notes", args=[req.pk])}"',
        )
        self.assertContains(
            response,
            f'data-membership-request-detail-note-add-url="{reverse("api-membership-request-notes-add", args=[req.pk])}"',
        )
        self.assertNotContains(response, "Visible note")
        self.assertNotContains(response, 'data-membership-notes-group-username="reviewer"')

    def test_membership_note_tags_remove_api_backed_parameter(self) -> None:
        from core.templatetags import core_membership_notes

        self.assertNotIn(
            "api_backed_read",
            inspect.signature(core_membership_notes.membership_notes).parameters,
        )
        self.assertNotIn(
            "preloaded_notes",
            inspect.signature(core_membership_notes.membership_notes).parameters,
        )
        self.assertNotIn(
            "fail_on_query_fallback",
            inspect.signature(core_membership_notes.membership_notes).parameters,
        )

    def test_manage_user_can_submit_vote_actions(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            resp = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "approve",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(resp.status_code, 200)
        payload = json.loads(resp.content)
        self.assertTrue(payload.get("ok"))
        self.assertTrue(
            Note.objects.filter(
                membership_request=req,
                username="reviewer",
                action={"type": "vote", "value": "approve"},
            ).exists()
        )

    def test_vote_badges_highlight_reviewers_latest_vote(self) -> None:
        req = MembershipRequest.objects.create(requested_username="alice", membership_type_id="individual")

        self._login_as_freeipa_user("reviewer")
        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            first = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "vote_approve",
                    "message": "",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), {"ok": True, "message": "Recorded approve vote."})
        self.assertEqual(
            self._fetch_request_notes_summary_payload(req.pk),
            {
                "note_count": 1,
                "approvals": 1,
                "disapprovals": 0,
                "current_user_vote": "approve",
            },
        )

        with patch("core.freeipa.user.FreeIPAUser.get", return_value=self._reviewer_user()):
            second = self.client.post(
                reverse("api-membership-request-notes-add", args=[req.pk]),
                data={
                    "note_action": "vote_disapprove",
                    "message": "",
                    "next": reverse("membership-requests"),
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json(), {"ok": True, "message": "Recorded disapprove vote."})
        self.assertEqual(
            self._fetch_request_notes_summary_payload(req.pk),
            {
                "note_count": 2,
                "approvals": 0,
                "disapprovals": 1,
                "current_user_vote": "disapprove",
            },
        )

    def test_request_resubmitted_diff_is_stable_across_multi_cycle_history(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Contributions": "Cycle 3"},
                {"Additional info": "Same"},
            ],
        )
        note_1 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 1"},
                    {"Additional info": "Same"},
                ],
            },
        )
        note_2 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 2"},
                    {"Additional info": "Same"},
                ],
            },
        )

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        request_resubmitted_entries = {
            entry["note_id"]: entry
            for group in detail_payload["groups"]
            for entry in group["entries"]
            if entry["kind"] == "action" and entry.get("note_id") in {note_1.pk, note_2.pk}
        }

        note_1_entry = request_resubmitted_entries[note_1.pk]
        note_2_entry = request_resubmitted_entries[note_2.pk]
        self.assertEqual(note_1_entry["request_resubmitted_diff_rows"][0]["question"], "Contributions")
        self.assertIn("Cycle 1", note_1_entry["request_resubmitted_diff_rows"][0]["old_value"])
        self.assertIn("Cycle 2", note_1_entry["request_resubmitted_diff_rows"][0]["new_value"])
        self.assertNotIn("Cycle 3", note_1_entry["request_resubmitted_diff_rows"][0]["new_value"])

        self.assertEqual(note_2_entry["request_resubmitted_diff_rows"][0]["question"], "Contributions")
        self.assertIn("Cycle 2", note_2_entry["request_resubmitted_diff_rows"][0]["old_value"])
        self.assertIn("Cycle 3", note_2_entry["request_resubmitted_diff_rows"][0]["new_value"])

    def test_request_resubmitted_diff_uses_pk_tiebreak_for_equal_timestamps(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Contributions": "Cycle 3"},
                {"Additional info": "Same"},
            ],
        )
        note_1 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 1"},
                    {"Additional info": "Same"},
                ],
            },
        )
        note_2 = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Contributions": "Cycle 2"},
                    {"Additional info": "Same"},
                ],
            },
        )
        tie_timestamp = timezone.now() - timedelta(minutes=5)
        Note.objects.filter(pk=note_1.pk).update(timestamp=tie_timestamp)
        Note.objects.filter(pk=note_2.pk).update(timestamp=tie_timestamp)

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        request_resubmitted_entries = [
            entry
            for group in detail_payload["groups"]
            for entry in group["entries"]
            if entry["kind"] == "action" and entry.get("note_id") in {note_1.pk, note_2.pk}
        ]
        self.assertEqual([entry["note_id"] for entry in request_resubmitted_entries], [note_1.pk, note_2.pk])
        self.assertIn("Cycle 1", request_resubmitted_entries[0]["request_resubmitted_diff_rows"][0]["old_value"])
        self.assertIn("Cycle 2", request_resubmitted_entries[0]["request_resubmitted_diff_rows"][0]["new_value"])
        self.assertIn("Cycle 2", request_resubmitted_entries[1]["request_resubmitted_diff_rows"][0]["old_value"])
        self.assertIn("Cycle 3", request_resubmitted_entries[1]["request_resubmitted_diff_rows"][0]["new_value"])

    def test_request_resubmitted_diff_renders_changed_questions_as_collapsed_details_only(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[
                {"Changed question": "Updated value"},
                {"Unchanged question": "Same value"},
            ],
        )
        note = Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [
                    {"Changed question": "Old value"},
                    {"Unchanged question": "Same value"},
                ],
            },
        )

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        entry = next(
            entry
            for group in detail_payload["groups"]
            for entry in group["entries"]
            if entry["kind"] == "action" and entry.get("note_id") == note.pk
        )
        self.assertEqual(
            entry["request_resubmitted_diff_rows"],
            [
                {
                    "question": "Changed question",
                    "old_value": "Old value",
                    "new_value": "Updated value",
                }
            ],
        )

    @override_settings(MEMBERSHIP_NOTES_RESUBMITTED_DIFFS_ENABLED=False)
    def test_request_resubmitted_diff_can_be_disabled_without_hiding_notes(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
            responses=[{"Contributions": "Updated"}],
        )
        Note.objects.create(
            membership_request=req,
            username="alice",
            action={
                "type": "request_resubmitted",
                "old_responses": [{"Contributions": "Original"}],
            },
        )

        detail_payload = self._fetch_request_notes_detail_payload(req.pk)
        entry = next(
            entry
            for group in detail_payload["groups"]
            for entry in group["entries"]
            if entry["kind"] == "action"
        )
        self.assertEqual(entry["label"], "Request resubmitted")
        self.assertEqual(entry["request_resubmitted_diff_rows"], [])

    def test_aggregate_note_ajax_rerender_reuses_profile_target_user(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "aggregate_preloaded_target_token": self._aggregate_target_token(
                        username="alice",
                        email="alice@example.com",
                    ),
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [])
        self.assertEqual(payload.get("message"), "Note added.")
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="reviewer",
                content="Ajax note",
            ).exists()
        )

    def test_aggregate_note_ajax_rerender_reuses_no_email_profile_target_from_signed_token(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "aggregate_preloaded_target_token": self._aggregate_target_token(username="alice"),
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [])
        self.assertEqual(payload.get("message"), "Note added.")
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="reviewer",
                content="Ajax note",
            ).exists()
        )

    def test_aggregate_note_ajax_rerender_ignores_untrusted_raw_target_identity_fields(self) -> None:
        membership_request = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=membership_request,
            username="alice",
            content="Target note",
            action={},
        )
        Note.objects.create(
            membership_request=membership_request,
            username="ghost-user",
            content="Ghost note",
            action={},
        )

        self._login_as_freeipa_user("reviewer")
        reviewer = self._reviewer_user()
        lightweight_lookup_usernames: list[tuple[str, ...]] = []

        def _record_lightweight_lookup(usernames: set[str]) -> dict[str, FreeIPAUser]:
            lightweight_lookup_usernames.append(tuple(sorted(usernames)))
            return {}

        with (
            patch("core.freeipa.user.FreeIPAUser.get", return_value=reviewer),
            patch(
                "core.templatetags.core_membership_notes.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=_record_lightweight_lookup,
            ),
        ):
            response = self.client.post(
                reverse("api-membership-notes-aggregate-add"),
                data={
                    "target_type": "user",
                    "target": "alice",
                    "aggregate_preloaded_target_username": "alice",
                    "aggregate_preloaded_target_email": "attacker@example.com",
                    "note_action": "message",
                    "message": "Ajax note",
                    "compact": "1",
                    "next": "/user/alice/",
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload.get("ok"), True)
        self.assertEqual(lightweight_lookup_usernames, [])
        self.assertEqual(payload.get("message"), "Note added.")
        self.assertTrue(
            Note.objects.filter(
                membership_request=membership_request,
                username="reviewer",
                content="Ajax note",
            ).exists()
        )

    def test_detail_notes_do_not_switch_to_aggregate_lightweight_author_lookup(self) -> None:
        req = MembershipRequest.objects.create(
            requested_username="alice",
            membership_type_id="individual",
        )
        Note.objects.create(
            membership_request=req,
            username="committee2",
            content="Detail note",
            action={},
        )

        request = RequestFactory().get("/")
        request.session = {"_freeipa_username": "reviewer"}

        reviewer = self._reviewer_user()
        committee2 = FreeIPAUser(
            "committee2",
            {
                "uid": ["committee2"],
                "displayname": ["Committee Two"],
                "mail": ["committee2@example.com"],
            },
        )

        def _fake_get(username: str) -> FreeIPAUser | None:
            if username == "committee2":
                return committee2
            if username == "reviewer":
                return reviewer
            return None

        with (
            patch(
                "core.membership_requests_datatables.FreeIPAUser.find_lightweight_by_usernames",
                side_effect=AssertionError("detail notes must stay on the existing full-detail author path"),
            ),
            patch("core.templatetags.core_membership_notes.FreeIPAUser.get", side_effect=_fake_get),
        ):
            self._login_as_freeipa_user("reviewer")
            response = self.client.get(
                reverse("api-membership-request-notes", args=[req.pk]),
                HTTP_ACCEPT="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Detail note", json.dumps(response.json()))
