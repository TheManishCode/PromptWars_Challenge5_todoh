"""Typed data contracts for to;doh-the event planner.

``core`` holds the planning contracts (request, plan, budget, vendors, schedule, ledgers); ``integrations`` holds the contracts for travel, weather, deals,
places, outreach, ideas and digests. Everything is re-exported here, so ``from eventmate.schemas import X`` (including the private ``_Model`` and ``_EMAIL``) is unchanged.
"""

from eventmate.schemas import core as _core
from eventmate.schemas import integrations as _integrations

globals().update({name: value for module in (_core, _integrations) for name, value in vars(module).items() if not name.startswith("__")})
