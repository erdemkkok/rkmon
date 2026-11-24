#!/bin/sh
# rkmon APT deposunu ekler ve paketi kurar:
#   curl -fsSL https://erdemkkok.github.io/rkmon/install.sh | sudo sh
set -e
[ "$(id -u)" -eq 0 ] || { echo "root olarak çalıştırın: ... | sudo sh"; exit 1; }
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://erdemkkok.github.io/rkmon/rkmon.gpg -o /usr/share/keyrings/rkmon.gpg
echo "deb [signed-by=/usr/share/keyrings/rkmon.gpg] https://erdemkkok.github.io/rkmon stable main" > /etc/apt/sources.list.d/rkmon.list
apt-get update -o Dir::Etc::sourcelist=sources.list.d/rkmon.list -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=0
apt-get install -y rkmon
echo "Kuruldu. Çalıştırmak için: sudo rkmon"
