from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.models import AdminGrant, AuditLog, BlacklistUser, Chat, ChatAdmin, ChatMember, MessageStat, Punishment, Rule, User, UserReport, Violation, WhitelistUser


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def ensure_chat(session: AsyncSession, chat_id: int, title: str | None, default_log_chat_id: int | None, newcomer_watch_seconds: int) -> Chat:
    query: Select[tuple[Chat]] = select(Chat).where(Chat.id == chat_id)
    result = await session.execute(query)
    chat = result.scalar_one_or_none()
    if chat is None:
        chat = Chat(
            id=chat_id,
            title=title,
            log_chat_id=default_log_chat_id,
            newcomer_watch_seconds=newcomer_watch_seconds,
        )
        session.add(chat)
        await session.flush()
        return chat

    changed = False
    if title is not None and chat.title != title:
        chat.title = title
        changed = True
    if chat.log_chat_id is None and default_log_chat_id is not None:
        chat.log_chat_id = default_log_chat_id
        changed = True
    if changed:
        await session.flush()
    return chat


async def ensure_user(session: AsyncSession, user_id: int, username: str | None, full_name: str | None, is_bot: bool, language_code: str | None) -> User:
    query: Select[tuple[User]] = select(User).where(User.id == user_id)
    result = await session.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            id=user_id,
            username=username,
            full_name=full_name,
            is_bot=is_bot,
            language_code=language_code,
        )
        session.add(user)
        await session.flush()
        return user

    user.username = username
    user.full_name = full_name
    user.is_bot = is_bot
    user.language_code = language_code
    await session.flush()
    return user


async def ensure_chat_member(session: AsyncSession, chat_id: int, user_id: int, joined_at: datetime | None, is_newcomer: bool) -> ChatMember:
    query: Select[tuple[ChatMember]] = select(ChatMember).where(
        and_(ChatMember.chat_id == chat_id, ChatMember.user_id == user_id)
    )
    result = await session.execute(query)
    member = result.scalar_one_or_none()
    if member is None:
        member = ChatMember(
            chat_id=chat_id,
            user_id=user_id,
            joined_at=joined_at,
            is_newcomer=is_newcomer,
            status="member",
        )
        session.add(member)
        await session.flush()
        return member

    if joined_at is not None and member.joined_at is None:
        member.joined_at = joined_at
    if member.is_newcomer != is_newcomer:
        member.is_newcomer = is_newcomer
    await session.flush()
    return member


async def mark_member_first_message(session: AsyncSession, member: ChatMember, first_message_at: datetime) -> None:
    if member.first_message_at is None:
        member.first_message_at = first_message_at
        await session.flush()


async def increment_message_stats(session: AsyncSession, chat_id: int, user_id: int) -> MessageStat:
    stats = await get_or_create_message_stats(session, chat_id, user_id)
    stats.total_messages = int(stats.total_messages) + 1
    await session.flush()
    return stats


async def get_or_create_message_stats(session: AsyncSession, chat_id: int, user_id: int) -> MessageStat:
    query: Select[tuple[MessageStat]] = select(MessageStat).where(
        and_(MessageStat.chat_id == chat_id, MessageStat.user_id == user_id)
    )
    result = await session.execute(query)
    stats = result.scalar_one_or_none()
    if stats is None:
        stats = MessageStat(chat_id=chat_id, user_id=user_id, total_messages=0)
        session.add(stats)
        await session.flush()
        return stats

    return stats


async def increment_violation_stats(session: AsyncSession, chat_id: int, user_id: int, action: str) -> None:
    stats = await get_or_create_message_stats(session, chat_id, user_id)
    stats.total_violations = int(stats.total_violations) + 1
    if action == "warn":
        stats.total_warns = int(stats.total_warns) + 1
    if action == "mute":
        stats.total_mutes = int(stats.total_mutes) + 1
    if action == "ban":
        stats.total_bans = int(stats.total_bans) + 1
    await session.flush()


