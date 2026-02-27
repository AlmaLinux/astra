import logging

import requests
from django.contrib.auth.backends import BaseBackend
from python_freeipa import exceptions

from core.freeipa.client import _get_freeipa_client
from core.freeipa.user import FreeIPAUser

logger = logging.getLogger("core.backends")


class FreeIPAAuthBackend(BaseBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None

        logger.debug("authenticate: username=%s", username)

        try:
            client = _get_freeipa_client(username, password)

            user_data = FreeIPAUser._fetch_full_user(client, username)
            if user_data:
                logger.debug("authenticate: success username=%s", username)
                user = FreeIPAUser(username, user_data)
                if request is not None and hasattr(request, 'session'):
                    request.session['_freeipa_username'] = username
                return user
            return None
        except exceptions.PasswordExpired:
            logger.debug("authenticate: password expired username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_password_expired", True)
                try:
                    request.session["_freeipa_pwexp_username"] = username
                except Exception:
                    pass
            return None
        except exceptions.UserLocked:
            logger.debug("authenticate: user locked username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Your account is locked. Please contact support.")
            return None
        except exceptions.KrbPrincipalExpired:
            logger.debug("authenticate: principal expired username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Your account credentials have expired. Please contact support.")
            return None
        except exceptions.InvalidSessionPassword:
            logger.debug("authenticate: invalid session password username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Invalid username or password.")
            return None
        except exceptions.Denied:
            logger.debug("authenticate: denied username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login denied.")
            return None
        except exceptions.Unauthorized:
            logger.debug("authenticate: unauthorized username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Invalid username or password.")
            return None
        except exceptions.BadRequest as e:
            logger.warning("authenticate: bad request username=%s error=%s", username, e)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login failed due to a FreeIPA error.")
            return None
        except requests.exceptions.ConnectionError:
            logger.warning("authenticate: connection error username=%s", username)
            if request is not None:
                setattr(
                    request,
                    "_freeipa_auth_error",
                    "We cannot sign you in right now because AlmaLinux Accounts is temporarily unavailable. "
                    "Please try again in a few minutes.",
                )
            return None
        except Exception:
            logger.exception("FreeIPA authentication error username=%s", username)
            if request is not None:
                setattr(request, "_freeipa_auth_error", "Login failed due to an internal error.")
            return None

    def get_user(self, user_id):
        return None


__all__ = ["FreeIPAAuthBackend"]
