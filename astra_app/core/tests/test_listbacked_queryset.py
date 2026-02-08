
from unittest.mock import patch

from django.db.models import Count
from django.test import TestCase

from core.admin import _ListBackedQuerySet
from core.models import IPAGroup


class ListBackedQuerySetTests(TestCase):
    def test_filter_pk_in_returns_selected_items(self):
        a = IPAGroup(cn="g-a", description="", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        b = IPAGroup(cn="g-b", description="", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        c = IPAGroup(cn="g-c", description="", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)

        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        # Admin sends pk__in with list of pks (for our models cn acts as pk)
        filtered = list(qs.filter(pk__in=["g-a", "g-c"]))
        self.assertEqual([i.cn for i in filtered], ["g-a", "g-c"])

    def test_delete_calls_freeipa_delete_and_returns_count(self):
        a = IPAGroup(cn="g-x", description="", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        b = IPAGroup(cn="g-y", description="", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)

        qs = _ListBackedQuerySet(IPAGroup, [a, b])

        # Patch FreeIPAGroup.get to return an object with a delete() method
        class _FakeFreeIPAGroup:
            def __init__(self):
                self.deleted = 0

            def delete(self):
                self.deleted += 1

        fake = _FakeFreeIPAGroup()

        with patch("core.admin.FreeIPAGroup.get", return_value=fake) as mock_get:
            deleted_count, extra = qs.delete()

        # Both items should have been deleted (delete() called twice)
        self.assertEqual(deleted_count, 2)
        self.assertEqual(extra, {})
        # Ensure FreeIPAGroup.get was called with each cn
        self.assertEqual(mock_get.call_count, 2)

    def _make_groups(self):
        a = IPAGroup(cn="g-a", description="aaa", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        b = IPAGroup(cn="g-b", description="bbb", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        c = IPAGroup(cn="g-c", description="ccc", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        return a, b, c

    def test_count_and_len(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        self.assertEqual(qs.count(), 3)
        self.assertEqual(len(qs), 3)

    def test_order_by_asc_and_desc(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [b, c, a])
        asc = list(qs.order_by("cn"))
        self.assertEqual([i.cn for i in asc], ["g-a", "g-b", "g-c"])

        desc = list(qs.order_by("-cn"))
        self.assertEqual([i.cn for i in desc], ["g-c", "g-b", "g-a"])

    def test_chain_filter_and_order(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        result = list(qs.filter(cn__in=["g-a", "g-c"]).order_by("-cn"))
        self.assertEqual([i.cn for i in result], ["g-c", "g-a"])

    def test_slice_and_index(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        self.assertEqual(qs[0].cn, "g-a")
        slice_list = qs[1:3]
        self.assertEqual([i.cn for i in slice_list], ["g-b", "g-c"])

    def test_get_single_and_exceptions(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        found = qs.get(cn="g-b")
        self.assertEqual(found.cn, "g-b")

        with self.assertRaises(IPAGroup.DoesNotExist):
            qs.get(cn="no-such")

        # Duplicate cn should trigger MultipleObjectsReturned
        dup = IPAGroup(cn="g-b", description="dup", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        qs2 = _ListBackedQuerySet(IPAGroup, [a, b, dup])
        with self.assertRaises(IPAGroup.MultipleObjectsReturned):
            qs2.get(cn="g-b")

    def test_select_related_and_clone_preserve_query(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        qs2 = qs.select_related("dummy")
        self.assertTrue(qs2.query.select_related)
        clone = qs2._clone()
        self.assertTrue(clone.query.select_related)

    def test_distinct_and_filter_no_kwargs(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        self.assertIs(qs.distinct(), qs)
        # filter with no kwargs returns the same object
        self.assertIs(qs.filter(), qs)

    def test_iteration_and_list_conversion(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        self.assertEqual([i.cn for i in qs], ["g-a", "g-b", "g-c"])

class QuerySetDocsComplianceTests(TestCase):
    """Tests that exercise a broad swath of QuerySet API behaviors from
    Django's QuerySet reference (for compatibility with Django admin and
    other code paths). These tests are intentionally written to assert the
    documented behavior; missing methods will cause failures and indicate
    implementation gaps.
    """

    def setUp(self):
        a = IPAGroup(cn="g-a", description="aaa", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        b = IPAGroup(cn="g-b", description="bbb", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        c = IPAGroup(cn="g-c", description="ccc", fas_url="", fas_mailing_list="", fas_discussion_url="", fas_group=False)
        self.items = [a, b, c]
        self.qs = _ListBackedQuerySet(IPAGroup, list(self.items))

    def test_none_returns_empty_queryset(self):
        # Django's QuerySet.none() returns an empty queryset-like object.
        none_qs = self.qs.none()
        self.assertEqual(len(list(none_qs)), 0)

    def test_aggregate_count(self):
        # Aggregate a simple count using Django's Count expression.
        out = self.qs.aggregate(total=Count("cn"))
        # Expect a mapping with 'total' key and integer value equal to 3
        self.assertIn("total", out)
        self.assertEqual(int(out["total"]), 3)

    def test_annotate_adds_attributes(self):
        # Annotate should allow adding computed attributes per row.
        annotated = list(self.qs.annotate(member_count=Count("cn")))
        self.assertEqual(len(annotated), 3)
        # Each returned item should be a dict-like or object with attribute
        # accessible by the annotation name; prefer attribute access if present.
        first = annotated[0]
        # Accept either dicts or objects with attribute
        val = getattr(first, "member_count", None) if not isinstance(first, dict) else first.get("member_count")
        self.assertEqual(int(val), 1)

    def test_update_applies_changes(self):
        # Update should bulk-change fields on all members and return count.
        updated = self.qs.update(description="new")
        self.assertEqual(int(updated), 3)
        self.assertTrue(all(i.description == "new" for i in self.qs))

    def test_earliest_and_latest(self):
        earliest = self.qs.earliest("cn")
        latest = self.qs.latest("cn")
        self.assertEqual(earliest.cn, "g-a")
        self.assertEqual(latest.cn, "g-c")

    def test_only_and_defer(self):
        # only() should allow selecting a subset of fields; values('cn') should still work
        limited = self.qs.only("cn")
        self.assertEqual([d["cn"] for d in limited.values("cn")], ["g-a", "g-b", "g-c"])

    def test_union_intersection_difference(self):
        a_qs = _ListBackedQuerySet(IPAGroup, [self.items[0], self.items[1]])
        b_qs = _ListBackedQuerySet(IPAGroup, [self.items[1], self.items[2]])
        u = list(a_qs.union(b_qs))
        i = list(a_qs.intersection(b_qs))
        d = list(a_qs.difference(b_qs))
        self.assertEqual(sorted([x.cn for x in u]), ["g-a", "g-b", "g-c"])
        self.assertEqual([x.cn for x in i], ["g-b"])
        self.assertEqual([x.cn for x in d], ["g-a"])

    def test_select_for_update_and_explain(self):
        qs_locked = self.qs.select_for_update()
        self.assertIsInstance(qs_locked, _ListBackedQuerySet)
        expl = self.qs.explain()
        self.assertIsInstance(expl, str)

    def test_exists_first_last_and_reverse(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        self.assertTrue(qs.exists())
        self.assertEqual(qs.first().cn, "g-a")
        self.assertEqual(qs.last().cn, "g-c")
        rev = list(qs.reverse())
        self.assertEqual([i.cn for i in rev], ["g-c", "g-b", "g-a"])

    def test_exclude_simple_and_in(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        out = list(qs.exclude(cn="g-b"))
        self.assertEqual([i.cn for i in out], ["g-a", "g-c"])
        out2 = list(qs.exclude(cn__in=["g-a", "g-c"]))
        self.assertEqual([i.cn for i in out2], ["g-b"])

    def test_values_and_values_list_and_in_bulk(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        vals = qs.values("cn", "description")
        self.assertIsInstance(vals, list)
        self.assertEqual(vals[0]["cn"], "g-a")

        vl = qs.values_list("cn")
        self.assertEqual(vl[0], ("g-a",))

        flat = qs.values_list("cn", flat=True)
        self.assertEqual(flat, ["g-a", "g-b", "g-c"])

        bulk = qs.in_bulk(["g-a", "g-c"])
        self.assertIn("g-a", bulk)
        self.assertNotIn("g-b", bulk)

    def test_iterator_and_in_bulk_none(self):
        a, b, c = self._make_groups()
        qs = _ListBackedQuerySet(IPAGroup, [a, b, c])
        it = qs.iterator()
        self.assertEqual(next(it).cn, "g-a")
        all_bulk = qs.in_bulk()
        # Keys should include pks.
        self.assertIn("g-a", all_bulk)
        self.assertIn("g-c", all_bulk)

    def _make_groups(self):
        # compatibility helper for older tests
        return tuple(self.items)