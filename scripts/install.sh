#!/bin/sh
set -eu

REPO_URL="${POLICYNIM_REPOSITORY_URL:-https://github.com/nnennandukwe/policyNIM}"
VERSION="${POLICYNIM_VERSION:-${1:-latest}}"
CHECKSUMS_FILE="SHA256SUMS"

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1. Install it and retry."
}

normalize_os() {
  raw_os="${POLICYNIM_INSTALLER_TEST_OS:-$(uname -s)}"
  case "$raw_os" in
    Darwin | darwin) printf 'darwin' ;;
    Linux | linux) printf 'linux' ;;
    *) printf '%s' "$raw_os" | tr '[:upper:]' '[:lower:]' ;;
  esac
}

normalize_arch() {
  raw_arch="${POLICYNIM_INSTALLER_TEST_ARCH:-$(uname -m)}"
  case "$raw_arch" in
    arm64 | aarch64) printf 'arm64' ;;
    x86_64 | amd64) printf 'amd64' ;;
    *) printf '%s' "$raw_arch" | tr '[:upper:]' '[:lower:]' ;;
  esac
}

supported_platform() {
  case "$1" in
    darwin-arm64 | darwin-amd64 | linux-amd64) return 0 ;;
    *) return 1 ;;
  esac
}

resolve_latest_version() {
  need_command curl
  latest_url="$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO_URL/releases/latest" || true)"
  latest_tag="${latest_url##*/}"
  case "$latest_tag" in
    v[0-9]*)
      printf '%s' "${latest_tag#v}"
      ;;
    *)
      fail "Could not resolve the latest PolicyNIM release version. Pass a version as POLICYNIM_VERSION or as the first argument."
      ;;
  esac
}

download_asset() {
  source_url="$1"
  destination="$2"
  label="$3"
  if ! curl -fsSL --retry 2 --retry-delay 1 -o "$destination" "$source_url"; then
    fail "Could not download release asset $label from $source_url. Check the release page or retry the install."
  fi
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    fail "Missing required command: sha256sum or shasum. Install one and retry."
  fi
}

extract_bundle() {
  archive_path="$1"
  destination="$2"
  if ! tar -xzf "$archive_path" -C "$destination"; then
    fail "Could not extract PolicyNIM bundle. Delete the downloaded asset and retry the install."
  fi
}

replace_install_dir() {
  staging_dir="$1"
  install_dir="$2"
  install_parent="$(dirname "$install_dir")"
  backup_dir="$install_parent/.${VERSION}.backup.$$"

  rm -rf "$backup_dir"
  if [ -d "$install_dir" ]; then
    mv "$install_dir" "$backup_dir"
  fi

  if mv "$staging_dir" "$install_dir"; then
    rm -rf "$backup_dir"
    return 0
  fi

  if [ -d "$backup_dir" ]; then
    mv "$backup_dir" "$install_dir"
  fi
  fail "Could not replace install directory $install_dir. Existing install was restored."
}

write_launcher() {
  install_dir="$1"
  launcher_path="$2"
  launcher_tmp="${launcher_path}.tmp.$$"

  mkdir -p "$(dirname "$launcher_path")"
  cat >"$launcher_tmp" <<EOF
#!/bin/sh
exec "$install_dir/policynim" "\$@"
EOF
  chmod 755 "$launcher_tmp"
  mv "$launcher_tmp" "$launcher_path"
}

print_path_guidance() {
  launcher_dir="$1"
  printf 'Installed PolicyNIM %s to %s.\n' "$VERSION" "$INSTALL_DIR"
  printf 'Launcher: %s\n' "$launcher_dir/policynim"
  case ":$PATH:" in
    *":$launcher_dir:"*) ;;
    *)
      printf 'Add PolicyNIM to PATH for this shell:\n'
      printf '  export PATH="$HOME/.local/bin:$PATH"\n'
      ;;
  esac
  printf 'Run `policynim init` to configure your local NVIDIA API key.\n'
}

OS_NAME="$(normalize_os)"
ARCH_NAME="$(normalize_arch)"
PLATFORM="${OS_NAME}-${ARCH_NAME}"
if ! supported_platform "$PLATFORM"; then
  fail "Unsupported platform: $PLATFORM. Supported platforms: darwin-arm64, darwin-amd64, linux-amd64."
fi

need_command curl
need_command tar
need_command awk

if [ "$VERSION" = "latest" ]; then
  VERSION="$(resolve_latest_version)"
fi
VERSION="${VERSION#v}"
TAG="v${VERSION}"
ASSET_NAME="policynim-${TAG}-${PLATFORM}"
RELEASE_BASE_URL="${POLICYNIM_RELEASE_BASE_URL:-$REPO_URL/releases/download/$TAG}"
RELEASE_PAGE_URL="$REPO_URL/releases/tag/$TAG"
INSTALL_DIR="$HOME/.local/share/policynim/$VERSION"
INSTALL_PARENT="$(dirname "$INSTALL_DIR")"
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER_PATH="$LAUNCHER_DIR/policynim"

WORK_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT INT TERM

asset_path="$WORK_DIR/$ASSET_NAME"
checksums_path="$WORK_DIR/$CHECKSUMS_FILE"
extract_dir="$WORK_DIR/extract"
mkdir -p "$extract_dir"

download_asset "$RELEASE_BASE_URL/$ASSET_NAME" "$asset_path" "$ASSET_NAME"
download_asset "$RELEASE_BASE_URL/$CHECKSUMS_FILE" "$checksums_path" "$CHECKSUMS_FILE"

expected_checksum="$(awk -v asset="$ASSET_NAME" '$2 == asset {print $1}' "$checksums_path" | head -n 1)"
if [ -z "$expected_checksum" ]; then
  fail "Checksum entry for $ASSET_NAME was not found in $CHECKSUMS_FILE. Check $RELEASE_PAGE_URL and retry."
fi

actual_checksum="$(sha256_file "$asset_path")"
if [ "$actual_checksum" != "$expected_checksum" ]; then
  fail "Checksum mismatch for $ASSET_NAME. Check $RELEASE_PAGE_URL and retry the install."
fi

extract_bundle "$asset_path" "$extract_dir"
bundle_binary="$(find "$extract_dir" -type f -name policynim -perm -111 | head -n 1)"
if [ -z "$bundle_binary" ]; then
  fail "Extracted asset $ASSET_NAME did not contain an executable policynim binary. Check $RELEASE_PAGE_URL and retry."
fi

bundle_root="$(dirname "$bundle_binary")"
staging_dir="$INSTALL_PARENT/.${VERSION}.staging.$$"
rm -rf "$staging_dir"
mkdir -p "$staging_dir"
cp -R "$bundle_root/." "$staging_dir/"
chmod 755 "$staging_dir/policynim"

mkdir -p "$INSTALL_PARENT"
replace_install_dir "$staging_dir" "$INSTALL_DIR"
write_launcher "$INSTALL_DIR" "$LAUNCHER_PATH"
print_path_guidance "$LAUNCHER_DIR"
