from pathlib import Path

from django.test import SimpleTestCase


class FormFieldDryPhase4TemplateTests(SimpleTestCase):
    def _read_template(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "templates" / "core" / template_name
        return template_path.read_text(encoding="utf-8")

    def test_phase4_templates_use_shared_form_field_includes(self) -> None:
        expectations: dict[str, list[str]] = {
            "_settings_tab_profile.html": [
                "{% include 'core/_form_field.html' with field=profile_form.givenname wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field.html' with field=profile_form.sn wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field.html' with field=profile_form.fasPronoun %}",
                "{% include 'core/_form_field.html' with field=profile_form.country_code wrapper_class='settings-field-highlight' wrapper_attrs='id=\"country-code-field-wrapper\"' %}",
                "{% include 'core/_form_field.html' with field=profile_form.country_code wrapper_attrs='id=\"country-code-field-wrapper\"' %}",
            ],
            "_settings_tab_keys.html": [
                "{% include 'core/_form_field_widget_fallback.html' with field=keys_form.fasGPGKeyId widget_id='gpg-keys-widget' fallback_id='gpg-keys-fallback' table_id='gpg-keys-table' add_button_id='gpg-keys-add' add_button_title='Add another GPG key ID' add_button_text='Add GPG key ID' %}",
                "{% include 'core/_form_field_widget_fallback.html' with field=keys_form.ipasshpubkey widget_id='ssh-keys-widget' fallback_id='ssh-keys-fallback' table_id='ssh-keys-table' add_button_id='ssh-keys-add' add_button_title='Add another SSH key' add_button_text='Add SSH key' %}",
            ],
            "election_edit.html": [
                "{% include 'core/_form_field.html' with field=details_form.name %}",
                "{% include 'core/_form_field.html' with field=details_form.start_datetime wrapper_class='col-md-6' %}",
                "{% include 'core/_form_field_inner.html' with field=f.freeipa_username show_label=0 show_help=0 %}",
                "{% include 'core/_form_field_inner.html' with field=g.candidate_usernames show_label=0 show_help=0 %}",
            ],
            "send_mail.html": [
                "{% include 'core/_form_field.html' with field=form.group_cn wrapper_class='mb-0' help_text_override='Includes nested group members.' %}",
                "{% include 'core/_form_field.html' with field=form.csv_file wrapper_class='mb-0' help_text_override='CSV should include an Email column. If you previously uploaded a CSV, you can leave this blank to reuse it.' %}",
                "{% include 'core/_form_field.html' with field=form.reply_to wrapper_class='mb-0' help_text_override='Comma-separated email addresses.' %}",
            ],
            "_templated_email_compose.html": [
                "{% include 'core/_form_field.html' with field=form.subject %}",
                "{% include 'core/_form_field_inner.html' with field=form.html_content show_label=0 show_help=0 %}",
                "{% include 'core/_form_field_inner.html' with field=form.text_content show_label=0 show_help=0 %}",
            ],
        }

        for template_name, snippets in expectations.items():
            template_content = self._read_template(template_name)
            for snippet in snippets:
                with self.subTest(template=template_name, snippet=snippet):
                    self.assertIn(snippet, template_content)

    def test_phase4_settings_templates_remove_redundant_manual_rendering(self) -> None:
        keys_template = self._read_template("_settings_tab_keys.html")
        profile_template = self._read_template("_settings_tab_profile.html")

        self.assertNotIn("show_help=0 show_errors=0", keys_template)
        self.assertNotIn("keys_form.fasGPGKeyId.help_text", keys_template)
        self.assertNotIn("keys_form.fasGPGKeyId.errors", keys_template)
        self.assertNotIn("keys_form.ipasshpubkey.help_text", keys_template)
        self.assertNotIn("keys_form.ipasshpubkey.errors", keys_template)

        self.assertNotIn("{{ profile_form.givenname.label_tag }}", profile_template)
        self.assertNotIn("{{ profile_form.sn.label_tag }}", profile_template)
        self.assertNotIn("{{ profile_form.fasPronoun.label_tag }}", profile_template)

    def test_phase4_out_of_scope_non_django_controls_remain_custom(self) -> None:
        login_template = self._read_template("login.html")
        ballot_template = self._read_template("ballot_verify.html")
        compose_template = self._read_template("_templated_email_compose.html")

        self.assertIn('name="username"', login_template)
        self.assertIn('name="password"', login_template)
        self.assertIn('name="otp"', login_template)

        self.assertIn('name="receipt"', ballot_template)

        self.assertIn('<select class="form-control" name="email_template_id">', compose_template)
