#!/usr/bin/env bash
# Restricted SSH forced-command receiver for FlexDisplay on one DumbHA target.
# Install this exact reviewed file separately on DumbHA and bind its dedicated
# public key with authorized_keys command= plus no forwarding and no PTY.

set -Eeuo pipefail
umask 077

readonly APP_SLUG="629898c9_flexdisplay_bridge"
readonly APP_REPOSITORY="629898c9"
readonly APP_SOURCE_URL="https://github.com/clintonmarshall/xteink-flexdisplay-ha"
readonly CONTROL_DIR="/config/.flexdisplay-deploy-control"
readonly LOCK_FILE="$CONTROL_DIR/bridge-deploy.lock"
readonly INTEGRATION_DIR="/config/custom_components/flexdisplay"
readonly INTEGRATION_ROLLBACK_ROOT="$CONTROL_DIR/integration-rollbacks"
readonly INTEGRATION_STAGE_RECORD="$CONTROL_DIR/integration-stage.json"
readonly HA_CLI="/usr/bin/ha"
readonly JQ="/usr/bin/jq"
readonly SHA256SUM="/usr/bin/sha256sum"
readonly FLOCK="/usr/bin/flock"
readonly TAR="/bin/tar"

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
test -x "$TAR"
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

integration_version() {
  local manifest=$1
  test -f "$manifest"
  "$JQ" -er \
    'select(.domain == "flexdisplay") | .version |
     select(type == "string" and test("^[0-9]+\\.[0-9]+\\.[0-9]+$"))' \
    "$manifest"
}

