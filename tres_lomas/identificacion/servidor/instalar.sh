#!/usr/bin/env bash
# Instala campo.py en un servidor Ubuntu 24.04 (DigitalOcean) con HTTPS y contraseña.
# Lo corre subir.ps1 por vos; también se puede correr a mano, como root:
#     bash /root/servidor/instalar.sh
# Se puede correr de nuevo sin problema (por ejemplo, después de subir datos nuevos).
set -euo pipefail

CARPETA=/home/vacas/desfile_tres_lomas
VENV=/home/vacas/venv
PAQUETE=/root/vacas.tar

if [ "$(id -u)" != "0" ]; then echo "Correlo como root."; exit 1; fi

echo "== 1/7 Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3-venv python3-pip curl ufw gnupg debian-keyring debian-archive-keyring apt-transport-https libgl1
apt-get install -y -q libglib2.0-0t64 || apt-get install -y -q libglib2.0-0

echo "== 2/7 Memoria de reserva (swap de 4 GB, por si hay un pico)"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

echo "== 3/7 Usuario y datos"
id vacas >/dev/null 2>&1 || useradd -m -s /bin/bash vacas
if [ -f "$PAQUETE" ]; then
  systemctl stop campo 2>/dev/null || true
  mkdir -p "$CARPETA"
  tar -xf "$PAQUETE" -C "$CARPETA"
  chown -R vacas:vacas /home/vacas
  rm -f "$PAQUETE"
  echo "   datos descomprimidos en $CARPETA"
fi
[ -f "$CARPETA/campo.py" ] || { echo "No están los datos en $CARPETA. Corré subir.ps1 desde tu compu."; exit 1; }

echo "== 4/7 Python (torch solo CPU: unos minutos la primera vez)"
if [ ! -x "$VENV/bin/python" ]; then sudo -u vacas python3 -m venv "$VENV"; fi
sudo -u vacas "$VENV/bin/pip" install -q --upgrade pip
sudo -u vacas "$VENV/bin/pip" install -q --index-url https://download.pytorch.org/whl/cpu torch torchvision
sudo -u vacas "$VENV/bin/pip" install -q ultralytics timm flask pillow numpy

echo "== 5/7 Contraseña del equipo"
if [ ! -f /etc/campo.env ]; then
  while true; do
    read -r -s -p "   Elegí una contraseña para entrar a la página: " C1; echo
    read -r -s -p "   Repetila: " C2; echo
    [ -n "$C1" ] && [ "$C1" = "$C2" ] && break
    echo "   No coinciden (o está vacía). Probá de nuevo."
  done
  umask 077
  printf 'CAMPO_CLAVE=%s\n' "$C1" > /etc/campo.env
  umask 022
else
  echo "   ya estaba elegida (para cambiarla: borrá /etc/campo.env y corré esto de nuevo)"
fi

echo "== 6/7 Servicio campo.py (arranca solo, se reinicia si se cae)"
HILOS=$(( $(nproc) > 2 ? $(nproc) - 1 : $(nproc) ))   # deja un núcleo libre para la página si hay más de 2
cat > /etc/systemd/system/campo.service <<EOF
[Unit]
Description=Identificacion de vacas (campo.py)
After=network-online.target

[Service]
User=vacas
WorkingDirectory=$CARPETA
EnvironmentFile=/etc/campo.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/python campo.py --sin-navegador --hilos $HILOS --pausa 0
KillSignal=SIGINT
TimeoutStopSec=600
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable campo >/dev/null
systemctl restart campo

echo "== 7/7 HTTPS (Caddy) y firewall"
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi
IP=$(curl -4 -s --max-time 10 https://ifconfig.me || hostname -I | awk '{print $1}')
DOMINIO="${IP//./-}.nip.io"     # nombre gratis que apunta a esta IP, para tener certificado HTTPS
OTRO="${IP//./-}.sslip.io"      # igual que nip.io; algunos routers resuelven uno solo
cat > /etc/caddy/Caddyfile <<EOF
$DOMINIO, $OTRO {
    encode gzip
    reverse_proxy 127.0.0.1:5004
}
EOF
systemctl reload caddy || systemctl restart caddy
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null

echo
echo "================================================================"
echo " Listo. El equipo entra en:  https://$DOMINIO"
echo " Usuario: cualquiera · Contraseña: la que elegiste"
echo " La primera vez tarda unos 20 minutos (procesa las fotos de referencia)."
echo " Ver qué está haciendo:  journalctl -u campo -f"
echo "================================================================"
