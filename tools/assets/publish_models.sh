#!/usr/bin/env bash
# Upload the room's full-quality models to a Cloudflare R2 bucket under their content-addressed names (eventmate/web/cdn_models.json).
# usage: publish_models.sh STAGING_DIR BUCKET   (STAGING_DIR holds <name>.<sha10>.glb; needs `wrangler login`)
# The bucket needs public access (the r2.dev URL or a custom domain) and a CORS rule allowing GET from the site's origin. Then set EVENTMATE_ASSET_BASE to its public URL.
# Names embed the file hash, so they are cached for a year and a changed model is simply a new name: old objects can be deleted once no deployment names them.
set -euo pipefail
DIR=${1:?usage: publish_models.sh STAGING_DIR BUCKET}; BUCKET=${2:?}; WR=${WRANGLER:-wrangler}
for f in "$DIR"/*.glb; do
  $WR r2 object put "$BUCKET/$(basename "$f")" --file "$f" --content-type model/gltf-binary --cache-control "public, max-age=31536000, immutable" --remote
done
