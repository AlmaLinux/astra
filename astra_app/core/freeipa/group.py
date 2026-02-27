import logging

from django.core.cache import cache
from python_freeipa import ClientMeta, exceptions

from core.freeipa.circuit_breaker import (
    _elections_freeipa_circuit_open,
    _is_freeipa_availability_error,
    _record_elections_freeipa_availability_failure,
    _reset_elections_freeipa_circuit_failures,
)
from core.freeipa.client import _with_freeipa_service_client_retry
from core.freeipa.exceptions import (
    FreeIPAMisconfiguredError,
    FreeIPAOperationFailed,
    FreeIPAUnavailableError,
)
from core.freeipa.user import _FreeIPAClientMixin
from core.freeipa.utils import (
    _clean_str_list,
    _compact_repr,
    _group_cache_key,
    _groups_list_cache_key,
    _invalidate_group_cache,
    _invalidate_groups_list_cache,
    _invalidate_user_cache,
    _raise_if_freeipa_failed,
)

logger = logging.getLogger("core.backends")


def get_freeipa_group_for_elections(*, cn: str, require_fresh: bool = False) -> FreeIPAGroup:
    """Fetch a FreeIPA group for elections-critical checks.

    Uses a circuit breaker to fail closed when FreeIPA is unavailable and
    distinguishes missing groups from transient failures.
    """

    group_cn = str(cn or "").strip()
    if not group_cn:
        raise FreeIPAMisconfiguredError("FreeIPA group cn is required")

    if _elections_freeipa_circuit_open():
        raise FreeIPAUnavailableError("FreeIPA circuit breaker is open")

    cache_key = _group_cache_key(group_cn)
    if not require_fresh:
        cached_data = cache.get(cache_key)
        if cached_data is not None:
            return FreeIPAGroup(group_cn, cached_data)

    try:
        result = _with_freeipa_service_client_retry(
            FreeIPAGroup.get_client,
            lambda client: client.group_find(o_cn=group_cn, o_all=True, o_no_members=False),
        )
    except Exception as exc:
        if isinstance(exc, FreeIPAUnavailableError) or _is_freeipa_availability_error(exc):
            _record_elections_freeipa_availability_failure()
            logger.exception("FreeIPA elections group lookup failed cn=%s: %s", group_cn, exc)
            raise FreeIPAUnavailableError("FreeIPA group lookup failed") from exc
        raise

    if not isinstance(result, dict) or result.get("count", 0) <= 0:
        raise FreeIPAMisconfiguredError(f"FreeIPA group not found: {group_cn}")

    group_data = (result.get("result") or [None])[0]
    if not isinstance(group_data, dict):
        raise FreeIPAMisconfiguredError(f"FreeIPA group not found: {group_cn}")

    _reset_elections_freeipa_circuit_failures()

    cache.set(cache_key, group_data)
    return FreeIPAGroup(group_cn, group_data)


