import logging

from django.conf import settings
from django.utils.crypto import salted_hmac
from python_freeipa import ClientMeta, exceptions

from core.freeipa.client import (
    _get_current_viewer_username,
    _get_freeipa_service_client_cached,
    _with_freeipa_service_client_retry,
)
from core.freeipa.exceptions import FreeIPAOperationFailed
from core.freeipa.utils import (
    _clean_str_list,
    _compact_repr,
    _first_attr_ci,
    _invalidate_group_cache,
    _invalidate_groups_list_cache,
    _invalidate_user_cache,
    _invalidate_users_list_cache,
    _raise_if_freeipa_failed,
    _session_user_id_for_username,
    _user_cache_key,
    _users_list_cache_key,
)
from core.ipa_utils import bool_from_ipa

logger = logging.getLogger("core.backends")


class _FreeIPAPK:
    attname = 'username'
    name = 'username'

    def value_to_string(self, obj):
        username = getattr(obj, 'username', None)
        if username:
            return str(_session_user_id_for_username(username))
        return str(getattr(obj, 'pk', ''))


class _FreeIPAMeta:
    pk = _FreeIPAPK()


class DegradedFreeIPAUser:
    def __init__(self, username: str) -> None:
        self.username = str(username).strip() if username else ""
        self.backend = "core.freeipa.auth_backend.FreeIPAAuthBackend"
        self._meta = _FreeIPAMeta()
        self.is_authenticated = True
        self.is_anonymous = False
        self.is_staff = False
        self.is_superuser = False
        self.email = ""
        self.first_name = ""
        self.last_name = ""
        self.displayname = ""
        self.commonname = ""
        self.gecos = ""
        self.fasstatusnote = ""
        self.groups_list: list[str] = []
        self.timezone = ""
        self.last_login = None

    def get_username(self) -> str:
        return self.username

    def get_full_name(self) -> str:
        return self.displayname or self.username

    def get_short_name(self) -> str:
        return self.first_name or self.username

    def get_session_auth_hash(self) -> str:
        return salted_hmac('freeipa-user', self.username, secret=settings.SECRET_KEY).hexdigest()

    def get_all_permissions(self, obj: object | None = None) -> set[str]:
        return set()

    def has_perm(self, perm: str, obj: object | None = None) -> bool:
        return False

    def has_perms(self, perm_list: list[str], obj: object | None = None) -> bool:
        return False


class FreeIPAManager:
    """
    A mock manager to mimic Django's RelatedManager.
    """

    def __init__(self, iterable):
        self._iterable = iterable

    def all(self):
        return self._iterable

    def count(self):
        return len(self._iterable)

    def __iter__(self):
        return iter(self._iterable)


class _FreeIPAClientMixin:
    """Shared service-client helpers for FreeIPA-backed model classes."""

    @classmethod
    def get_client(cls) -> ClientMeta:
        """Return a FreeIPA client authenticated as the service account."""
        return _get_freeipa_service_client_cached()

    @classmethod
    def _rpc(cls, client: ClientMeta, method: str, args: list[object] | None, params: dict[str, object] | None):
        """Call a FreeIPA JSON-RPC method.

        python-freeipa's ClientMeta doesn't generate methods for custom plugin
        commands (e.g. fasagreement_*). All clients expose a raw `_request()`
        method which can call any command the server supports.
        """
        # hasattr needed: duck-typing check against third-party ClientMeta
        if not hasattr(client, "_request"):
            raise FreeIPAOperationFailed("FreeIPA client does not support raw JSON-RPC requests")
        return client._request(method, args or [], params or {})


