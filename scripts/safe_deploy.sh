#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/imobicrm}"
SERVICE_NAME="${SERVICE_NAME:-imobicrm}"

cd "$APP_DIR"

echo "==> [1/6] Atualizando repositório (git pull --ff-only)..."
git pull --ff-only

echo "==> [2/6] Ativando virtualenv e carregando .env..."
# shellcheck disable=SC1091
source venv/bin/activate
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

echo "==> [3/6] Executando Django system check..."
python manage.py check

echo "==> [4/6] Executando suíte de testes (aborta deploy se falhar)..."
python manage.py test

echo "==> [5/6] Aplicando migrations e coletando arquivos estáticos..."
python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo "==> [6/6] Reiniciando serviço $SERVICE_NAME..."
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active "$SERVICE_NAME"

echo "==> Deploy concluído com sucesso!"
