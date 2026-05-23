#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo bash "${BASH_SOURCE[0]}" "$@"
  fi
  echo "请使用 sudo 运行: sudo bash scripts/update_debian.sh"
  exit 1
fi

cd "${ROOT_DIR}"

set_env() {
  local key="$1"
  local value="$2"
  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    echo "${key}=${value}" >> "${ENV_FILE}"
  fi
}

read_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo ""
    return
  fi
  echo "${line#*=}"
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return
  fi
  date +%s%N | sha256sum | awk '{print $1}'
}

if [[ ! -f "${ENV_FILE}" ]]; then
  echo ".env 不存在，请先运行 sudo bash scripts/setup_debian.sh"
  exit 1
fi

redis_password="$(read_env "REDIS_PASSWORD")"
if [[ -z "${redis_password}" ]]; then
  redis_password="$(generate_secret)"
  set_env "REDIS_PASSWORD" "${redis_password}"
fi
set_env "REDIS_URL" "redis://:${redis_password}@redis:6379/0"

git fetch --all --prune
git pull --ff-only

docker compose up -d --build
docker compose exec -T bot alembic upgrade head

echo "更新完成。"
echo "查看日志: docker compose logs -f bot"
