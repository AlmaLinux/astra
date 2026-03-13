import datetime
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from tablib import Dataset

from core.csv_import_utils import (
    attach_unmatched_csv_to_result,
    get_result_attr,
    get_result_row_errors,
    get_result_rows,
    get_result_totals,
    normalize_csv_name,
    parse_csv_date,
    resolve_column_header,
    sanitize_csv_cell,
)


class ParseCsvDateTests(TestCase):
    def test_parse_csv_date_preserves_date_only_formats(self) -> None:
        cases = [
            ("2022-07-12", datetime.date(2022, 7, 12)),
            ("2022/07/12", datetime.date(2022, 7, 12)),
            ("07/12/2022", datetime.date(2022, 7, 12)),
            ("07/12/22", datetime.date(2022, 7, 12)),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                parsed = parse_csv_date(raw_value)
                self.assertEqual(parsed, expected)
                self.assertNotIsInstance(parsed, datetime.datetime)

    def test_parse_csv_date_accepts_us_timestamp_values(self) -> None:
        cases = [
            ("7/12/2022 5:52:20", datetime.date(2022, 7, 12)),
            ("11/21/2023 19:41:09", datetime.date(2023, 11, 21)),
            ("8/10/2022 10:18:27", datetime.date(2022, 8, 10)),
        ]

        for raw_value, expected in cases:
            with self.subTest(raw_value=raw_value):
                self.assertEqual(parse_csv_date(raw_value), expected)

    def test_parse_csv_date_accepts_iso_datetime_with_timezone(self) -> None:
        self.assertEqual(
            parse_csv_date("2022-07-12T05:52:20Z"),
            datetime.date(2022, 7, 12),
        )

    def test_parse_csv_date_passes_explicit_dayfirst_yearfirst_flags(self) -> None:
        with patch(
            "core.csv_import_utils.parser.parse",
            return_value=datetime.datetime(2023, 1, 2, 12, 0, 0),
        ) as parse:
            parsed = parse_csv_date("01/02/2023")

        self.assertEqual(parsed, datetime.date(2023, 1, 2))
        parse.assert_called_once_with("01/02/2023", dayfirst=False, yearfirst=False)


class SanitizeCsvCellTests(TestCase):
    def test_sanitize_csv_cell_prefixes_formula_prefixes(self) -> None:
        cases = [
            "=2+2",
            "+SUM(A1:A2)",
            "-1+1",
            "@cmd",
            "\tvalue",
            "\rvalue",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_csv_cell(raw), f"'{raw}")

    def test_sanitize_csv_cell_keeps_safe_values_unchanged(self) -> None:
        self.assertEqual(sanitize_csv_cell("plain text"), "plain text")
        self.assertEqual(sanitize_csv_cell(""), "")


class NormalizeCsvNameTests(TestCase):
    def test_normalize_csv_name_is_case_insensitive(self) -> None:
        self.assertEqual(normalize_csv_name("Alice Example"), normalize_csv_name("aLiCe eXample"))

    def test_normalize_csv_name_uses_unicode_casefold(self) -> None:
        self.assertEqual(normalize_csv_name("Straße"), normalize_csv_name("STRASSE"))


class ResolveColumnHeaderTests(TestCase):
    def test_resolve_column_header_uses_exact_override_first(self) -> None:
        headers = ["Email", "Name"]
        header_by_norm = {"email": "Email", "name": "Name"}

        resolved = resolve_column_header(
            "email",
            headers,
            header_by_norm,
            {"email": "Email"},
            "email",
        )

        self.assertEqual(resolved, "Email")

    def test_resolve_column_header_uses_normalized_override(self) -> None:
        headers = ["E-mail Address", "Name"]
        header_by_norm = {"emailaddress": "E-mail Address", "name": "Name"}

        resolved = resolve_column_header(
            "email",
            headers,
            header_by_norm,
            {"email": "email address"},
            "email",
            "emailaddress",
        )

        self.assertEqual(resolved, "E-mail Address")

    def test_resolve_column_header_raises_on_unknown_override(self) -> None:
        with self.assertRaisesRegex(ValueError, "Column 'unknown' not found"):
            resolve_column_header(
                "email",
                ["Email"],
                {"email": "Email"},
                {"email": "unknown"},
                "email",
            )

    def test_resolve_column_header_uses_fallback_norms(self) -> None:
        resolved = resolve_column_header(
            "membership_end_date",
            ["Expiry Date"],
            {"expirydate": "Expiry Date"},
            {},
            "membershipenddate",
            "expirydate",
        )

        self.assertEqual(resolved, "Expiry Date")

    def test_resolve_column_header_returns_none_when_no_match(self) -> None:
        resolved = resolve_column_header(
            "membership_end_date",
            ["Email", "Name"],
            {"email": "Email", "name": "Name"},
            {},
            "membershipenddate",
            "expirydate",
        )

        self.assertIsNone(resolved)


class AttachUnmatchedCsvToResultTests(TestCase):
    def test_attach_unmatched_csv_to_result_sets_result_attrs_and_cache(self) -> None:
        result = SimpleNamespace()
        dataset = Dataset()
        dataset.headers = ["Email", "reason"]
        dataset.append(["missing@example.org", "No match"])

        with (
            patch("core.csv_import_utils.secrets.token_urlsafe", return_value="token-123"),
            patch("core.csv_import_utils.cache.set") as cache_set,
            patch("core.csv_import_utils.reverse", return_value="/admin/download/token-123/") as reverse,
        ):
            attach_unmatched_csv_to_result(
                result=result,
                dataset=dataset,
                cache_key_prefix="membership-import-unmatched",
                reverse_url_name="admin:core_membershipcsvimportlink_download_unmatched",
            )

        self.assertIn("missing@example.org", result.unmatched_csv_content)
        self.assertEqual(result.unmatched_download_url, "/admin/download/token-123/")
        cache_set.assert_called_once()
        cache_args, cache_kwargs = cache_set.call_args
        self.assertEqual(cache_args[0], "membership-import-unmatched:token-123")
        self.assertEqual(cache_kwargs.get("timeout"), 60 * 60)
        reverse.assert_called_once_with(
            "admin:core_membershipcsvimportlink_download_unmatched",
            kwargs={"token": "token-123"},
        )


class ImportResultAccessHelpersTests(TestCase):
    def test_get_result_totals_tolerates_missing_or_invalid_totals_attr(self) -> None:
        class BrokenTotalsResult:
            @property
            def totals(self) -> object:
                raise TypeError("broken totals")

        self.assertEqual(get_result_totals(SimpleNamespace()), {})
        self.assertEqual(get_result_totals(BrokenTotalsResult()), {})
        self.assertEqual(get_result_totals(SimpleNamespace(totals={"error": 2})), {"error": 2})

    def test_get_result_row_errors_supports_tuple_and_flat_shapes(self) -> None:
        pair_error = SimpleNamespace(error=ValueError("bad row"), traceback="traceback")
        flat_error = SimpleNamespace(error=ValueError("other row"), traceback="traceback", number=4)

        pair_result = SimpleNamespace(row_errors=lambda: [(3, [pair_error])])
        flat_result = SimpleNamespace(row_errors=lambda: [flat_error])

        self.assertEqual(get_result_row_errors(pair_result), ([(3, [pair_error])], []))
        self.assertEqual(get_result_row_errors(flat_result), ([], [flat_error]))

    def test_get_result_rows_supports_callable_list_like_and_fallback_rows(self) -> None:
        callable_row = SimpleNamespace(number=1)
        list_like_row = SimpleNamespace(number=2)
        fallback_row = SimpleNamespace(number=3)

        self.assertEqual(
            get_result_rows(SimpleNamespace(valid_rows=lambda: [callable_row]), "valid_rows"),
            [callable_row],
        )
        self.assertEqual(
            get_result_rows(SimpleNamespace(valid_rows=[list_like_row]), "valid_rows"),
            [list_like_row],
        )
        self.assertEqual(
            get_result_rows(SimpleNamespace(rows=[fallback_row]), "valid_rows", fallback_attr_name="rows"),
            [fallback_row],
        )

    def test_get_result_attr_returns_default_when_optional_attr_is_missing(self) -> None:
        class BrokenAttrResult:
            @property
            def unmatched_download_url(self) -> str:
                raise AttributeError("missing")

        self.assertEqual(get_result_attr(SimpleNamespace(), "unmatched_download_url", ""), "")
        self.assertEqual(get_result_attr(BrokenAttrResult(), "unmatched_download_url", ""), "")
        self.assertEqual(
            get_result_attr(SimpleNamespace(unmatched_download_url="/download/token/"), "unmatched_download_url", ""),
            "/download/token/",
        )
