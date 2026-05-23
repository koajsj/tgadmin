from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import Message

from bot.app_context import AppContext
from bot.database import repositories
from bot.database.session import session_scope
from bot.schemas.lexicon import ModerationAction, RiskLevel
from bot.schemas.moderation import RuleHit
from bot.services import lexicon_learning, moderation, moderation_service, onboarding, rule_engine
from bot.services.night_mode import is_night_window
from bot.utils.permissions import is_admin_for_chat


router = Router(name="messages")


@router.message(F.new_chat_members)
async def on_new_members(message: Message, app_context: AppContext) -> None:
    chat = message.chat
    members = message.new_chat_members
    if chat is None or members is None:
        return

    for member in members:
        async for session in session_scope(app_context.session_factory):
            await repositories.ensure_chat(
                session=session,
                chat_id=chat.id,
                title=chat.title,
                default_log_chat_id=app_context.settings.default_log_chat_id,
                newcomer_watch_seconds=app_context.settings.newcomer_watch_seconds,
            )
            await repositories.ensure_user(
                session=session,
                user_id=member.id,
                username=member.username,
                full_name=member.full_name,
                is_bot=member.is_bot,
                language_code=member.language_code,
            )
            await repositories.ensure_chat_member(
                session=session,
                chat_id=chat.id,
                user_id=member.id,
                joined_at=datetime.now(timezone.utc),
                is_newcomer=True,
            )
        await message.answer(f"欢迎 {member.full_name} 加入，请先阅读群规。")


def _build_repeat_hit() -> RuleHit:
    return RuleHit(
        rule_name="repeat_message",
        reason="repeat_message",
        score=55,
        is_link=False,
        is_keyword=False,
        is_flood=False,
        category="spam_repeat",
        risk_level=RiskLevel.MEDIUM,
        action=ModerationAction.DELETE,
        trigger="repeat",
        source="builtin_repeat_filter",
    )


def _build_mention_hit() -> RuleHit:
    return RuleHit(
        rule_name="mention_spam",
        reason="mention_spam",
        score=70,
        is_link=False,
        is_keyword=False,
        is_flood=False,
        category="spam_mentions",
        risk_level=RiskLevel.HIGH,
        action=ModerationAction.MUTE,
        trigger="many_mentions",
        source="builtin_mention_filter",
    )


