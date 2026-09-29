#!/bin/sh
# Render a Redis ACL file (design DS-23, P4-10) with two users:
#   app    – backend API/runner: full access to application keys (no admin/dangerous commands)
#   worker – delivery workers: ONLY the shared provider token buckets (rl:provider:*) and exactly the
#            commands the bucket's Lua script runs. It cannot read quotas, sessions, stats or login limits.
# The default user is disabled. Passwords are stored as SHA-256 hashes (never plaintext).
#
#   REDIS_APP_PASSWORD=… REDIS_WORKER_PASSWORD=… ./render-acl.sh > users.acl
#   redis-server --aclfile /etc/redis/users.acl
# Then: backend REDIS_URL=redis://app:<pw>@redis:6379/0, workers REDIS_URL=redis://worker:<pw>@redis:6379/0
set -eu
: "${REDIS_APP_PASSWORD:?set REDIS_APP_PASSWORD}"
: "${REDIS_WORKER_PASSWORD:?set REDIS_WORKER_PASSWORD}"
hash() { printf '%s' "$1" | sha256sum | cut -d' ' -f1; }
cat <<ACL
user default off
user app on #$(hash "$REDIS_APP_PASSWORD") ~* &* +@all -@dangerous +info
user worker on #$(hash "$REDIS_WORKER_PASSWORD") ~rl:provider:* resetchannels -@all +ping +hello +evalsha +eval +script|load +script|exists +hmget +hset +pexpire
ACL
