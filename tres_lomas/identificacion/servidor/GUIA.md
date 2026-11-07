# campo.py en DigitalOcean: guía paso a paso

Así los tres trabajan al mismo tiempo desde cualquier lugar, con contraseña y HTTPS, y tu compu
queda libre. Lo que se marca se guarda en el servidor; al final lo bajás a tu compu.

## Cuánto sale

- **Droplet Basic de 4 GB / 2 vCPU: USD 24 por mes** (alcanza: campo.py usa ~1,3 GB con tres
  personas). El de 8 GB / 4 vCPU (USD 48) procesa los frames que faltan el doble de rápido.
  Se puede agrandar después con **Resize** sin perder nada (achicar no). Desde 2026 DigitalOcean
  cobra por segundo, así que **pagás solo los días que exista**: 2 semanas ≈ USD 12.
- Un droplet **apagado igual se cobra**. Para dejar de pagar hay que **destruirlo** (paso 7).
- No hagan falta extras (backups, etc.). La transferencia incluida (5 TB) sobra.

## 1. Crear tu llave SSH (una sola vez, en tu compu)

En PowerShell:

```
ssh-keygen -t ed25519
```

Apretá Enter a todo. Después mostrá la llave pública y copiala entera:

```
type $env:USERPROFILE\.ssh\id_ed25519.pub
```

## 2. Crear el servidor en DigitalOcean

En cloud.digitalocean.com → **Create → Droplets**:

- **Region**: New York (la más cercana a Argentina).
- **Image**: Ubuntu 24.04 (LTS) x64.
- **Size**: Basic → Regular → **4 GB / 2 CPUs** (USD 24/mes), o 8 GB / 4 CPUs si querés más velocidad.
- **Authentication**: SSH Key → New SSH Key → pegá la llave del paso 1.
- **Hostname**: `vacas-tres-lomas`.

Create. Anotá la **IP** que te da (por ejemplo `159.203.10.20`).

Probá que entrás (la primera vez pregunta "Are you sure…": escribí `yes`):

```
ssh root@159.203.10.20 "echo ok"
```

## 3. Subir todo e instalar (un solo comando)

**Cerrá `campo.py` en tu compu.** Desde la carpeta `desfile_tres_lomas`, en PowerShell:

```
powershell -ExecutionPolicy Bypass -File servidor\subir.ps1 -IP 159.203.10.20
```

Arma un paquete con el programa, los modelos, los 8037 frames, las fotos de referencia y todo lo
marcado hasta ahora (unos 6,5 GB), lo sube, instala Python y lo necesario, te pide que **elijas la
contraseña del equipo**, y al final muestra la dirección, algo así:

```
 Listo. El equipo entra en:  https://159-203-10-20.nip.io
```

(`nip.io` es un servicio gratis que da un nombre a la IP para poder tener HTTPS. No hace falta
comprar un dominio.)

La subida es lo que más tarda: con 10 Mbps de subida, alrededor de 1 h y media. Si se corta,
volvé a correr el mismo comando.

## 4. Pasarles el acceso a los otros dos

Mandales la dirección y la contraseña. El navegador pide **usuario** (cualquiera) y **contraseña**.
Después la página les pide su nombre, para saber quién marcó cada recorrido.

La primera vez que arranca tarda 2 a 5 minutos (carga los modelos y recalcula las huellas de las
fotos de referencia). Mientras tanto la página no responde: es normal.

**Desde que subiste, trabajen solo en el servidor.** Si abrís `campo.py` en tu compu, vas a tener
dos versiones distintas de lo marcado.

## 5. El día a día

- El reentrenamiento (la "huella afinada") se hace solo cada 5 recorridos confirmados, igual que
  en tu compu. No hay que hacer nada.
- El procesamiento de los frames que faltan sigue solo en el servidor (usa todos los núcleos menos uno si tiene más de 2).
- Si el servidor se reinicia, `campo.py` arranca solo.
- Ver qué está haciendo:
  ```
  ssh root@159.203.10.20 "journalctl -u campo -n 50"
  ```

## 6. Seguir trabajando con Claude

Sí, se puede. Hay dos formas:

- **Que Claude trabaje directo en el servidor**: en la app de Claude, agregá una conexión SSH a
  `root@159.203.10.20` (con la misma llave del paso 1) y abrí una sesión ahí, en la carpeta
  `/home/vacas/desfile_tres_lomas`. Claude puede leer los datos, cambiar el código y reiniciar el
  servicio (`systemctl restart campo`). Condición: el droplet tiene que existir y estar prendido.
- **Seguir como ahora, en tu compu**: Claude cambia el código acá y vos lo subís con:
  ```
  powershell -ExecutionPolicy Bypass -File servidor\actualizar.ps1 -IP 159.203.10.20
  ```
  Esto sube solo el código (no los datos) y reinicia. Lo marcado no se pierde, pero la página queda
  unos minutos sin andar: avisale al equipo.

## 7. Al terminar: bajar todo y destruir el servidor

1. Avisale al equipo (se frena unos minutos) y corré:
   ```
   powershell -ExecutionPolicy Bypass -File servidor\bajar.ps1 -IP 159.203.10.20
   ```
   Baja `campo`, `identificacion` y `recorridas_campo`. Tus carpetas locales anteriores quedan
   guardadas con la fecha (`campo_antes_servidor_…`).
2. Abrí `campo.py` en tu compu y fijate que esté todo lo marcado.
3. En DigitalOcean: el droplet → **Destroy**. Recién ahí deja de cobrar.

Si querés pausar unas semanas sin perder nada: Power off → **Take snapshot** (USD 0,06 por
GB por mes; con estos datos, alrededor de USD 1 por mes) → Destroy. Para volver, creás un droplet desde
ese snapshot.

## Si algo falla

| Qué pasa | Qué hacer |
| --- | --- |
| La página no carga recién instalada | Esperá 5 minutos (carga modelos). Mirá `journalctl -u campo -n 50`. |
| El navegador avisa que la conexión no es segura | El certificado tarda 1 o 2 minutos la primera vez. Recargá. |
| Te olvidaste la contraseña | `ssh root@IP "cat /etc/campo.env"` |
| Cambiar la contraseña | `ssh root@IP "rm /etc/campo.env"` y después `ssh -t root@IP "bash /root/servidor/instalar.sh"` |