class FreeIPAGroup(_FreeIPAClientMixin):
    """
    A non-persistent group object backed by FreeIPA.
    """

    def __init__(self, cn, group_data=None):
        self.cn = str(cn).strip() if cn else ""
        self._group_data = group_data or {}

        description = self._group_data.get('description', None)
        if isinstance(description, list):
            description = description[0] if description else None
        self.description = str(description).strip() if description else ""

        self.members = _clean_str_list(self._group_data.get('member_user', []))

        self.member_groups = _clean_str_list(self._group_data.get('member_group', []))

        sponsors = None
        for key in ("membermanager_user", "membermanager", "membermanageruser_user"):
            if key in self._group_data:
                sponsors = self._group_data.get(key)
                break
        self.sponsors = _clean_str_list(sponsors)

        self.sponsor_groups = _clean_str_list(self._group_data.get("membermanager_group", []))

        fas_url = self._group_data.get('fasurl', None)
        if isinstance(fas_url, list):
            fas_url = fas_url[0] if fas_url else None
        self.fas_url = fas_url

        fas_mailing_list = self._group_data.get('fasmailinglist', None)
        if isinstance(fas_mailing_list, list):
            fas_mailing_list = fas_mailing_list[0] if fas_mailing_list else None
        self.fas_mailing_list = fas_mailing_list

        self.fas_irc_channels = _clean_str_list(self._group_data.get('fasircchannel', []))

        fas_discussion_url = self._group_data.get('fasdiscussionurl', None)
        if isinstance(fas_discussion_url, list):
            fas_discussion_url = fas_discussion_url[0] if fas_discussion_url else None
        self.fas_discussion_url = fas_discussion_url

        fasgroup_field = self._group_data.get('fasgroup', None)
        if isinstance(fasgroup_field, list):
            fasgroup_field = fasgroup_field[0] if fasgroup_field else None
        if fasgroup_field is not None:
            if isinstance(fasgroup_field, bool):
                self.fas_group = bool(fasgroup_field)
            else:
                s = str(fasgroup_field).strip().upper()
                self.fas_group = s in {"TRUE", "T", "YES", "Y", "1", "ON"}
        else:
            object_classes = _clean_str_list(self._group_data.get('objectclass', []))
            self.fas_group = 'fasgroup' in [oc.lower() for oc in object_classes]

    def __str__(self):
        return self.cn

    @classmethod
    def all(cls):
        """
        Returns a list of all groups from FreeIPA.
        """

        def _fetch_groups() -> list[dict[str, object]]:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_find(o_all=True, o_no_members=False, o_sizelimit=0, o_timelimit=0),
            )
            return result.get('result', [])

        try:
            groups = cache.get_or_set(_groups_list_cache_key(), _fetch_groups) or []
            return [cls(g['cn'][0], g) for g in groups]
        except Exception as e:
            logger.exception(f"Failed to list groups: {e}")
            return []

    @classmethod
    def get(cls, cn):
        """
        Fetch a single group by cn.
        """
        cache_key = _group_cache_key(cn)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return cls(cn, cached_data)

        try:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_find(o_cn=cn, o_all=True, o_no_members=False),
            )
            if result['count'] > 0:
                group_data = result['result'][0]
                cache.set(cache_key, group_data)
                return cls(cn, group_data)
        except Exception as e:
            logger.exception(f"Failed to get group cn={cn}: {e}")
        return None

    @classmethod
    def create(cls, cn, description=None, fas_group: bool = False):
        """
        Create a new group in FreeIPA. If `fas_group` is True, attempt to
        request the fasGroup objectClass at creation time.
        """
        try:
            kwargs = {}
            if description:
                kwargs['o_description'] = description

            if fas_group:
                kwargs['fasgroup'] = True

            _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.group_add(cn, **kwargs),
            )
            _invalidate_groups_list_cache()
            return cls.get(cn)
        except Exception:
            logger.exception("Failed to create group cn=%s", cn)
            raise

    def save(self):
        """
        Updates the group data in FreeIPA.
        """

        def _first_str(value: object) -> str:
            if isinstance(value, list):
                value = value[0] if value else ""
            return str(value or "").strip()

        old_description = _first_str(self._group_data.get("description"))
        old_fas_url = _first_str(self._group_data.get("fasurl"))
        old_fas_mailing_list = _first_str(self._group_data.get("fasmailinglist"))
        old_fas_discussion_url = _first_str(self._group_data.get("fasdiscussionurl"))
        old_fas_irc_channels = set(_clean_str_list(self._group_data.get("fasircchannel", [])))

        new_description = str(self.description or "").strip()
        new_fas_url = str(self.fas_url or "").strip()
        new_fas_mailing_list = str(self.fas_mailing_list or "").strip()
        new_fas_discussion_url = str(self.fas_discussion_url or "").strip()
        new_fas_irc_channels = [str(ch or "").strip() for ch in (self.fas_irc_channels or [])]
        new_fas_irc_channels = [ch for ch in new_fas_irc_channels if ch]
        new_fas_irc_channels_set = set(new_fas_irc_channels)

        setattrs: list[str] = []
        addattrs: list[str] = []
        delattrs: list[str] = []

        def _maybe_update_single(attr: str, *, old: str, new: str) -> None:
            if (old or "") == (new or ""):
                return
            if new:
                setattrs.append(f"{attr}={new}")
            elif old:
                delattrs.append(f"{attr}=")

        _maybe_update_single("description", old=old_description, new=new_description)
        _maybe_update_single("fasurl", old=old_fas_url, new=new_fas_url)
        _maybe_update_single("fasmailinglist", old=old_fas_mailing_list, new=new_fas_mailing_list)
        _maybe_update_single("fasdiscussionurl", old=old_fas_discussion_url, new=new_fas_discussion_url)

        if old_fas_irc_channels != new_fas_irc_channels_set:
            to_remove = old_fas_irc_channels - new_fas_irc_channels_set
            to_add = new_fas_irc_channels_set - old_fas_irc_channels

            for ch in sorted(to_remove, key=str.lower):
                delattrs.append(f"fasircchannel={ch}")
            for ch in sorted(to_add, key=str.lower):
                addattrs.append(f"fasircchannel={ch}")

        try:
            if setattrs or addattrs or delattrs:
                try:
                    kwargs: dict[str, object] = {}
                    if setattrs:
                        kwargs["o_setattr"] = setattrs
                    if addattrs:
                        kwargs["o_addattr"] = addattrs
                    if delattrs:
                        kwargs["o_delattr"] = delattrs
                    _with_freeipa_service_client_retry(
                        self.get_client,
                        lambda client: client.group_mod(self.cn, **kwargs),
                    )
                except exceptions.BadRequest as e:
                    if "no modifications to be performed" not in str(e).lower():
                        raise
                    logger.info("FreeIPA group_mod was a no-op cn=%s", self.cn)
            else:
                return

            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception as e:
            logger.exception("Failed to update group cn=%s: %s", self.cn, e)
            raise

    def delete(self):
        """
        Delete the group from FreeIPA.
        First remove all members, then delete the group.
        """
        try:
            if self.members:
                res = _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: client.group_remove_member(self.cn, o_user=self.members),
                )
                _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn}")
                for username in self.members:
                    _invalidate_user_cache(username)

            if self.member_groups:
                res = _with_freeipa_service_client_retry(
                    self.get_client,
                    lambda client: client.group_remove_member(self.cn, o_group=self.member_groups),
                )
                _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn}")

            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_del(self.cn),
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
        except Exception:
            logger.exception("Failed to delete group cn=%s", self.cn)
            raise

    def add_member(self, username):
        from core.freeipa.user import FreeIPAUser

        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_add_member(self.cn, o_user=[username]),
            )
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_user_cache(username)
            _invalidate_groups_list_cache()
            fresh_group = FreeIPAGroup.get(self.cn)
            fresh_user = FreeIPAUser.get(username)
            if fresh_group and username not in fresh_group.members:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but membership not present after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
            if fresh_user and self.cn not in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but user does not show membership after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to add member username=%s group=%s", username, self.cn)
            raise

    def add_sponsor(self, username: str) -> None:
        username = username.strip()
        if not username:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "group_add_member_manager", [self.cn], {"user": [username]})
                except Exception:
                    return self._rpc(client, "group_add_member_manager", [self.cn], {"users": [username]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_add_member_manager", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to add sponsor username=%s group=%s", username, self.cn)
            raise

    def remove_sponsor(self, username: str) -> None:
        username = username.strip()
        if not username:
            return
        try:
            def _do(client: ClientMeta):
                try:
                    return self._rpc(client, "group_remove_member_manager", [self.cn], {"user": [username]})
                except Exception:
                    return self._rpc(client, "group_remove_member_manager", [self.cn], {"users": [username]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_remove_member_manager", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to remove sponsor username=%s group=%s", username, self.cn)
            raise

    def add_sponsor_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return self._rpc(client, "group_add_member_manager", [self.cn], {"group": [group_cn]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(
                res,
                action="group_add_member_manager",
                subject=f"group={self.cn} sponsor_group={group_cn}",
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to add sponsor group parent=%s sponsor_group=%s", self.cn, group_cn)
            raise

    def remove_sponsor_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return self._rpc(client, "group_remove_member_manager", [self.cn], {"group": [group_cn]})

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(
                res,
                action="group_remove_member_manager",
                subject=f"group={self.cn} sponsor_group={group_cn}",
            )
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
        except Exception:
            logger.exception("Failed to remove sponsor group parent=%s sponsor_group=%s", self.cn, group_cn)
            raise

    def remove_member(self, username):
        from core.freeipa.user import FreeIPAUser

        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_remove_member(self.cn, o_user=[username]),
            )
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn} user={username}")
            _invalidate_group_cache(self.cn)
            _invalidate_user_cache(username)
            _invalidate_groups_list_cache()
            fresh_group = FreeIPAGroup.get(self.cn)
            fresh_user = FreeIPAUser.get(username)
            if fresh_group and username in fresh_group.members:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but membership still present after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
            if fresh_user and self.cn in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but user still shows membership after refresh "
                    f"(group={self.cn} user={username} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to remove member username=%s group=%s", username, self.cn)
            raise

    def add_member_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return client.group_add_member(self.cn, o_group=[group_cn])

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"group={self.cn} group_member={group_cn}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
            self._recursive_member_usernames_cache = None
        except Exception:
            logger.exception("Failed to add member group parent=%s child=%s", self.cn, group_cn)
            raise

    def remove_member_group(self, group_cn: str) -> None:
        group_cn = str(group_cn).strip()
        if not group_cn:
            return
        try:
            def _do(client: ClientMeta):
                return client.group_remove_member(self.cn, o_group=[group_cn])

            res = _with_freeipa_service_client_retry(self.get_client, _do)
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"group={self.cn} group_member={group_cn}")
            _invalidate_group_cache(self.cn)
            _invalidate_groups_list_cache()
            FreeIPAGroup.get(self.cn)
            self._recursive_member_usernames_cache = None
        except Exception:
            logger.exception("Failed to remove member group parent=%s child=%s", self.cn, group_cn)
            raise

    def member_usernames_recursive(self, *, fas_only: bool = False) -> set[str]:
        if not fas_only:
            cached = getattr(self, "_recursive_member_usernames_cache", None)
            if isinstance(cached, set):
                return set(cached)
        users = self._member_usernames_recursive(visited=set(), fas_only=fas_only)
        if not fas_only:
            self._recursive_member_usernames_cache = set(users)
        return users

    def _member_usernames_recursive(self, *, visited: set[str], fas_only: bool) -> set[str]:
        cn = str(self.cn or "").strip()
        key = cn.lower()
        if key and key in visited:
            return set()
        if key:
            visited.add(key)

        if fas_only and not self.fas_group:
            return set()

        users: set[str] = set(self.members)
        for child_cn in sorted(set(self.member_groups), key=str.lower):
            child = FreeIPAGroup.get(child_cn)
            if child is None:
                continue
            if fas_only and not child.fas_group:
                continue
            try:
                users |= child._member_usernames_recursive(visited=visited, fas_only=fas_only)
            except Exception:
                logger.exception("Failed to expand nested group members parent=%s child=%s", self.cn, child_cn)
                continue
        return users

    def member_count_recursive(self, *, fas_only: bool = False) -> int:
        if not fas_only:
            cached = getattr(self, "_recursive_member_usernames_cache", None)
            if isinstance(cached, set):
                return len(cached)
        return len(self.member_usernames_recursive(fas_only=fas_only))


__all__ = ["FreeIPAGroup", "get_freeipa_group_for_elections"]