async def decrement_violation_stats(session: AsyncSession, chat_id: int, user_id: int, action: str) -> None:
    stats = await get_or_create_message_stats(session, chat_id, user_id)
    if int(stats.total_violations) > 0:
        stats.total_violations = int(stats.total_violations) - 1
    if action == "warn" and int(stats.total_warns) > 0:
        stats.total_warns = int(stats.total_warns) - 1
    if action == "mute" and int(stats.total_mutes) > 0:
        stats.total_mutes = int(stats.total_mutes) - 1
    if action == "ban" and int(stats.total_bans) > 0:
        stats.total_bans = int(stats.total_bans) - 1
    await session.flush()


async def is_user_whitelisted(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    query: Select[tuple[WhitelistUser]] = select(WhitelistUser).where(
        and_(WhitelistUser.chat_id == chat_id, WhitelistUser.user_id == user_id)
    )
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


async def is_user_blacklisted(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    query: Select[tuple[BlacklistUser]] = select(BlacklistUser).where(
        and_(BlacklistUser.chat_id == chat_id, BlacklistUser.user_id == user_id)
    )
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


async def add_whitelist_user(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    if await is_user_whitelisted(session, chat_id, user_id):
        return False
    session.add(WhitelistUser(chat_id=chat_id, user_id=user_id))
    await session.flush()
    return True


async def add_blacklist_user(session: AsyncSession, chat_id: int, user_id: int, reason: str) -> bool:
    if await is_user_blacklisted(session, chat_id, user_id):
        return False
    session.add(BlacklistUser(chat_id=chat_id, user_id=user_id, reason=reason))
    await session.flush()
    return True


async def create_violation(session: AsyncSession, chat_id: int, user_id: int, message_id: int | None, rule_name: str, reason: str, content_excerpt: str | None, score: int, rule_id: int | None) -> Violation:
    violation = Violation(
        chat_id=chat_id,
        user_id=user_id,
        message_id=message_id,
        rule_name=rule_name,
        reason=reason,
        content_excerpt=content_excerpt,
        score=score,
        rule_id=rule_id,
    )
    session.add(violation)
    await session.flush()
    return violation


async def create_punishment(session: AsyncSession, violation_id: int | None, chat_id: int, user_id: int, action: str, duration_seconds: int | None, reason: str, executed_by: int | None) -> Punishment:
    punishment = Punishment(
        violation_id=violation_id,
        chat_id=chat_id,
        user_id=user_id,
        action=action,
        duration_seconds=duration_seconds,
        reason=reason,
        executed_by=executed_by,
    )
    session.add(punishment)
    await session.flush()
    return punishment


async def create_audit_log(session: AsyncSession, chat_id: int | None, actor_user_id: int | None, target_user_id: int | None, action: str, detail_json: dict[str, object]) -> AuditLog:
    item = AuditLog(
        chat_id=chat_id,
        actor_user_id=actor_user_id,
        target_user_id=target_user_id,
        action=action,
        detail_json=detail_json,
    )
    session.add(item)
    await session.flush()
    return item


async def list_user_history(session: AsyncSession, chat_id: int, user_id: int, limit: int) -> list[Punishment]:
    query: Select[tuple[Punishment]] = (
        select(Punishment)
        .where(and_(Punishment.chat_id == chat_id, Punishment.user_id == user_id))
        .order_by(Punishment.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def count_recent_violations(session: AsyncSession, chat_id: int, user_id: int, within_hours: int) -> int:
    since = _utc_now() - timedelta(hours=within_hours)
    query = select(func.count(Violation.id)).where(
        and_(Violation.chat_id == chat_id, Violation.user_id == user_id, Violation.created_at >= since)
    )
    result = await session.execute(query)
    count = result.scalar_one()
    return int(count)


async def get_or_create_default_rules(session: AsyncSession, chat_id: int) -> list[Rule]:
    query: Select[tuple[Rule]] = select(Rule).where(Rule.chat_id == chat_id)
    result = await session.execute(query)
    existing = list(result.scalars().all())
    if len(existing) > 0:
        return existing

    defaults = [
        Rule(
            chat_id=chat_id,
            name="newcomer_no_links_24h",
            rule_type="newcomer",
            trigger_json={"kind": "link", "watch_seconds": 86400},
            newcomer_only=True,
            action="delete",
            severity=1,
            enabled=True,
            note="newcomer cannot send links during watch period",
        ),
        Rule(
            chat_id=chat_id,
            name="flood_5_in_10",
            rule_type="flood",
            trigger_json={"window_seconds": 10, "max_messages": 5},
            newcomer_only=False,
            action="warn",
            severity=2,
            enabled=True,
            note="flood baseline",
        ),
        Rule(
            chat_id=chat_id,
            name="keyword_blacklist",
            rule_type="keyword",
            trigger_json={"match": "keywords"},
            newcomer_only=False,
            action="delete",
            severity=2,
            enabled=True,
            note="keyword filter",
        ),
    ]
    for item in defaults:
        session.add(item)
    await session.flush()
    return defaults


async def get_chat_settings(session: AsyncSession, chat_id: int) -> Chat | None:
    query: Select[tuple[Chat]] = select(Chat).where(Chat.id == chat_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def update_chat_log_chat(session: AsyncSession, chat_id: int, log_chat_id: int) -> None:
    chat = await get_chat_settings(session, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    chat.log_chat_id = log_chat_id
    await session.flush()


async def set_newcomer_mode(session: AsyncSession, chat_id: int, enabled: bool, watch_seconds: int, allow_links: bool, allow_media: bool) -> None:
    chat = await get_chat_settings(session, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    chat.newcomer_restrict_enabled = enabled
    chat.newcomer_watch_seconds = watch_seconds
    chat.allow_links = allow_links
    chat.allow_media = allow_media
    await session.flush()


async def set_chat_switches(
    session: AsyncSession,
    chat_id: int,
    newcomer_restrict_enabled: bool,
    keyword_filter_enabled: bool,
    link_filter_enabled: bool,
    flood_enabled: bool,
) -> None:
    chat = await get_chat_settings(session, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    chat.newcomer_restrict_enabled = newcomer_restrict_enabled
    chat.keyword_filter_enabled = keyword_filter_enabled
    chat.link_filter_enabled = link_filter_enabled
    chat.flood_enabled = flood_enabled
    await session.flush()


def get_chat_enforcement_mode(chat: Chat) -> str:
    runtime = get_chat_runtime_settings(chat)
    mode = str(runtime.get("enforcement_mode", "enforce"))
    if mode not in {"enforce", "observe"}:
        return "enforce"
    return mode


async def set_chat_enforcement_mode(session: AsyncSession, chat_id: int, mode: str) -> None:
    if mode not in {"enforce", "observe"}:
        raise ValueError(f"unsupported mode: {mode}")
    chat = await get_chat_settings(session, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    next_settings = get_chat_runtime_settings(chat)
    next_settings["enforcement_mode"] = mode
    chat.settings_json = next_settings
    await session.flush()


async def list_chats_for_panel(session: AsyncSession) -> list[Chat]:
    query: Select[tuple[Chat]] = select(Chat).order_by(Chat.updated_at.desc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def count_violations(session: AsyncSession, chat_id: int, since: datetime) -> int:
    query = select(func.count(Violation.id)).where(and_(Violation.chat_id == chat_id, Violation.created_at >= since))
    result = await session.execute(query)
    return int(result.scalar_one())


async def count_punishments(session: AsyncSession, chat_id: int, since: datetime) -> int:
    query = select(func.count(Punishment.id)).where(and_(Punishment.chat_id == chat_id, Punishment.created_at >= since))
    result = await session.execute(query)
    return int(result.scalar_one())


async def count_members_joined(session: AsyncSession, chat_id: int, since: datetime) -> int:
    query = select(func.count(ChatMember.id)).where(and_(ChatMember.chat_id == chat_id, ChatMember.joined_at.is_not(None), ChatMember.joined_at >= since))
    result = await session.execute(query)
    return int(result.scalar_one())


async def count_verification_passed(session: AsyncSession, chat_id: int, since: datetime) -> int:
    from bot.database.models import VerificationSession

    query = select(func.count(VerificationSession.id)).where(
        and_(
            VerificationSession.chat_id == chat_id,
            VerificationSession.status == "passed",
            VerificationSession.verified_at.is_not(None),
            VerificationSession.verified_at >= since,
        )
    )
    result = await session.execute(query)
    return int(result.scalar_one())


async def count_verification_total(session: AsyncSession, chat_id: int, since: datetime) -> int:
    from bot.database.models import VerificationSession

    query = select(func.count(VerificationSession.id)).where(
        and_(
            VerificationSession.chat_id == chat_id,
            VerificationSession.created_at >= since,
        )
    )
    result = await session.execute(query)
    return int(result.scalar_one())


async def top_active_users(session: AsyncSession, chat_id: int, limit: int) -> list[MessageStat]:
    query: Select[tuple[MessageStat]] = (
        select(MessageStat)
        .where(MessageStat.chat_id == chat_id)
        .order_by(MessageStat.total_messages.desc())
        .limit(limit)
    )
    result = await session.execute(query)
    return list(result.scalars().all())


async def list_audit_logs(
    session: AsyncSession,
    chat_id: int | None,
    actor_user_id: int | None,
    action_prefix: str | None,
    since: datetime,
    limit: int,
) -> list[AuditLog]:
    query = select(AuditLog).where(AuditLog.created_at >= since)
    if chat_id is not None:
        query = query.where(AuditLog.chat_id == chat_id)
    if actor_user_id is not None:
        query = query.where(AuditLog.actor_user_id == actor_user_id)
    if action_prefix is not None and action_prefix != "":
        query = query.where(AuditLog.action.like(f"{action_prefix}%"))
    query = query.order_by(AuditLog.created_at.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def upsert_chat_admins(session: AsyncSession, chat_id: int, admins: list[dict[str, object]]) -> int:
    await session.execute(delete(ChatAdmin).where(ChatAdmin.chat_id == chat_id))
    created = 0
    for item in admins:
        user_id = int(item["user_id"])
        session.add(
            ChatAdmin(
                chat_id=chat_id,
                user_id=user_id,
                status=str(item.get("status", "administrator")),
                can_delete_messages=bool(item.get("can_delete_messages", False)),
                can_restrict_members=bool(item.get("can_restrict_members", False)),
                can_promote_members=bool(item.get("can_promote_members", False)),
                can_manage_chat=bool(item.get("can_manage_chat", False)),
            )
        )
        created += 1
    await session.flush()
    return created


async def list_chat_admins(session: AsyncSession, chat_id: int) -> list[ChatAdmin]:
    query: Select[tuple[ChatAdmin]] = select(ChatAdmin).where(ChatAdmin.chat_id == chat_id).order_by(ChatAdmin.user_id.asc())
    result = await session.execute(query)
    return list(result.scalars().all())


async def is_admin_granted(session: AsyncSession, chat_id: int, user_id: int) -> bool:
    query: Select[tuple[AdminGrant]] = select(AdminGrant).where(
        and_(
            AdminGrant.chat_id == chat_id,
            AdminGrant.user_id == user_id,
            AdminGrant.active.is_(True),
        )
    )
    result = await session.execute(query)
    return result.scalar_one_or_none() is not None


async def set_admin_grant(session: AsyncSession, chat_id: int, user_id: int, granted_by: int, active: bool) -> AdminGrant:
    query: Select[tuple[AdminGrant]] = select(AdminGrant).where(
        and_(AdminGrant.chat_id == chat_id, AdminGrant.user_id == user_id)
    )
    result = await session.execute(query)
    row = result.scalar_one_or_none()
    if row is None:
        row = AdminGrant(chat_id=chat_id, user_id=user_id, granted_by=granted_by, active=active)
        session.add(row)
    else:
        row.granted_by = granted_by
        row.active = active
    await session.flush()
    return row


async def list_all_chat_ids(session: AsyncSession) -> list[int]:
    query = select(Chat.id)
    result = await session.execute(query)
    return [int(item) for item in result.scalars().all()]


async def create_user_report(
    session: AsyncSession,
    chat_id: int,
    reporter_user_id: int,
    target_user_id: int | None,
    message_link: str | None,
    reason: str,
) -> UserReport:
    item = UserReport(
        chat_id=chat_id,
        reporter_user_id=reporter_user_id,
        target_user_id=target_user_id,
        message_link=message_link,
        reason=reason,
        status="pending",
    )
    session.add(item)
    await session.flush()
    return item


async def get_violation_by_id(session: AsyncSession, violation_id: int) -> Violation | None:
    query: Select[tuple[Violation]] = select(Violation).where(Violation.id == violation_id)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def revoke_punishments_by_violation(
    session: AsyncSession,
    violation_id: int,
    revoked_by: int,
    revoked_at: datetime,
) -> list[str]:
    query: Select[tuple[Punishment]] = select(Punishment).where(Punishment.violation_id == violation_id, Punishment.revoked.is_(False))
    rows = list((await session.execute(query)).scalars().all())
    revoked_actions: list[str] = []
    for row in rows:
        row.revoked = True
        row.revoked_by = revoked_by
        row.revoked_at = revoked_at
        revoked_actions.append(str(row.action))
    await session.flush()
    return revoked_actions


async def top_false_positive_rules(session: AsyncSession, chat_id: int, limit: int) -> list[dict[str, object]]:
    query = (
        select(
            Violation.rule_name,
            func.count(AuditLog.id).label("false_positive_count"),
        )
        .join(AuditLog, and_(AuditLog.chat_id == Violation.chat_id, AuditLog.target_user_id == Violation.user_id))
        .where(
            Violation.chat_id == chat_id,
            AuditLog.action == "false_positive_marked",
            AuditLog.detail_json["violation_id"].as_integer() == Violation.id,
        )
        .group_by(Violation.rule_name)
        .order_by(func.count(AuditLog.id).desc())
        .limit(limit)
    )
    rows = (await session.execute(query)).all()
    result: list[dict[str, object]] = []
    for rule_name, count in rows:
        result.append({"rule_name": str(rule_name), "false_positive_count": int(count)})
    return result


def _default_chat_runtime_settings() -> dict[str, object]:
    return {
        "enforcement_mode": "enforce",
        "night_mode": {
            "enabled": False,
            "timezone": "Asia/Shanghai",
            "start_hour": 0,
            "end_hour": 6,
            "flood_window_seconds": 10,
            "flood_max_messages": 3,
            "newcomer_links_blocked": True,
            "newcomer_media_blocked": True,
            "ad_action": "delete",
        },
        "template_name": "standard",
        "allow_auto_ban": False,
        "observe_mode": False,
    }


def get_chat_runtime_settings(chat: Chat) -> dict[str, object]:
    base = _default_chat_runtime_settings()
    current = chat.settings_json if isinstance(chat.settings_json, dict) else {}
    merged = dict(base)
    for key, value in current.items():
        if key == "night_mode" and isinstance(value, dict):
            night = dict(base["night_mode"])  # type: ignore[arg-type]
            night.update(value)
            merged["night_mode"] = night
            continue
        merged[key] = value
    return merged


async def set_chat_runtime_settings(session: AsyncSession, chat_id: int, runtime_settings: dict[str, object]) -> None:
    chat = await get_chat_settings(session, chat_id)
    if chat is None:
        raise ValueError(f"chat not found: {chat_id}")
    chat.settings_json = runtime_settings
    await session.flush()


async def create_config_snapshot(session: AsyncSession, chat_id: int, actor_user_id: int, note: str, config_json: dict[str, object]) -> None:
    await create_audit_log(
        session=session,
        chat_id=chat_id,
        actor_user_id=actor_user_id,
        target_user_id=None,
        action="config_snapshot_created",
        detail_json={"note": note, "config_json": config_json},
    )
