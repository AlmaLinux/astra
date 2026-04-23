from django.test import RequestFactory, SimpleTestCase

from core.views_utils import parse_datatables_request_base


class ParseDatatablesRequestBaseTests(SimpleTestCase):
    def test_uses_default_allowed_params_when_not_provided(self) -> None:
        request = RequestFactory().get(
            "/api/",
            {
                "draw": "2",
                "start": "0",
                "length": "25",
                "search[value]": "",
                "search[regex]": "false",
                "order[0][column]": "0",
                "order[0][dir]": "asc",
                "order[0][name]": "requested_at",
                "columns[0][data]": "request_id",
                "columns[0][name]": "requested_at",
                "columns[0][searchable]": "true",
                "columns[0][orderable]": "true",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
            },
        )

        draw, start, length = parse_datatables_request_base(
            request,
            allow_cache_buster=False,
        )

        self.assertEqual((draw, start, length), (2, 0, 25))

    def test_allows_additional_allowed_params(self) -> None:
        request = RequestFactory().get(
            "/api/",
            {
                "draw": "1",
                "start": "0",
                "length": "10",
                "search[value]": "",
                "search[regex]": "false",
                "order[0][column]": "0",
                "order[0][dir]": "asc",
                "order[0][name]": "requested_at",
                "columns[0][data]": "request_id",
                "columns[0][name]": "requested_at",
                "columns[0][searchable]": "true",
                "columns[0][orderable]": "true",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
                "q": "alice",
            },
        )

        draw, start, length = parse_datatables_request_base(
            request,
            allow_cache_buster=False,
            additional_allowed_params={"q"},
        )

        self.assertEqual((draw, start, length), (1, 0, 10))

    def test_parses_valid_base_payload(self) -> None:
        request = RequestFactory().get(
            "/api/",
            {
                "draw": "2",
                "start": "0",
                "length": "25",
                "search[value]": "",
                "search[regex]": "false",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
                "custom": "ok",
            },
        )

        draw, start, length = parse_datatables_request_base(
            request,
            allowed_params={
                "draw",
                "start",
                "length",
                "search[value]",
                "search[regex]",
                "columns[0][search][value]",
                "columns[0][search][regex]",
                "custom",
            },
            allow_cache_buster=False,
        )

        self.assertEqual((draw, start, length), (2, 0, 25))

    def test_rejects_non_digit_cache_buster_when_enabled(self) -> None:
        request = RequestFactory().get(
            "/api/",
            {
                "draw": "1",
                "start": "0",
                "length": "10",
                "search[value]": "",
                "search[regex]": "false",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
                "_": "not-a-number",
            },
        )

        with self.assertRaisesMessage(ValueError, "Invalid query parameters."):
            parse_datatables_request_base(
                request,
                allowed_params={
                    "draw",
                    "start",
                    "length",
                    "search[value]",
                    "search[regex]",
                    "columns[0][search][value]",
                    "columns[0][search][regex]",
                },
                allow_cache_buster=True,
            )

    def test_rejects_invalid_search_regex_flag(self) -> None:
        request = RequestFactory().get(
            "/api/",
            {
                "draw": "1",
                "start": "0",
                "length": "10",
                "search[value]": "",
                "search[regex]": "true",
                "columns[0][search][value]": "",
                "columns[0][search][regex]": "false",
            },
        )

        with self.assertRaisesMessage(ValueError, "Invalid query parameters."):
            parse_datatables_request_base(
                request,
                allowed_params={
                    "draw",
                    "start",
                    "length",
                    "search[value]",
                    "search[regex]",
                    "columns[0][search][value]",
                    "columns[0][search][regex]",
                },
                allow_cache_buster=False,
            )
