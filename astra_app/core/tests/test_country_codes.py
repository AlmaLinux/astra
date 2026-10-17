from django.conf import settings
from django.test import SimpleTestCase

from core.country_codes import embargoed_country_label_from_user_data


class CountryCodesTests(SimpleTestCase):
    def test_embargoed_country_label_from_user_data_returns_label_for_embargoed_code(self) -> None:
        attr_name = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        label = embargoed_country_label_from_user_data(
            user_data={attr_name: ["RU"]},
            embargoed_codes={"RU", "IR"},
        )

        self.assertIsNotNone(label)
        assert label is not None
        self.assertTrue(label.endswith("(RU)"))

    def test_embargoed_country_label_from_user_data_returns_none_for_non_embargoed_code(self) -> None:
        attr_name = settings.SELF_SERVICE_ADDRESS_COUNTRY_ATTR
        label = embargoed_country_label_from_user_data(
            user_data={attr_name: ["US"]},
            embargoed_codes={"RU", "IR"},
        )

        self.assertIsNone(label)