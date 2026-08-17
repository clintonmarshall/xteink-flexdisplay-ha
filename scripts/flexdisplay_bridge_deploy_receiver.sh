#!/usr/bin/env bash
# Restricted SSH forced-command receiver for a single FlexDisplay Bridge App.
# Install this exact reviewed file separately on DumbHA and bind its dedicated
# public key with authorized_keys command= plus no forwarding and no PTY.

set -Eeuo pipefail
umask 077

readonly APP_SLUG="629898c9_flexdisplay_bridge"
readonly APP_REPOSITORY="629898c9"
readonly APP_SOURCE_URL="https://github.com/clintonmarshall/xteink-flexdisplay-ha"
readonly CONTROL_DIR="/config/.flexdisplay-deploy-control"
readonly LOCK_FILE="$CONTROL_DIR/bridge-deploy.lock"
readonly HA_CLI="/usr/bin/ha"
readonly JQ="/usr/bin/jq"
readonly SHA256SUM="/usr/bin/sha256sum"
readonly FLOCK="/usr/bin/flock"

backup_slug=""
temporary_directory=""

cleanup() {
  local exit_code=$?
  set +e
  if [[ $exit_code -ne 0 && -n "$backup_slug" ]]; then
    "$JQ" -cn \
      --arg backup_slug "$backup_slug" \
      '{deployment_failed:true, rollback_backup:$backup_slug}' >&2
  fi
  if [[ -n "$temporary_directory" && -d "$temporary_directory" ]]; then
    find "$temporary_directory" -depth -type f -delete 2>/dev/null || true
    find "$temporary_directory" -depth -type d -exec rmdir {} \; 2>/dev/null || true
  fi
  exit "$exit_code"
}
trap cleanup EXIT

test -x "$HA_CLI"
test -x "$JQ"
test -x "$SHA256SUM"
test -x "$FLOCK"
test -d "$CONTROL_DIR"

exec 9>"$LOCK_FILE"
"$FLOCK" -n 9 || {
  echo "Another FlexDisplay Bridge operation is active" >&2
  exit 1
}

temporary_directory="$(mktemp -d /tmp/flexdisplay-bridge-deploy.XXXXXX)"
readonly SELF_SHA256="$($SHA256SUM "$0" | awk '{print $1}')"
[[ "$SELF_SHA256" =~ ^[0-9a-f]{64}$ ]]

app_info() {
  local destination=$1
  "$HA_CLI" apps info "$APP_SLUG" --no-progress --raw-json > "$destination"
  "$JQ" -e \
    --arg slug "$APP_SLUG" \
    --arg repository "$APP_REPOSITORY" \
    --arg source_url "$APP_SOURCE_URL" \
    '.result == "ok" and
     .data.slug == $slug and
     (.data.repository | tostring) == $repository and
     .data.url == $source_url and
     (.data.version | type) == "string" and
     (.data.version_latest | type) == "string" and
     (.data.auto_update | type) == "boolean" and
     (.data.state | type) == "string"' \
    "$destination" > /dev/null
}

core_version() {
  local destination=$1
  "$HA_CLI" core info --no-progress --raw-json > "$destination"
  "$JQ" -er '.data.version | select(type == "string" and length > 0)' \
    "$destination"
}

status_json() {
  local app_file="$temporary_directory/app.json"
  local core_file="$temporary_directory/core.json"
  local installed_core
  app_info "$app_file"
  installed_core="$(core_version "$core_file")"
  "$JQ" -c \
    --arg receiver_sha256 "$SELF_SHA256" \
    --arg core_version "$installed_core" \
    '{receiver_sha256:$receiver_sha256,
      core_version:$core_version,
      app:{slug:.data.slug,
           repository:(.data.repository | tostring),
           source_url:.data.url,
           version:.data.version,
           version_latest:.data.version_latest,
           state:.data.state,
           auto_update:.data.auto_update,
           update_available:.data.update_available}}' \
    "$app_file"
}

