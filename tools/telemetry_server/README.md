# VCFG Crash & Log Telemetry Server (VPS -> Disk + Telegram / Discord)

Лёгкий сервер приёма логов и краш-репортов с PS4 (`VCFG Launcher` и плагина `vcfg_classic.prx`), не требующий никаких внешних библиотек (только стандартный `python3`).

## 1. Архитектура
1. **PS4 (`VCFG Launcher` и плагин `vcfg_classic.prx`)**:
   - Читает адрес сервера из `/data/vcfg/vcfg_launcher.ini` (или `/data/vcfg/telemetry.ini`):
     ```ini
     telemetry_host = 203.0.113.10
     telemetry_port = 8765
     auto_send = 1
     ```
   - **Авто-отправка при вылетах (`auto_send = 1`)**: если игра вылетела и оставила `/data/vcfg/crash-CUSAxxxxx.txt`, то при следующем старте игры или открытии `VCFG Launcher` плагин/аппка в фоновом неблокирующем сокете (таймаут 2.5 сек) отправляет полный бандл (`crash` + `boot` + `trace` + `adapt` + `config`) на VPS и записывает хэш/размер в `/data/vcfg/sent_crashes.ini`, чтобы не дублировать отправку.
   - **Ручная отправка из `VCFG Launcher`**:
     - В колонке **`04 PLUGIN`** или **`05 DEV`** нажатие `CIRCLE: SAVE / SEND REPORT` сохраняет `/data/vcfg_report.txt` и мгновенно отправляет его на ваш VPS.
     - На экране ТВ пользователь видит короткий 6-символьный код репорта: **`REPORT SENT! ID: #A7F39C`** — этот код пользователь может скинуть вам в чат, а вы сразу найдёте его в папке `reports/CUSAxxxxx/` или в своём Telegram/Discord канале!

## 2. Запуск на арендованном VPS (Ubuntu / Debian)

Скопируйте `vcfg_server.py` на ваш VPS (например, в `/opt/vcfg_telemetry/vcfg_server.py`) и запустите:

```bash
mkdir -p /opt/vcfg_telemetry/reports
python3 /opt/vcfg_telemetry/vcfg_server.py --port 8765 --data-dir /opt/vcfg_telemetry/reports
```

### Автозапуск через `systemd` + пересылка в Telegram / Discord
Создайте `/etc/systemd/system/vcfg-telemetry.service`:

```ini
[Unit]
Description=VCFG PS4 Crash & Log Telemetry Server
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/vcfg_telemetry
Environment="VCFG_PORT=8765"
Environment="VCFG_DATA_DIR=/opt/vcfg_telemetry/reports"
# Необязательно: укажите токен бота и ID чата/канала для мгновенной пересылки .txt отчётов в Telegram:
Environment="TG_BOT_TOKEN="
Environment="TG_CHAT_ID="
# Необязательно: или укажите Discord Webhook URL:
Environment="DISCORD_WEBHOOK_URL="
ExecStart=/usr/bin/python3 /opt/vcfg_telemetry/vcfg_server.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Активируйте сервис и откройте порт `8765/tcp`:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vcfg-telemetry
sudo ufw allow 8765/tcp
```
