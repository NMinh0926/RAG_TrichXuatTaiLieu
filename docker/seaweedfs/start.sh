#!/bin/sh
# Single-node SeaweedFS with an S3 identity taken from the environment and the bucket pre-created.
set -eu
: "${S3_ACCESS_KEY:?S3_ACCESS_KEY is required}"
: "${S3_SECRET_KEY:?S3_SECRET_KEY is required}"
: "${S3_BUCKET:?S3_BUCKET is required}"
json_escape() { printf '%s' "$1" | sed -e 's/[\\]/&&/g' -e 's/"/\\"/g'; }
access_key=$(json_escape "$S3_ACCESS_KEY")
secret_key=$(json_escape "$S3_SECRET_KEY")
umask 077
cat > /tmp/s3.json <<JSON
{"identities": [{"name": "app",
  "credentials": [{"accessKey": "$access_key", "secretKey": "$secret_key"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON
exec weed mini -dir=/data -s3.config=/tmp/s3.json -bucket="$S3_BUCKET" -admin.ui=false
