from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.database import repositories
from bot.schemas.lexicon import ModerationAction
from bot.schemas.lexicon import RiskLevel
from bot.schemas.moderation import RuleHit
from bot.services import audit_log, moderation


@dataclass(frozen=True)
class ModerationOutcome:
    action_requested: ModerationAction
    action_executed: str
    duration_seconds: int | None
    delete_success: bool
    error: str | None


def _hit_priority(hit: RuleHit) -> tuple[int, int]:
    return (hit.score, 1 if hit.is_flood else 0)


def _resolve_duration_seconds(hit: RuleHit, settings: Settings) -> int | None:
    if hit.action == ModerationAction.MUTE:
        if hit.risk_level.value == "medium":
            return settings.mute_minutes_step3 * 60
        if hit.risk_level.value == "high":
            return settings.mute_hours_step4 * 3600
        return settings.mute_hours_step4 * 3600
    return None


def _escalate_action(base_action: ModerationAction, risk_level: RiskLevel, violation_count: int, allow_ban: bool) -> ModerationAction:
    if risk_level == RiskLevel.LOW:
        return ModerationAction.LOG
    if risk_level == RiskLevel.MEDIUM:
        if violation_count >= 2:
            return ModerationAction.DELETE
        return base_action
    if risk_level == RiskLevel.HIGH:
        if violation_count >= 5 and allow_ban:
            return ModerationAction.BAN
        if violation_count >= 3:
            return ModerationAction.MUTE
        return base_action
    if allow_ban:
        return ModerationAction.BAN
    if violation_count >= 3:
        return ModerationAction.MUTE
    return base_action


async def _bot_permission_for_action(bot: Bot, bot_user_id: int, chat_id: int, action: ModerationAction) -> tuple[bool, str]:
    member = await bot.get_chat_member(chat_id=chat_id, user_id=bot_user_id)
    if member.status == "creator":
        return True, "creator"
    if member.status != "administrator":
        return False, "bot_not_admin"
    if action in {ModerationAction.DELETE, ModerationAction.WARN}:
        can_delete = bool(getattr(member, "can_delete_messages", False))
        return can_delete, "missing_can_delete_messages"
    if action in {ModerationAction.MUTE}:
        can_restrict = bool(getattr(member, "can_restrict_members", False))
        return can_restrict, "missing_can_restrict_members"
    if action in {ModerationAction.BAN, ModerationAction.KICK}:
        can_restrict = bool(getattr(member, "can_restrict_members", False))
        return can_restrict, "missing_can_restrict_members"
    return True, "ok"


async def _execute_action(
    message: Message,
    action: ModerationAction,
    duration_seconds: int | None,
) -> ModerationOutcome:
    delete_success = True
    error: str | None = None
    chat = message.chat
    user = message.from_user
    if chat is None or user is None:
        return ModerationOutcome(action_requested=action, action_executed="none", duration_seconds=duration_seconds, delete_success=False, error="missing_chat_or_user")

    if action in {ModerationAction.DELETE, ModerationAction.WARN, ModerationAction.MUTE, ModerationAction.BAN, ModerationAction.KICK}:
        delete_success = await moderation.try_delete_message(message)

    executed = "none"
    if action == ModerationAction.LOG:
        executed = "log"
    elif action == ModerationAction.NOTIFY:
        executed = "notify"
    elif action == ModerationAction.DELETE:
        executed = "delete" if delete_success else "none"
    elif action == ModerationAction.WARN:
        try:
            await message.answer(f"用户 {user.id} 违规，已警告。")
            executed = "warn"
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            executed = "none"
    elif action == ModerationAction.MUTE:
        if duration_seconds is None:
            return ModerationOutcome(action_requested=action, action_executed="none", duration_seconds=duration_seconds, delete_success=delete_success, error="mute_duration_required")
        try:
            await moderation.mute_user(message.bot, chat.id, user.id, duration_seconds)
            executed = "mute"
        except moderation.ModerationActionError as exc:
            error = str(exc)
            executed = "none"
    elif action == ModerationAction.BAN:
        try:
            await moderation.ban_user(message.bot, chat.id, user.id)
            executed = "ban"
        except moderation.ModerationActionError as exc:
            error = str(exc)
            executed = "none"
    elif action == ModerationAction.KICK:
        try:
            await moderation.ban_user(message.bot, chat.id, user.id)
            await moderation.unban_user(message.bot, chat.id, user.id)
            executed = "kick"
        except moderation.ModerationActionError as exc:
            error = str(exc)
            executed = "none"
    return ModerationOutcome(action_requested=action, action_executed=executed, duration_seconds=duration_seconds, delete_success=delete_success, error=error)