@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def on_group_message(message: Message, app_context: AppContext) -> None:
    user = message.from_user
    chat = message.chat
    if user is None or chat is None or user.is_bot:
        return
    if user.id in app_context.settings.owner_ids:
        return

    text = message.text or message.caption or ""
    if text == "" and not onboarding.message_has_media(message):
        return

    async for session in session_scope(app_context.session_factory):
        chat_model = await repositories.ensure_chat(
            session=session,
            chat_id=chat.id,
            title=chat.title,
            default_log_chat_id=app_context.settings.default_log_chat_id,
            newcomer_watch_seconds=app_context.settings.newcomer_watch_seconds,
        )
        await repositories.ensure_user(
            session=session,
            user_id=user.id,
            username=user.username,
            full_name=user.full_name,
            is_bot=user.is_bot,
            language_code=user.language_code,
        )
        member = await repositories.ensure_chat_member(
            session=session,
            chat_id=chat.id,
            user_id=user.id,
            joined_at=None,
            is_newcomer=True,
        )
        await repositories.mark_member_first_message(session, member, datetime.now(timezone.utc))
        await repositories.increment_message_stats(session, chat.id, user.id)

        if await repositories.is_user_blacklisted(session, chat.id, user.id):
            _ = await moderation.try_delete_message(message)
            try:
                await moderation.ban_user(message.bot, chat.id, user.id)
                action = "ban"
                error = None
            except moderation.ModerationActionError as exc:
                action = "none"
                error = str(exc)
            await repositories.create_violation(
                session=session,
                chat_id=chat.id,
                user_id=user.id,
                message_id=message.message_id,
                rule_name="blacklist_user",
                reason="blacklist_user",
                content_excerpt=text[:200],
                score=100,
                rule_id=None,
            )
            await repositories.create_punishment(
                session=session,
                violation_id=violation.id,
                chat_id=chat.id,
                user_id=user.id,
                action=action,
                duration_seconds=None,
                reason="blacklist_user",
                executed_by=None,
            )
            await repositories.increment_violation_stats(session, chat.id, user.id, action)
            await repositories.create_audit_log(
                session=session,
                chat_id=chat.id,
                actor_user_id=None,
                target_user_id=user.id,
                action="blacklist_auto_action",
                detail_json={"action": action, "error": error},
            )
            return

        is_group_admin = await is_admin_for_chat(
            bot=message.bot,
            settings=app_context.settings,
            session_factory=app_context.session_factory,
            user_id=user.id,
            chat_id=chat.id,
        )
        is_whitelisted_user = await repositories.is_user_whitelisted(session, chat.id, user.id)
        protected_target = is_group_admin or is_whitelisted_user or user.id in app_context.settings.owner_ids
        if protected_target:
            return

        runtime_settings = repositories.get_chat_runtime_settings(chat_model)
        night_enabled = is_night_window(runtime_settings, datetime.now(timezone.utc))
        enforcement_mode = repositories.get_chat_enforcement_mode(chat_model)

        newcomer_reason = None
        if chat_model.newcomer_restrict_enabled:
            allow_links = chat_model.allow_links
            allow_media = chat_model.allow_media
            if night_enabled:
                night_mode = runtime_settings.get("night_mode")
                if isinstance(night_mode, dict):
                    if bool(night_mode.get("newcomer_links_blocked", True)):
                        allow_links = False
                    if bool(night_mode.get("newcomer_media_blocked", True)):
                        allow_media = False
            newcomer_reason = onboarding.newcomer_violation_reason(
                message=message,
                text=text,
                joined_at=member.joined_at,
                watch_seconds=chat_model.newcomer_watch_seconds,
                allow_links=allow_links,
                allow_media=allow_media,
            )
        if newcomer_reason is not None:
            hit = RuleHit(
                rule_name="newcomer_restriction",
                reason=newcomer_reason,
                score=45,
                is_link="link" in newcomer_reason,
                is_keyword=False,
                is_flood=False,
                category="newcomer",
                risk_level=RiskLevel.MEDIUM,
                action=ModerationAction.DELETE,
                trigger=newcomer_reason,
                source="builtin_newcomer_guard",
            )
            await moderation_service.handle_hits(
                message=message,
                session=session,
                settings=app_context.settings,
                chat_id=chat.id,
                user_id=user.id,
                log_chat_id=chat_model.log_chat_id,
                enforcement_mode=enforcement_mode,
                hits=[hit],
                original_text=text,
                protected_target=protected_target,
                allow_auto_ban=False,
            )
            return

        snapshot = app_context.keyword_store.get_snapshot()

        flood_window_seconds = app_context.settings.flood_window_seconds
        flood_max_messages = app_context.settings.flood_max_messages
        if night_enabled:
            night_mode = runtime_settings.get("night_mode")
            if isinstance(night_mode, dict):
                flood_window_seconds = int(night_mode.get("flood_window_seconds", flood_window_seconds))
                flood_max_messages = int(night_mode.get("flood_max_messages", flood_max_messages))

        hits = await rule_engine.evaluate_message(
            redis_client=app_context.redis,
            chat_id=chat.id,
            user_id=user.id,
            text=text,
            keywords=app_context.keyword_store.get_keywords(),
            keyword_score=60,
            link_score=35,
            flood_score=35,
            flood_window_seconds=flood_window_seconds,
            flood_max_messages=flood_max_messages,
            snapshot=snapshot,
        )

        repeated = await rule_engine.check_repeat_message(
            redis_client=app_context.redis,
            chat_id=chat.id,
            user_id=user.id,
            text=text,
            window_seconds=45,
            max_repeats=3,
        )
        if repeated:
            hits.append(_build_repeat_hit())

        if rule_engine.mention_count(text) >= 6:
            hits.append(_build_mention_hit())

        if len(hits) == 0:
            return

        outcome = await moderation_service.handle_hits(
            message=message,
            session=session,
            settings=app_context.settings,
            chat_id=chat.id,
            user_id=user.id,
            log_chat_id=chat_model.log_chat_id,
            enforcement_mode=enforcement_mode,
            hits=hits,
            original_text=text,
            protected_target=protected_target,
            allow_auto_ban=bool(runtime_settings.get("allow_auto_ban", False)),
        )
        if outcome.action_executed in {"delete", "warn", "mute", "ban", "kick"}:
            learning_result = await lexicon_learning.learn_from_message(
                redis_client=app_context.redis,
                keyword_store=app_context.keyword_store,
                text=text,
                hits=hits,
            )
            if len(learning_result.promoted_tokens) > 0:
                await repositories.create_audit_log(
                    session=session,
                    chat_id=chat.id,
                    actor_user_id=None,
                    target_user_id=user.id,
                    action="lexicon_auto_learned",
                    detail_json={
                        "promoted_tokens": list(learning_result.promoted_tokens),
                        "inspected_tokens": learning_result.inspected_tokens,
                        "reason_count": len(hits),
                    },
                )