class FreeIPAUser(_FreeIPAClientMixin):
    """
    A non-persistent user object backed by FreeIPA.
    """

    def __init__(self, username, user_data=None):
        self.username = str(username).strip() if username else ""
        self.backend = "core.freeipa.auth_backend.FreeIPAAuthBackend"
        self._user_data = dict(user_data) if isinstance(user_data, dict) else {}
        self.is_authenticated = True
        self.is_anonymous = False
        self._meta = _FreeIPAMeta()

        self.last_login = None

        def _first(key, default=None):
            value = self._user_data.get(key, default)
            if isinstance(value, list):
                return value[0] if value else default
            return value

        self.first_name = _first('givenname') or ""
        self.last_name = _first('sn') or ""
        self.commonname = _first("cn") or ""
        self.displayname = _first("displayname") or ""
        self.gecos = _first("gecos") or ""
        self.email = _first('mail') or ""

        krb_last_pwd_change = _first_attr_ci(self._user_data, "krbLastPwdChange", None)
        self.last_password_change = str(krb_last_pwd_change).strip() if krb_last_pwd_change else ""

        fas_status_note = _first_attr_ci(self._user_data, "fasstatusnote", None)
        self.fasstatusnote = str(fas_status_note).strip() if fas_status_note else ""

        fas_is_private_raw = _first_attr_ci(self._user_data, "fasIsPrivate", None)
        if fas_is_private_raw is None:
            fas_is_private_raw = _first_attr_ci(self._user_data, "fasisprivate", None)
        self.fas_is_private = bool_from_ipa(fas_is_private_raw, default=False)

        nsaccountlock = self._user_data.get('nsaccountlock', False)
        if isinstance(nsaccountlock, list):
            nsaccountlock = nsaccountlock[0] if nsaccountlock else False
        self.is_active = not bool(nsaccountlock)

        self.direct_groups_list = _clean_str_list(self._user_data.get("memberof_group", []))
        self.indirect_groups_list = _clean_str_list(self._user_data.get("memberofindirect_group", []))
        self.groups_list = _clean_str_list(self.direct_groups_list + self.indirect_groups_list)

        admin_group = settings.FREEIPA_ADMIN_GROUP
        self.is_staff = admin_group in self.groups_list
        self.is_superuser = admin_group in self.groups_list

        viewer_username = _get_current_viewer_username()
        if self.fas_is_private and viewer_username and viewer_username.lower() != self.username.lower():
            self.anonymize()

    @property
    def groups(self):
        from core.freeipa.group import FreeIPAGroup

        return FreeIPAManager([FreeIPAGroup(cn) for cn in self.groups_list])

    @property
    def user_permissions(self):
        """
        Returns an empty manager as we use groups for permissions.
        """
        return FreeIPAManager([])

    @property
    def pk(self):
        return _session_user_id_for_username(self.username)

    @property
    def id(self):
        return _session_user_id_for_username(self.username)

    def get_username(self):
        return self.username

    @property
    def full_name(self) -> str:
        displayname = str(self.displayname or "").strip()
        if displayname:
            return displayname

        gecos = str(self.gecos or "").strip()
        if gecos:
            return gecos

        commonname = str(self.commonname or "").strip()
        if commonname:
            return commonname

        derived = f"{self.first_name or ''} {self.last_name or ''}".strip()
        return derived or self.username

    def get_full_name(self) -> str:
        return self.full_name

    def anonymize(self) -> None:
        """Redact private fields in-place if the user opted into privacy.

        This keeps only:
        - username
        - groups (memberof_group)
        - fasIsPrivate itself

        Email is redacted (CRITICAL-01 privacy fix).
        Agreements are computed separately from FreeIPA and are not stored on
        the user object.
        """

        if not self.fas_is_private:
            return

        self.email = ""
        self.first_name = ""
        self.last_name = ""
        self.displayname = ""
        self.gecos = ""
        self.commonname = ""

        self._user_data = {
            "uid": [self.username],
            "mail": [],
            "memberof_group": list(self.groups_list),
            "fasIsPrivate": ["TRUE"],
        }

    def get_short_name(self):
        return self.first_name or self.username

    def get_session_auth_hash(self):
        return salted_hmac('freeipa-user', self.username, secret=settings.SECRET_KEY).hexdigest()

    @classmethod
    def all(cls):
        """
        Returns a list of all users from FreeIPA.
        """

        def _fetch_users() -> list[dict[str, object]]:
            result = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.user_find(o_all=True, o_no_members=False, o_sizelimit=0, o_timelimit=0),
            )
            return result.get('result', [])

        try:
            from django.core.cache import cache

            users = cache.get_or_set(_users_list_cache_key(), _fetch_users) or []
            excluded = {str(u).strip().lower() for u in settings.FREEIPA_FILTERED_USERNAMES}
            out: list[FreeIPAUser] = []
            for user_data in users:
                if not isinstance(user_data, dict):
                    continue

                uid = user_data.get("uid")
                if isinstance(uid, list):
                    username = uid[0] if uid else ""
                else:
                    username = uid or ""
                username = str(username).strip()
                if not username:
                    continue

                if username.lower() in excluded:
                    continue

                out.append(cls(username, user_data))
            return out
        except Exception as e:
            logger.exception(f"Failed to list users: {e}")
            return []

    @classmethod
    def _fetch_full_user(cls, client: ClientMeta, username: str):
        """Return a single user's full attribute dict.

        Prefer user_show (returns full attribute set including custom schema
        like Fedora's FAS fields). Fallback to user_find if needed.
        """

        def _try(label: str, fn):
            try:
                return fn()
            except exceptions.Unauthorized:
                raise
            except TypeError:
                raise
            except exceptions.FreeIPAError as e:
                logger.debug("FreeIPA call failed label=%s username=%s error=%s", label, username, e)
                return None
            except Exception:
                logger.exception("FreeIPA call failed (unexpected) label=%s username=%s", label, username)
                return None

        res = _try("user_show(username)", lambda: client.user_show(username, o_all=True, o_no_members=False))
        if res and 'result' in res:
            return res['result']

        res = _try("user_find(o_uid=...)", lambda: client.user_find(o_uid=username, o_all=True, o_no_members=False))
        if res and res.get('count', 0) > 0:
            return res['result'][0]
        return None

    @classmethod
    def get(cls, username):
        """
        Fetch a single user by username.
        """
        from django.core.cache import cache

        cache_key = _user_cache_key(username)
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return cls(username, cached_data)

        try:
            user_data = _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: cls._fetch_full_user(client, username),
            )
            if user_data is not None:
                cache.set(cache_key, user_data)
                return cls(username, user_data)
        except Exception as e:
            logger.exception(f"Failed to get user username={username}: {e}")
            raise
        return None

    @classmethod
    def find_by_email(cls, email: str) -> FreeIPAUser | None:
        email = (email or "").strip().lower()
        if not email:
            return None

        def _do(client: ClientMeta):
            return client.user_find(o_mail=email, o_all=True, o_no_members=False)

        try:
            res = _with_freeipa_service_client_retry(cls.get_client, _do)
            if not isinstance(res, dict) or res.get("count", 0) <= 0:
                return None

            first = (res.get("result") or [None])[0]
            if not isinstance(first, dict):
                return None

            uid = first.get("uid")
            if isinstance(uid, list):
                username = (uid[0] if uid else "") or ""
            else:
                username = uid or ""
            username = str(username).strip()
            if not username:
                return None

            return cls(username, first)
        except Exception as e:
            logger.exception(f"Failed to find user by email email={email}: {e}")
            return None

    @classmethod
    def find_usernames_by_email(cls, email: str) -> list[str]:
        normalized = (email or "").strip().lower()
        if not normalized:
            return []

        def _do(client: ClientMeta):
            return client.user_find(o_mail=normalized, o_all=True, o_no_members=False)

        try:
            res = _with_freeipa_service_client_retry(cls.get_client, _do)
        except Exception:
            logger.exception("Failed to find users by email")
            return []

        if not isinstance(res, dict) or res.get("count", 0) <= 0:
            return []

        results = res.get("result")
        if not isinstance(results, list):
            return []

        usernames: set[str] = set()
        for item in results:
            if not isinstance(item, dict):
                continue
            uid = item.get("uid")
            if isinstance(uid, list):
                values = uid
            else:
                values = [uid]
            for value in values:
                name = str(value or "").strip().lower()
                if name:
                    usernames.add(name)

        return sorted(usernames)

    @classmethod
    def create(cls, username, **kwargs):
        """
        Create a new user in FreeIPA.
        kwargs should match FreeIPA user_add arguments (e.g., givenname, sn, mail, password).
        """
        try:
            givenname = kwargs.pop('givenname', None) or kwargs.pop('first_name', None)
            sn = kwargs.pop('sn', None) or kwargs.pop('last_name', None)
            if not givenname or not sn:
                raise ValueError('FreeIPA user creation requires givenname/first_name and sn/last_name')

            cn = f"{givenname or ''} {sn or ''}".strip() or username

            initials = f"{(str(givenname).strip()[:1] or '').upper()}{(str(sn).strip()[:1] or '').upper()}"

            ipa_kwargs = {}

            ipa_kwargs["o_displayname"] = cn
            ipa_kwargs["o_gecos"] = cn
            if initials:
                ipa_kwargs["o_initials"] = initials

            mail = kwargs.pop('mail', None) or kwargs.pop('email', None)
            if mail:
                ipa_kwargs['o_mail'] = mail

            password = kwargs.pop('password', None) or kwargs.pop('userpassword', None)
            if password:
                ipa_kwargs['o_userpassword'] = password

            for key, value in kwargs.items():
                if key.startswith(('o_', 'a_')):
                    ipa_kwargs[key] = value
                else:
                    ipa_kwargs[f"o_{key}"] = value

            _with_freeipa_service_client_retry(
                cls.get_client,
                lambda client: client.user_add(username, givenname, sn, cn, **ipa_kwargs),
            )
            _invalidate_users_list_cache()
            return cls.get(username)
        except Exception:
            logger.exception("Failed to create user username=%s", username)
            raise

    def save(self, *args, **kwargs):
        """Persist changes.

        - If called by Django's update_last_login signal (update_fields includes
          only 'last_login'), do nothing (we don't persist last_login).
        - Otherwise, update selected fields in FreeIPA.
        """

        update_fields = kwargs.get('update_fields')
        if update_fields is not None:
            update_fields_set = set(update_fields)
            if update_fields_set == {'last_login'}:
                return

        updates = {}
        if self.first_name:
            updates['o_givenname'] = self.first_name
        if self.last_name:
            updates['o_sn'] = self.last_name
        if self.email:
            updates['o_mail'] = self.email

        updates['o_nsaccountlock'] = (not bool(self.is_active))

        desired_name = f"{self.first_name or ''} {self.last_name or ''}".strip() or self.username
        updates["o_cn"] = desired_name
        updates["o_gecos"] = desired_name
        updates["o_displayname"] = desired_name

        initials = f"{(str(self.first_name).strip()[:1] or '').upper()}{(str(self.last_name).strip()[:1] or '').upper()}"
        if initials:
            updates["o_initials"] = initials

        try:
            if updates:
                try:
                    _with_freeipa_service_client_retry(
                        self.get_client,
                        lambda client: client.user_mod(self.username, **updates),
                    )
                except exceptions.BadRequest as e:
                    if "no modifications to be performed" not in str(e).lower():
                        raise
                    logger.info("FreeIPA user_mod was a no-op username=%s", self.username)

            _invalidate_user_cache(self.username)
            _invalidate_users_list_cache()
            FreeIPAUser.get(self.username)
        except Exception as e:
            logger.exception("Failed to update user username=%s: %s", self.username, e)
            raise

    def delete(self):
        """
        Delete the user from FreeIPA.
        """
        try:
            _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.user_del(self.username),
            )
            _invalidate_user_cache(self.username)
            _invalidate_users_list_cache()
        except Exception:
            logger.exception("Failed to delete user username=%s", self.username)
            raise

    def get_all_permissions(self, obj=None):
        if obj is not None:
            return set()
        return self.get_group_permissions(obj) | self.get_user_permissions(obj)

    def get_user_permissions(self, obj=None):
        if obj is not None:
            return set()

        try:
            from core.models import FreeIPAPermissionGrant
        except Exception:
            return set()

        username = str(self.username or "").strip().lower()
        if not username:
            return set()

        return set(
            FreeIPAPermissionGrant.objects.filter(
                principal_type=FreeIPAPermissionGrant.PrincipalType.user,
                principal_name=username,
            ).values_list("permission", flat=True)
        )

    def has_perm(self, perm, obj=None):
        if self.is_active and self.is_superuser:
            return True
        return perm in self.get_all_permissions(obj)

    def has_perms(self, perm_list, obj=None):
        return all(self.has_perm(perm, obj) for perm in perm_list)

    def has_module_perms(self, app_label):
        if self.is_active and self.is_superuser:
            return True
        return any(perm.startswith(f"{app_label}.") for perm in self.get_all_permissions())

    def get_group_permissions(self, obj=None):
        if obj is not None:
            return set()

        perms = set()
        group_permissions_map = settings.FREEIPA_GROUP_PERMISSIONS

        for group in self.groups_list:
            if group in group_permissions_map:
                perms.update(group_permissions_map[group])

        try:
            from core.models import FreeIPAPermissionGrant
        except Exception:
            return perms

        groups = [str(g or "").strip().lower() for g in self.groups_list if str(g or "").strip()]
        if not groups:
            return perms

        perms.update(
            FreeIPAPermissionGrant.objects.filter(
                principal_type=FreeIPAPermissionGrant.PrincipalType.group,
                principal_name__in=groups,
            ).values_list("permission", flat=True)
        )

        return perms

    def __str__(self):
        return self.username

    def __eq__(self, other):
        return isinstance(other, FreeIPAUser) and self.username == other.username

    def __hash__(self):
        return hash(self.username)

    def add_to_group(self, group_name):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_add_member(group_name, o_user=[self.username]),
            )
            _raise_if_freeipa_failed(res, action="group_add_member", subject=f"user={self.username} group={group_name}")
            _invalidate_user_cache(self.username)
            _invalidate_group_cache(group_name)
            _invalidate_groups_list_cache()
            fresh_user = FreeIPAUser.get(self.username)
            from core.freeipa.group import FreeIPAGroup

            FreeIPAGroup.get(group_name)
            if not fresh_user:
                raise FreeIPAOperationFailed(
                    f"FreeIPA group_add_member reported success but user could not be re-fetched (user={self.username} group={group_name})"
                )
            if group_name not in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_add_member reported success but membership not present after refresh "
                    f"(user={self.username} group={group_name} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to add user to group username=%s group=%s", self.username, group_name)
            raise

    def remove_from_group(self, group_name):
        try:
            res = _with_freeipa_service_client_retry(
                self.get_client,
                lambda client: client.group_remove_member(group_name, o_user=[self.username]),
            )
            _raise_if_freeipa_failed(res, action="group_remove_member", subject=f"user={self.username} group={group_name}")
            _invalidate_user_cache(self.username)
            _invalidate_group_cache(group_name)
            _invalidate_groups_list_cache()
            fresh_user = FreeIPAUser.get(self.username)
            from core.freeipa.group import FreeIPAGroup

            FreeIPAGroup.get(group_name)
            if not fresh_user:
                raise FreeIPAOperationFailed(
                    f"FreeIPA group_remove_member reported success but user could not be re-fetched (user={self.username} group={group_name})"
                )
            if group_name in fresh_user.groups_list:
                raise FreeIPAOperationFailed(
                    "FreeIPA group_remove_member reported success but membership still present after refresh "
                    f"(user={self.username} group={group_name} response={_compact_repr(res)})"
                )
        except Exception:
            logger.exception("Failed to remove user from group username=%s group=%s", self.username, group_name)
            raise


__all__ = [
    "_FreeIPAPK",
    "_FreeIPAMeta",
    "DegradedFreeIPAUser",
    "FreeIPAManager",
    "_FreeIPAClientMixin",
    "FreeIPAUser",
]