deploy_bridge() {
  local target_version=$1
  local expected_version=$2
  local expected_receiver_sha=$3
  local before_file="$temporary_directory/before.json"
  local refreshed_file="$temporary_directory/refreshed.json"
  local core_check_file="$temporary_directory/core-check.json"
  local store_reload_file="$temporary_directory/store-reload.json"
  local backup_file="$temporary_directory/backup.json"
  local backup_info_file="$temporary_directory/backup-info.json"
  local update_file="$temporary_directory/update.json"
  local after_file="$temporary_directory/after.json"
  local installed_version=""
  local latest_version=""
  local update_attempt=0

  [[ "$target_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
  [[ "$expected_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
  [[ "$expected_receiver_sha" =~ ^[0-9a-f]{64}$ ]]
  test "$expected_receiver_sha" = "$SELF_SHA256"
  test "$target_version" != "$expected_version"

  app_info "$before_file"
  test "$("$JQ" -r '.data.version' "$before_file")" = "$expected_version"
  test "$("$JQ" -r '.data.state' "$before_file")" = "started"
  test "$("$JQ" -r '.data.auto_update' "$before_file")" = "false"

  "$HA_CLI" core check --no-progress --raw-json > "$core_check_file"
  "$JQ" -e '.result == "ok"' "$core_check_file" > /dev/null

  "$HA_CLI" store reload --no-progress --raw-json > "$store_reload_file"
  "$JQ" -e '.result == "ok"' "$store_reload_file" > /dev/null

  app_info "$refreshed_file"
  installed_version="$("$JQ" -r '.data.version' "$refreshed_file")"
  latest_version="$("$JQ" -r '.data.version_latest' "$refreshed_file")"
  test "$installed_version" = "$expected_version"
  test "$latest_version" = "$target_version"
  test "$("$JQ" -r '.data.auto_update' "$refreshed_file")" = "false"

  "$HA_CLI" backups new \
    --app "$APP_SLUG" \
    --name "FlexDisplay Bridge $expected_version pre-$target_version" \
    --no-progress --raw-json > "$backup_file"
  backup_slug="$("$JQ" -er '.data.slug | select(type == "string" and length > 0)' "$backup_file")"
  [[ "$backup_slug" =~ ^[A-Za-z0-9_-]+$ ]]

  "$HA_CLI" backups info "$backup_slug" --no-progress --raw-json > "$backup_info_file"
  "$JQ" -e \
    --arg slug "$APP_SLUG" \
    --arg version "$expected_version" \
    '.result == "ok" and
     ([.data.addons[]? |
       select(.slug == $slug and .version == $version)] | length) == 1' \
    "$backup_info_file" > /dev/null

  "$JQ" -cn \
    --arg backup_slug "$backup_slug" \
    --arg rollback_version "$expected_version" \
    --arg target_version "$target_version" \
    '{backup_verified:true,
      rollback_backup:$backup_slug,
      rollback_version:$rollback_version,
      target_version:$target_version}' >&2

  "$HA_CLI" apps update "$APP_SLUG" --no-progress --raw-json > "$update_file"
  "$JQ" -e '.result == "ok"' "$update_file" > /dev/null

  while (( update_attempt < 36 )); do
    update_attempt=$((update_attempt + 1))
    if app_info "$after_file" &&
       [[ "$("$JQ" -r '.data.version' "$after_file")" == "$target_version" ]] &&
       [[ "$("$JQ" -r '.data.state' "$after_file")" == "started" ]]; then
      break
    fi
    sleep 5
  done

  app_info "$after_file"
  test "$("$JQ" -r '.data.version' "$after_file")" = "$target_version"
  test "$("$JQ" -r '.data.state' "$after_file")" = "started"
  test "$("$JQ" -r '.data.auto_update' "$after_file")" = "false"

  "$JQ" -cn \
    --arg receiver_sha256 "$SELF_SHA256" \
    --arg backup_slug "$backup_slug" \
    --arg previous_version "$expected_version" \
    --arg installed_version "$target_version" \
    '{receiver_sha256:$receiver_sha256,
      backup_verified:true,
      rollback_backup:$backup_slug,
      previous_version:$previous_version,
      installed_version:$installed_version,
      auto_update:false,
      core_restart_performed:false}'
}

readonly ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND:-}"
case "$ORIGINAL_COMMAND" in
  status)
    status_json
    ;;
  deploy\ *)
    if [[ ! "$ORIGINAL_COMMAND" =~ ^deploy\ ([0-9]+\.[0-9]+\.[0-9]+)\ ([0-9]+\.[0-9]+\.[0-9]+)\ ([0-9a-f]{64})$ ]]; then
      echo "Refusing invalid deploy request" >&2
      exit 1
    fi
    deploy_bridge "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}"
    ;;
  *)
    echo "This key permits only FlexDisplay Bridge status or deployment" >&2
    exit 1
    ;;
esac