status_json() {
  local app_file="$temporary_directory/app.json"
  local core_file="$temporary_directory/core.json"
  local installed_core
  local installed_integration
  local integration_stage="null"
  app_info "$app_file"
  installed_core="$(core_version "$core_file")"
  installed_integration="$(integration_version "$INTEGRATION_DIR/manifest.json")"
  if [[ -f "$INTEGRATION_STAGE_RECORD" ]]; then
    integration_stage="$("$JQ" -ce \
      'select((.target_version | type) == "string") |
       select((.receiver_sha256 | type) == "string") |
       select((.core_restart_performed | type) == "boolean") |
       select(.core_restart_state == "not_started" or
              .core_restart_state == "requested" or
              .core_restart_state == "verified") |
       {target_version, receiver_sha256,
        core_restart_performed, core_restart_state}' \
      "$INTEGRATION_STAGE_RECORD")"
  fi
  "$JQ" -c \
    --arg receiver_sha256 "$SELF_SHA256" \
    --arg core_version "$installed_core" \
    --arg integration_version "$installed_integration" \
    --argjson integration_stage "$integration_stage" \
    '{receiver_sha256:$receiver_sha256,
      core_version:$core_version,
      integration:{version:$integration_version,stage:$integration_stage},
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

remove_tree() {
  local path=$1
  if [[ -d "$path" ]]; then
    find "$path" -depth -type f -delete
    find "$path" -depth -type l -delete
    find "$path" -depth -type d -exec rmdir {} \;
  fi
}

validate_integration_archive() {
  local archive=$1 listing=$2 verbose_listing=$3
  "$TAR" -tzf "$archive" > "$listing"
  test -s "$listing"
  while IFS= read -r path; do
    case "$path" in
      custom_components/flexdisplay|custom_components/flexdisplay/|custom_components/flexdisplay/*) ;;
      *) echo "Integration bundle contains an unexpected path" >&2; return 1 ;;
    esac
    case "/$path/" in */../*|*/./*) echo "Integration bundle contains an unsafe path" >&2; return 1 ;; esac
  done < "$listing"
  "$TAR" -tvzf "$archive" > "$verbose_listing"
  if grep -Ev '^[d-]' "$verbose_listing" | grep -q .; then
    echo "Integration bundle contains a non-regular entry" >&2
    return 1
  fi
}

stage_integration() {
  local target_version=$1 expected_version=$2 archive_sha=$3 expected_receiver_sha=$4
  local archive="$temporary_directory/integration.tar.gz"
  local listing="$temporary_directory/integration.list"
  local verbose_listing="$temporary_directory/integration.verbose"
  local extracted="$temporary_directory/extracted"
  local staged="$CONTROL_DIR/integration-staged.$$"
  local displaced="$CONTROL_DIR/integration-displaced.$$"
  local core_check_file="$temporary_directory/core-check.json"
  local post_check_file="$temporary_directory/post-check.json"
  local backup_file="$temporary_directory/backup.json"
  local backup_info_file="$temporary_directory/backup-info.json"
  local rollback_dir="$INTEGRATION_ROLLBACK_ROOT/$(date -u +%Y%m%dT%H%M%SZ)-$expected_version"
  local installed_version new_version archive_actual

  [[ "$target_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
  [[ "$expected_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
  [[ "$archive_sha" =~ ^[0-9a-f]{64}$ ]]
  [[ "$expected_receiver_sha" =~ ^[0-9a-f]{64}$ ]]
  test "$target_version" != "$expected_version"
  test "$expected_receiver_sha" = "$SELF_SHA256"
  installed_version="$(integration_version "$INTEGRATION_DIR/manifest.json")"
  test "$installed_version" = "$expected_version"
  if [[ -f "$INTEGRATION_STAGE_RECORD" ]]; then
    "$JQ" -e '.core_restart_performed == true and
              .core_restart_state == "verified"' \
      "$INTEGRATION_STAGE_RECORD" > /dev/null
  fi

  "$HA_CLI" core check --no-progress --raw-json > "$core_check_file"
  "$JQ" -e '.result == "ok"' "$core_check_file" > /dev/null

  /bin/cat > "$archive"
  archive_actual="$($SHA256SUM "$archive" | awk '{print $1}')"
  test "$archive_actual" = "$archive_sha"
  validate_integration_archive "$archive" "$listing" "$verbose_listing"
  mkdir -p "$extracted"
  "$TAR" -xzf "$archive" -C "$extracted" --no-same-owner --no-same-permissions
  new_version="$(integration_version "$extracted/custom_components/flexdisplay/manifest.json")"
  test "$new_version" = "$target_version"

  mkdir -p "$INTEGRATION_ROLLBACK_ROOT"
  chmod 700 "$INTEGRATION_ROLLBACK_ROOT"
  test ! -e "$rollback_dir"
  mkdir "$rollback_dir"
  chmod 700 "$rollback_dir"
  cp -a "$INTEGRATION_DIR" "$rollback_dir/flexdisplay"
  test "$(integration_version "$rollback_dir/flexdisplay/manifest.json")" = "$expected_version"

  "$HA_CLI" backups new \
    --folders homeassistant \
    --homeassistant-exclude-database \
    --name "FlexDisplay integration $expected_version pre-$target_version" \
    --no-progress --raw-json > "$backup_file"
  backup_slug="$("$JQ" -er '.data.slug | select(type == "string" and length > 0)' "$backup_file")"
  [[ "$backup_slug" =~ ^[A-Za-z0-9_-]+$ ]]
  "$HA_CLI" backups info "$backup_slug" --no-progress --raw-json > "$backup_info_file"
  "$JQ" -e '.result == "ok" and (.data.folders | index("homeassistant") != null)' \
    "$backup_info_file" > /dev/null

  test ! -e "$staged"
  test ! -e "$displaced"
  mv "$extracted/custom_components/flexdisplay" "$staged"
  chown -R root:root "$staged"
  mv "$INTEGRATION_DIR" "$displaced"
  if ! mv "$staged" "$INTEGRATION_DIR"; then
    mv "$displaced" "$INTEGRATION_DIR"
    return 1
  fi

  if ! "$HA_CLI" core check --no-progress --raw-json > "$post_check_file" ||
     ! "$JQ" -e '.result == "ok"' "$post_check_file" > /dev/null; then
    remove_tree "$INTEGRATION_DIR"
    mv "$displaced" "$INTEGRATION_DIR"
    "$HA_CLI" core check --no-progress --raw-json > "$post_check_file"
    "$JQ" -e '.result == "ok"' "$post_check_file" > /dev/null
    echo "Staged integration failed validation and the previous files were restored" >&2
    return 1
  fi
  remove_tree "$displaced"

  "$JQ" -cn \
    --arg target_version "$target_version" \
    --arg previous_version "$expected_version" \
    --arg source_archive_sha256 "$archive_sha" \
    --arg rollback_directory "$rollback_dir" \
    --arg rollback_backup "$backup_slug" \
    --arg receiver_sha256 "$SELF_SHA256" \
    '{target_version:$target_version,
      previous_version:$previous_version,
      source_archive_sha256:$source_archive_sha256,
      rollback_directory:$rollback_directory,
      rollback_backup:$rollback_backup,
      receiver_sha256:$receiver_sha256,
      core_restart_performed:false,
      core_restart_state:"not_started"}' > "$INTEGRATION_STAGE_RECORD.tmp"
  chmod 600 "$INTEGRATION_STAGE_RECORD.tmp"
  mv "$INTEGRATION_STAGE_RECORD.tmp" "$INTEGRATION_STAGE_RECORD"
  "$JQ" -c . "$INTEGRATION_STAGE_RECORD"
}

restart_core_for_integration() {
  local target_version=$1 expected_receiver_sha=$2
  local before_file="$temporary_directory/core-before.json"
  local restart_file="$temporary_directory/core-restart.json"
  local after_file="$temporary_directory/core-after.json"
  local check_file="$temporary_directory/core-check.json"
  local attempt=0 installed_core

  [[ "$target_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]
  [[ "$expected_receiver_sha" =~ ^[0-9a-f]{64}$ ]]
  test "$expected_receiver_sha" = "$SELF_SHA256"
  test "$(integration_version "$INTEGRATION_DIR/manifest.json")" = "$target_version"
  test -f "$INTEGRATION_STAGE_RECORD"
  "$JQ" -e --arg target "$target_version" --arg receiver "$SELF_SHA256" \
    '.target_version == $target and .receiver_sha256 == $receiver and
     .core_restart_performed == false and
     .core_restart_state == "not_started"' "$INTEGRATION_STAGE_RECORD" > /dev/null

  "$HA_CLI" core check --no-progress --raw-json > "$check_file"
  "$JQ" -e '.result == "ok"' "$check_file" > /dev/null
  installed_core="$(core_version "$before_file")"
  "$JQ" --arg requested_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '.core_restart_state = "requested" |
     .core_restart_requested_at = $requested_at' \
    "$INTEGRATION_STAGE_RECORD" > "$INTEGRATION_STAGE_RECORD.tmp"
  chmod 600 "$INTEGRATION_STAGE_RECORD.tmp"
  mv "$INTEGRATION_STAGE_RECORD.tmp" "$INTEGRATION_STAGE_RECORD"
  "$HA_CLI" core restart --no-progress --raw-json > "$restart_file"
  "$JQ" -e '.result == "ok"' "$restart_file" > /dev/null

  while (( attempt < 60 )); do
    attempt=$((attempt + 1))
    if "$HA_CLI" core info --no-progress --raw-json > "$after_file" 2>/dev/null &&
       "$JQ" -e --arg version "$installed_core" \
         '.result == "ok" and .data.version == $version and .data.state == "started"' \
         "$after_file" > /dev/null; then
      break
    fi
    sleep 5
  done
  "$JQ" -e --arg version "$installed_core" \
    '.result == "ok" and .data.version == $version and .data.state == "started"' \
    "$after_file" > /dev/null
  "$HA_CLI" core check --no-progress --raw-json > "$check_file"
  "$JQ" -e '.result == "ok"' "$check_file" > /dev/null

  "$JQ" --arg core_version "$installed_core" \
    '.core_restart_performed = true |
     .core_restart_state = "verified" |
     .home_assistant_core = $core_version' \
    "$INTEGRATION_STAGE_RECORD" > "$INTEGRATION_STAGE_RECORD.tmp"
  chmod 600 "$INTEGRATION_STAGE_RECORD.tmp"
  mv "$INTEGRATION_STAGE_RECORD.tmp" "$INTEGRATION_STAGE_RECORD"
  "$JQ" -c . "$INTEGRATION_STAGE_RECORD"
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
  stage-integration\ *)
    if [[ ! "$ORIGINAL_COMMAND" =~ ^stage-integration\ ([0-9]+\.[0-9]+\.[0-9]+)\ ([0-9]+\.[0-9]+\.[0-9]+)\ ([0-9a-f]{64})\ ([0-9a-f]{64})$ ]]; then
      echo "Refusing invalid integration staging request" >&2
      exit 1
    fi
    stage_integration "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}" "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
    ;;
  restart-core\ *)
    if [[ ! "$ORIGINAL_COMMAND" =~ ^restart-core\ ([0-9]+\.[0-9]+\.[0-9]+)\ ([0-9a-f]{64})$ ]]; then
      echo "Refusing invalid Core restart request" >&2
      exit 1
    fi
    restart_core_for_integration "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    ;;
  *)
    echo "This key permits only reviewed FlexDisplay status, deployment, staging, or Core restart operations" >&2
    exit 1
    ;;
esac
