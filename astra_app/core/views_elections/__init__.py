"""Election views package — split from the monolithic views_elections module.

All public view functions are re-exported here so that ``core.urls`` can
continue to reference ``views_elections.<view_name>`` unchanged.
"""

from core.views_elections.audit import (
    election_audit_log,
    election_public_audit,
    election_public_ballots,
)
from core.views_elections.ballot_verify import ballot_verify
from core.views_elections.detail import (
    election_algorithm,
    election_detail,
    elections_list,
)
from core.views_elections.edit import election_edit
from core.views_elections.lifecycle import (
    election_conclude,
    election_extend_end,
    election_send_mail_credentials,
)
from core.views_elections.search import (
    election_eligible_users_search,
    election_email_render_preview,
    election_nomination_users_search,
)
from core.views_elections.vote import election_vote, election_vote_submit

__all__ = [
    "ballot_verify",
    "election_algorithm",
    "election_audit_log",
    "election_conclude",
    "election_detail",
    "election_edit",
    "election_eligible_users_search",
    "election_email_render_preview",
    "election_extend_end",
    "election_nomination_users_search",
    "election_public_audit",
    "election_public_ballots",
    "election_send_mail_credentials",
    "election_vote",
    "election_vote_submit",
    "elections_list",
]
