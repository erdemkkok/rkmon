#!/usr/bin/env bash
# APT deposunu günceller ve imzalar.
#   scripts/update-repo.sh <depo_dizini> <paket.deb>...
# Ortam değişkenleri:
#   GPG_KEY_ID      imzalama anahtarı (boşsa varsayılan anahtar)
#   GPG_PASSPHRASE  anahtar parolası (varsa)
#   REPO_URL        https://kullanici.github.io/rkmon  (install.sh içine yazılır)
set -euo pipefail

REPO="$(realpath "$1")"; shift
SUITE=stable
COMPONENT=main
ARCHES="arm64 armhf amd64"

mkdir -p "$REPO/pool/$COMPONENT"
cp -v "$@" "$REPO/pool/$COMPONENT/"
cd "$REPO"

for arch in $ARCHES; do
  d="dists/$SUITE/$COMPONENT/binary-$arch"
  mkdir -p "$d"
  apt-ftparchive --arch "$arch" packages "pool/$COMPONENT" > "$d/Packages"
  gzip -9nkf "$d/Packages"
done

apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=rkmon \
  -o APT::FTPArchive::Release::Label=rkmon \
  -o APT::FTPArchive::Release::Suite=$SUITE \
  -o APT::FTPArchive::Release::Codename=$SUITE \
  -o "APT::FTPArchive::Release::Architectures=$ARCHES" \
  -o APT::FTPArchive::Release::Components=$COMPONENT \
  -o "APT::FTPArchive::Release::Description=rkmon APT repository" \
  release "dists/$SUITE" > "dists/$SUITE/Release.tmp"
mv "dists/$SUITE/Release.tmp" "dists/$SUITE/Release"

GPG=(gpg --batch --yes --pinentry-mode loopback)
[ -n "${GPG_KEY_ID:-}" ] && GPG+=(--local-user "$GPG_KEY_ID")
[ -n "${GPG_PASSPHRASE:-}" ] && GPG+=(--passphrase "$GPG_PASSPHRASE")
"${GPG[@]}" --clearsign -o "dists/$SUITE/InRelease" "dists/$SUITE/Release"
"${GPG[@]}" -abs -o "dists/$SUITE/Release.gpg" "dists/$SUITE/Release"

# Kullanıcıların indireceği açık anahtar (binary keyring formatı)
gpg --batch --yes --export ${GPG_KEY_ID:-} > rkmon.gpg
gpg --batch --yes --armor --export ${GPG_KEY_ID:-} > rkmon.asc

URL="${REPO_URL:-https://example.github.io/rkmon}"
cat > install.sh <<EOF
#!/bin/sh
# rkmon APT deposunu ekler ve paketi kurar:
#   curl -fsSL $URL/install.sh | sudo sh
set -e
[ "\$(id -u)" -eq 0 ] || { echo "root olarak çalıştırın: ... | sudo sh"; exit 1; }
install -d -m 0755 /usr/share/keyrings
curl -fsSL $URL/rkmon.gpg -o /usr/share/keyrings/rkmon.gpg
echo "deb [signed-by=/usr/share/keyrings/rkmon.gpg] $URL $SUITE $COMPONENT" > /etc/apt/sources.list.d/rkmon.list
apt-get update -o Dir::Etc::sourcelist=sources.list.d/rkmon.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
apt-get install -y rkmon
echo "Kuruldu. Çalıştırmak için: sudo rkmon"
EOF
chmod 755 install.sh

cat > index.html <<EOF
<!doctype html><meta charset="utf-8"><title>rkmon APT repository</title>
<body style="font-family:sans-serif;max-width:760px;margin:40px auto;line-height:1.5">
<h1>rkmon APT repository</h1>
<p>Hızlı kurulum:</p>
<pre>curl -fsSL $URL/install.sh | sudo sh</pre>
<p>Elle kurulum:</p>
<pre>curl -fsSL $URL/rkmon.gpg | sudo tee /usr/share/keyrings/rkmon.gpg &gt;/dev/null
echo "deb [signed-by=/usr/share/keyrings/rkmon.gpg] $URL $SUITE $COMPONENT" | sudo tee /etc/apt/sources.list.d/rkmon.list
sudo apt update &amp;&amp; sudo apt install rkmon</pre>
</body>
EOF
touch .nojekyll
echo "Depo güncellendi: $REPO"
