#!/usr/bin/env bash
# The deploy step's body. It REFUSES to run until the owner has chosen a host: docs/HOSTING_PLAN.md lists what is open. Replace this stub, in review, once there is a host; until then the workflow job
# that calls it is also switched off (`vars.DEPLOY_ENABLED` unset) and sits behind the `production` environment's manual approval.
# It reads DEPLOY_HOST, DEPLOY_USER and DEPLOY_SSH_KEY from the environment and never prints them.
set -euo pipefail
for v in DEPLOY_HOST DEPLOY_USER DEPLOY_SSH_KEY; do
  [ -n "${!v:-}" ] || { echo "$v is not set (a repository secret, set by the owner)" >&2; exit 1; }
done
echo "refusing to deploy: no host has been chosen. See docs/HOSTING_PLAN.md (the owner's decision), then write the deploy here and call tools/ci/post_deploy_smoke.sh after it." >&2
exit 1