async def handle_hits(
    message: Message,
    session: AsyncSession,
    settings: Settings,
    chat_id: int,
    user_id: int,
    log_chat_id: int | None,
    enforcement_mode: str,
    hits: list[RuleHit],
    original_text: str,
    protected_target: bool,
    allow_auto_ban: bool,
) -> ModerationOutcome:
    if len(hits) == 0:
        return ModerationOutcome(action_requested=ModerationAction.NONE, action_executed="none", duration_seconds=None, delete_success=True, error=None)

    sorted_hits = sorted(hits, key=_hit_priority, reverse=True)
    primary_hit = sorted_hits[0]
    reason = ",".join(item.reason for item in sorted_hits)
    score = sum(item.score for item in sorted_hits)
    violation = await repositories.create_violation(
        session=session,
        chat_id=chat_id,
        user_id=user_id,
        message_id=message.message_id,
        rule_name=primary_hit.rule_name,
        reason=reason,
        content_excerpt=original_text[:200],
        score=score,
        rule_id=None,
    )

    if enforcement_mode == "observe":
        await repositories.create_punishment(
            session=session,
            violation_id=violation.id,
            chat_id=chat_id,
            user_id=user_id,
            action="observe",
            duration_seconds=None,
            reason=reason,
            executed_by=None,
        )
        await repositories.create_audit_log(
            session=session,
            chat_id=chat_id,
            actor_user_id=None,
            target_user_id=user_id,
            action="moderation_observe_hit",
            detail_json={
                "rule_name": primary_hit.rule_name,
                "category": primary_hit.category,
                "risk_level": primary_hit.risk_level.value,
                "trigger": primary_hit.trigger,
                "source": primary_hit.source,
                "score": score,
            },
        )
        await audit_log.send_log(
            bot=message.bot,
            log_chat_id=log_chat_id,
            chat_id=chat_id,
            user_id=user_id,
            reason=reason,
            score=score,
            action="observe",
            excerpt=original_text,
        )
        return ModerationOutcome(action_requested=ModerationAction.LOG, action_executed="observe", duration_seconds=None, delete_success=True, error=None)

    if protected_target:
        await repositories.create_audit_log(
            session=session,
            chat_id=chat_id,
            actor_user_id=None,
            target_user_id=user_id,
            action="moderation_target_protected",
            detail_json={"reason": "target_is_owner_admin_or_whitelisted"},
        )
        return ModerationOutcome(action_requested=ModerationAction.NONE, action_executed="none", duration_seconds=None, delete_success=True, error="target_is_protected")

    violation_count = await repositories.count_recent_violations(session, chat_id, user_id, 24)
    requested_action = _escalate_action(primary_hit.action, primary_hit.risk_level, violation_count, allow_ban=allow_auto_ban)
    if requested_action == ModerationAction.BAN and not allow_auto_ban:
        requested_action = ModerationAction.MUTE
    duration_seconds = _resolve_duration_seconds(primary_hit, settings)

    try:
        bot_user = await message.bot.get_me()
    except TelegramAPIError:
        await repositories.create_audit_log(
            session=session,
            chat_id=chat_id,
            actor_user_id=None,
            target_user_id=user_id,
            action="moderation_bot_identity_failed",
            detail_json={"reason": "get_me_failed"},
        )
        return ModerationOutcome(action_requested=ModerationAction.NONE, action_executed="none", duration_seconds=None, delete_success=True, error="bot_identity_lookup_failed")
    can_execute, denied_reason = await _bot_permission_for_action(message.bot, bot_user.id, chat_id, requested_action)
    if not can_execute:
        await repositories.create_audit_log(
            session=session,
            chat_id=chat_id,
            actor_user_id=None,
            target_user_id=user_id,
            action="moderation_permission_denied",
            detail_json={"required_action": requested_action.value, "reason": denied_reason},
        )
        await repositories.create_punishment(
            session=session,
            violation_id=violation.id,
            chat_id=chat_id,
            user_id=user_id,
            action="none",
            duration_seconds=None,
            reason="bot_permission_denied",
            executed_by=None,
        )
        return ModerationOutcome(action_requested=requested_action, action_executed="none", duration_seconds=duration_seconds, delete_success=True, error=denied_reason)

    outcome = await _execute_action(message=message, action=requested_action, duration_seconds=duration_seconds)
    await repositories.create_punishment(
        session=session,
        violation_id=violation.id,
        chat_id=chat_id,
        user_id=user_id,
        action=outcome.action_executed,
        duration_seconds=duration_seconds,
        reason=reason,
        executed_by=None,
    )
    await repositories.increment_violation_stats(session, chat_id, user_id, outcome.action_executed)
    await repositories.create_audit_log(
        session=session,
        chat_id=chat_id,
        actor_user_id=None,
        target_user_id=user_id,
        action="moderation_auto_action",
        detail_json={
            "rule_name": primary_hit.rule_name,
            "category": primary_hit.category,
            "risk_level": primary_hit.risk_level.value,
            "trigger": primary_hit.trigger,
            "source": primary_hit.source,
            "action_requested": requested_action.value,
            "action_executed": outcome.action_executed,
            "delete_success": outcome.delete_success,
            "error": outcome.error,
        },
    )
    await audit_log.send_log(
        bot=message.bot,
        log_chat_id=log_chat_id,
        chat_id=chat_id,
        user_id=user_id,
        reason=reason,
        score=score,
        action=outcome.action_executed,
        excerpt=original_text,
    )
    return outcome
