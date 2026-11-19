#!/bin/bash
set -e

echo "================================================================="
echo "   INSTALANDO OS 2 SERVIÇOS IOT NA ORACLE CLOUD (24/7)"
echo "   1. mqtt-bridge (Ponte MQTT -> MongoDB)"
echo "   2. pipeline-iot (Tratamento MongoDB -> MariaDB turma_pi & Redis)"
echo "================================================================="

CURRENT_USER=$(whoami)
BASE_DIR=$(pwd)

# 1. Instalar dependências básicas do sistema
echo "[1/4] Instalando dependências do sistema (Python3, pip, venv)..."
if command -v apt-get &> /dev/null; then
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv
elif command -v dnf &> /dev/null; then
    sudo dnf install -y python3 python3-pip
elif command -v yum &> /dev/null; then
    sudo yum install -y python3 python3-pip
fi

# 2. Configurar o Serviço 1: mqtt-bridge
echo "[2/4] Configurando Serviço 1: mqtt-bridge..."
cd "$BASE_DIR/mqtt-bridge"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

sudo bash -c "cat <<EOF > /etc/systemd/system/mqtt-bridge.service
[Unit]
Description=Ponte MQTT para MongoDB (Meshtastic)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BASE_DIR/mqtt-bridge
ExecStart=$BASE_DIR/mqtt-bridge/venv/bin/python -u $BASE_DIR/mqtt-bridge/bridge.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

# 3. Configurar o Serviço 2: pipeline-iot
echo "[3/4] Configurando Serviço 2: pipeline-iot..."
cd "$BASE_DIR/pipeline-iot"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

sudo bash -c "cat <<EOF > /etc/systemd/system/pipeline-iot.service
[Unit]
Description=Pipeline Meshtastic IoT - Turma PI (MongoDB -> MariaDB/Redis)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$BASE_DIR/pipeline-iot
ExecStart=$BASE_DIR/pipeline-iot/venv/bin/python3 -u $BASE_DIR/pipeline-iot/pipeline.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF"

# 4. Ativar e Iniciar ambos os serviços
echo "[4/4] Ativando e iniciando ambos os serviços no systemd..."
sudo systemctl daemon-reload
sudo systemctl enable mqtt-bridge
sudo systemctl enable pipeline-iot
sudo systemctl restart mqtt-bridge
sudo systemctl restart pipeline-iot

echo ""
echo "================================================================="
echo "   PARABÉNS! OS DOIS SERVIÇOS ESTÃO RODANDO 24/7 NA ORACLE!      "
echo "================================================================="
echo ""
echo "Status do mqtt-bridge (MQTT -> Mongo):"
sudo systemctl status mqtt-bridge --no-pager
echo ""
echo "Status do pipeline-iot (Mongo -> MariaDB turma_pi):"
sudo systemctl status pipeline-iot --no-pager
echo ""
echo "-----------------------------------------------------------------"
echo "Comandos úteis para ver os logs em tempo real:"
echo "  Ver o MQTT Bridge:    journalctl -u mqtt-bridge -f"
echo "  Ver o Pipeline IoT:   journalctl -u pipeline-iot -f"
echo "-----------------------------------------------------------------"
