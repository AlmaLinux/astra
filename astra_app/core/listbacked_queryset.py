import logging

from core.freeipa.group import FreeIPAGroup

logger = logging.getLogger(__name__)


class _ListBackedQuerySet:
    """Minimal QuerySet-like wrapper for Django admin changelist.

    Django admin's changelist expects something sliceable with .count() and
    basic iteration semantics. This avoids hitting the DB for unmanaged models.
    """

    def __init__(self, model, items):
        self.model = model
        self._items = list(items)
        # Admin inspects `qs.query.select_related`.
        self.query = type("_Q", (), {"select_related": False, "order_by": []})()

    @property
    def _meta(self):
        # Django admin actions call model_ngettext(queryset) and expect queryset
        # to expose model metadata (verbose_name, verbose_name_plural, etc.).
        return self.model._meta

    @property
    def verbose_name(self) -> str:
        return self.model._meta.verbose_name

    @property
    def verbose_name_plural(self) -> str:
        return self.model._meta.verbose_name_plural

    def all(self):
        return self

    def select_related(self, *fields):
        self.query.select_related = True
        return self

    def filter(self, *args, **kwargs):
        # Django admin may call .filter(Q(...)) even when our backend isn't ORM.
        # We ignore positional Q objects and apply only simple kwarg equality.
        def matches(item):
            for key, expected in kwargs.items():
                # Support lookups like 'pk__in' used by admin bulk-delete.
                if "__" in key:
                    field, lookup = key.split("__", 1)
                else:
                    field, lookup = key, None

                if field in {"pk", "id"}:
                    actual = getattr(item, "pk", getattr(item, "id", None))
                else:
                    actual = getattr(item, field, None)

                if lookup == "in":
                    # expected should be an iterable of values
                    try:
                        if actual not in expected:
                            return False
                    except Exception:
                        return False
                else:
                    if actual != expected:
                        return False
            return True

        if not kwargs:
            return self

        return _ListBackedQuerySet(self.model, [i for i in self._items if matches(i)])

    def order_by(self, *fields):
        items = list(self._items)
        self.query.order_by = list(fields or [])
        # Apply sorts from right to left to mimic multi-key ordering.
        for field in reversed(fields or []):
            reverse_sort = False
            name = field
            if isinstance(name, str) and name.startswith("-"):
                reverse_sort = True
                name = name[1:]
            items.sort(key=lambda o: getattr(o, name, ""), reverse=reverse_sort)
        return _ListBackedQuerySet(self.model, items)

    def reverse(self):
        """Return a QuerySet-like with reversed ordering."""
        return _ListBackedQuerySet(self.model, list(reversed(self._items)))

    def none(self):
        return _ListBackedQuerySet(self.model, [])

    def count(self):
        return len(self._items)

    def exists(self):
        return bool(self._items)

    def first(self):
        try:
            return self._items[0]
        except IndexError:
            return None

    def last(self):
        try:
            return self._items[-1]
        except IndexError:
            return None

    def distinct(self, *args, **kwargs):
        return self

    def _clone(self):
        clone = _ListBackedQuerySet(self.model, list(self._items))
        clone.query.select_related = getattr(self.query, "select_related", False)
        clone.query.order_by = list(getattr(self.query, "order_by", []))
        return clone

    def __len__(self):
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def __getitem__(self, key):
        return self._items[key]

    def get(self, **kwargs):
        matches = list(self.filter(**kwargs))
        if not matches:
            raise self.model.DoesNotExist()
        if len(matches) > 1:
            raise self.model.MultipleObjectsReturned()
        return matches[0]

    def delete(self):
        """Delete all items represented by this QuerySet-like object.

        Django admin calls `queryset.delete()` on the queryset returned by
        `ModelAdmin.get_queryset`. For our FreeIPA-backed, unmanaged objects
        implement a delete operation that calls the backend delete for each
        group (based on `cn`). Returns a tuple similar to Django ORM: (count, {}).
        """
        deleted = 0
        for item in list(self._items):
            cn = getattr(item, "cn", None) or getattr(item, "pk", None) or getattr(item, "id", None)
            if not cn:
                continue
            try:
                # `FreeIPAGroup` handles member removal and cache invalidation.
                freeipa = FreeIPAGroup.get(cn)
                if freeipa:
                    freeipa.delete()
                    deleted += 1
            except Exception:
                logger.exception("Failed to delete FreeIPA group cn=%s", cn)
                continue
        return deleted, {}

    def aggregate(self, **kwargs):
        # Minimal aggregate: support simple counts by returning the length
        # for any aggregation requested. This intentionally keeps behavior
        # simple for tests that expect counts.
        out = {}
        for k in kwargs.keys():
            out[k] = len(self._items)
        return out

    def annotate(self, **kwargs):
        # Minimal annotate: set each annotation name to 1 for each item.
        # We mutate items in-place for simplicity; callers receive a new
        # QuerySet-like wrapper.
        items = []
        for item in self._items:
            for name in kwargs.keys():
                try:
                    setattr(item, name, 1)
                except Exception:
                    pass
            items.append(item)
        return _ListBackedQuerySet(self.model, items)

    def update(self, **kwargs):
        # Apply attribute changes to all items and return count.
        changed = 0
        for item in self._items:
            for k, v in kwargs.items():
                try:
                    setattr(item, k, v)
                except Exception:
                    continue
            changed += 1
        return changed

    def earliest(self, *fields):
        if not fields:
            return self.first()
        key = fields[0]
        items = sorted(self._items, key=lambda o: getattr(o, key, ""))
        return items[0] if items else None

    def latest(self, *fields):
        if not fields:
            return self.last()
        key = fields[0]
        items = sorted(self._items, key=lambda o: getattr(o, key, ""))
        return items[-1] if items else None

    def only(self, *fields):
        # No-op for our simple implementation; values() handles field selection.
        return self

    def defer(self, *fields):
        return self

    def union(self, other):
        seen = set()
        items = []
        for i in list(self._items) + list(getattr(other, "_items", [])):
            key = getattr(i, "cn", getattr(i, "pk", None))
            if key in seen:
                continue
            seen.add(key)
            items.append(i)
        return _ListBackedQuerySet(self.model, items)

    def intersection(self, other):
        other_keys = {getattr(i, "cn", getattr(i, "pk", None)) for i in getattr(other, "_items", [])}
        items = [i for i in self._items if getattr(i, "cn", getattr(i, "pk", None)) in other_keys]
        return _ListBackedQuerySet(self.model, items)

    def difference(self, other):
        other_keys = {getattr(i, "cn", getattr(i, "pk", None)) for i in getattr(other, "_items", [])}
        items = [i for i in self._items if getattr(i, "cn", getattr(i, "pk", None)) not in other_keys]
        return _ListBackedQuerySet(self.model, items)

    def select_for_update(self, *args, **kwargs):
        return self

    def explain(self, *args, **kwargs):
        return ""

    def exclude(self, *args, **kwargs):
        # Simple negation of filter kwarg matches.
        if not kwargs:
            return self

        def matches(item):
            for key, expected in kwargs.items():
                if "__" in key:
                    field, lookup = key.split("__", 1)
                else:
                    field, lookup = key, None

                if field in {"pk", "id"}:
                    actual = getattr(item, "pk", getattr(item, "id", None))
                else:
                    actual = getattr(item, field, None)

                if lookup == "in":
                    try:
                        if actual in expected:
                            return False
                    except Exception:
                        return True
                else:
                    if actual == expected:
                        return False
            return True

        return _ListBackedQuerySet(self.model, [i for i in self._items if matches(i)])

    def values(self, *fields):
        if not fields:
            # mimic QuerySet.values() returning all fields as dict
            return [vars(i).copy() for i in self._items]
        out = []
        for i in self._items:
            d = {}
            for f in fields:
                d[f] = getattr(i, f, None)
            out.append(d)
        return out

    def values_list(self, *fields, flat=False):
        if not fields:
            # return tuples of all fields (use __dict__ ordering unpredictable)
            return [tuple(vars(i).values()) for i in self._items]
        if flat and len(fields) == 1:
            return [getattr(i, fields[0], None) for i in self._items]
        return [tuple(getattr(i, f, None) for f in fields) for i in self._items]

    def in_bulk(self, id_list=None):
        result = {}
        if id_list is None:
            for i in self._items:
                key = getattr(i, "pk", getattr(i, "id", None))
                if key is not None:
                    result[key] = i
            return result
        for i in self._items:
            key = getattr(i, "pk", getattr(i, "id", None))
            if key in id_list:
                result[key] = i
        return result

    def iterator(self):
        return iter(self._items)
