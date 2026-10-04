#!/bin/bash
# Create once; subsequent builds reuse this certificate and private key.
# No key material belongs in git. See README.md, "Stable local signing".
set -euo pipefail
identity="${THUNDERTALK_SIGNING_NAME:-ThunderTalk Local Signing}"
signing_dir="$HOME/.thundertalk/signing"
keychain="$HOME/Library/Keychains/ThunderTalkLocalSigning.keychain-db"
password_file="$signing_dir/keychain-password"
umask 077
mkdir -p "$signing_dir" build
chmod 700 "$signing_dir"
if [[ ! -f "$password_file" ]]; then
    openssl rand -hex 32 > "$password_file"
fi
chmod 600 "$password_file"
keychain_password=$(cat "$password_file")
if [[ ! -f "$keychain" ]]; then
    security create-keychain -p "$keychain_password" "$keychain"
fi
security unlock-keychain -p "$keychain_password" "$keychain"
security set-keychain-settings -lut 21600 "$keychain"
# Preserve every existing search-list entry. codesign needs this keychain in
# the search list even when --keychain is supplied explicitly.
python3 - "$keychain" <<'PYTHON'
import subprocess
import sys
keys = [line.strip().strip('"') for line in subprocess.check_output(
    ["security", "list-keychains", "-d", "user"], text=True).splitlines()]
if sys.argv[1] not in keys:
    subprocess.run(["security", "list-keychains", "-d", "user", "-s", *keys, sys.argv[1]], check=True)
PYTHON
scratch=$(mktemp -d build/signing.XXXXXX)
trap 'rm -rf "$scratch"' EXIT
if security find-identity -p codesigning "$keychain" | /usr/bin/grep -Fq "\"$identity\""; then
    echo "Reusing signing identity: $identity"
    security find-certificate -c "$identity" -p "$keychain" > "$scratch/cert.pem"
else
    # Refuse to rotate a certificate whose private key has been lost.
    if security find-certificate -c "$identity" "$keychain" >/dev/null 2>&1; then
        echo "Existing certificate has no private key. Restore its keychain backup." >&2
        exit 1
    fi
    cat > "$scratch/certificate.conf" <<EOF
[req]
distinguished_name = subject
x509_extensions = extensions
prompt = no
[subject]
CN = $identity
[extensions]
basicConstraints = critical,CA:false
keyUsage = critical,digitalSignature
extendedKeyUsage = critical,codeSigning
subjectKeyIdentifier = hash
EOF
    openssl req -new -newkey rsa:3072 -nodes -x509 -sha256 -days 3650 \
        -config "$scratch/certificate.conf" -keyout "$scratch/key.pem" -out "$scratch/cert.pem"
    p12_password=$(openssl rand -hex 24)
    legacy=()
    if openssl pkcs12 -help 2>&1 | /usr/bin/grep -q -- '-legacy'; then
        legacy=(-legacy)
    fi
    # SHA-1 MAC and legacy encryption are required by macOS security import.
    openssl pkcs12 -export "${legacy[@]}" -macalg sha1 -inkey "$scratch/key.pem" -in "$scratch/cert.pem" \
        -name "$identity" -passout "pass:$p12_password" -out "$scratch/identity.p12"
    security import "$scratch/identity.p12" -k "$keychain" -P "$p12_password" -T /usr/bin/codesign
fi
# Set access only on this dedicated keychain, which contains our local identity.
security set-key-partition-list -S apple-tool:,apple:,codesign: -s -k "$keychain_password" "$keychain" >/dev/null
if [[ "${THUNDERTALK_DEFER_TRUST:-0}" == 1 ]]; then
    echo "User trust deferred. Leaf-pinned signatures still work for local builds."
else
    # macOS may require the user's password to approve this trust setting.
    # The certificate is trusted ONLY for code signing, not SSL or other uses.
    security add-trusted-cert -r trustRoot -p codeSign -k "$keychain" "$scratch/cert.pem"
fi
security find-identity -p codesigning "$keychain"
echo "Keep this keychain and password file for every local build; never commit them."
