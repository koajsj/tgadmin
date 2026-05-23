from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def home_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="选择群组（进入群管理）", callback_data="panel:groups")],
            [InlineKeyboardButton(text="运行状态（健康检查）", callback_data="panel:runtime")],
        ]
    )


def groups_keyboard(items: list[tuple[int, str]], page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for chat_id, title in items:
        label = f"{title} ({chat_id})"
        rows.append([InlineKeyboardButton(text=label[:60], callback_data=f"panel:g:{chat_id}")])
    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="上一页", callback_data=f"panel:groups:{page - 1}"))
    if page + 1 < total_pages:
        nav_row.append(InlineKeyboardButton(text="下一页", callback_data=f"panel:groups:{page + 1}"))
    if len(nav_row) > 0:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="返回首页", callback_data="panel:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def group_panel_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="规则设置（开关与模式）", callback_data=f"panel:menu:{chat_id}:rules")],
            [InlineKeyboardButton(text="关键词/域名（过滤词库）", callback_data=f"panel:menu:{chat_id}:keywords")],
            [InlineKeyboardButton(text="用户管理（警告/禁言/封禁）", callback_data=f"panel:menu:{chat_id}:users")],
            [InlineKeyboardButton(text="白黑名单（放行/拦截）", callback_data=f"panel:menu:{chat_id}:lists")],
            [InlineKeyboardButton(text="新人限制（观察期配置）", callback_data=f"panel:menu:{chat_id}:newcomer")],
            [InlineKeyboardButton(text="数据统计（近7天报表）", callback_data=f"panel:stats:{chat_id}")],
            [InlineKeyboardButton(text="审计导出（CSV/JSON）", callback_data=f"panel:menu:{chat_id}:export")],
            [InlineKeyboardButton(text="返回群列表", callback_data="panel:groups")],
            [InlineKeyboardButton(text="返回首页", callback_data="panel:home")],
        ]
    )


def rules_keyboard(
    chat_id: int,
    newcomer_enabled: bool,
    keyword_enabled: bool,
    link_enabled: bool,
    flood_enabled: bool,
    mode: str,
) -> InlineKeyboardMarkup:
    mode_label = "观察模式" if mode == "observe" else "安全模式(执行)"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"新人限制开关: {'开' if newcomer_enabled else '关'}", callback_data=f"panel:toggle:{chat_id}:newcomer")],
            [InlineKeyboardButton(text=f"关键词过滤开关: {'开' if keyword_enabled else '关'}", callback_data=f"panel:toggle:{chat_id}:keyword")],
            [InlineKeyboardButton(text=f"链接过滤开关: {'开' if link_enabled else '关'}", callback_data=f"panel:toggle:{chat_id}:link")],
            [InlineKeyboardButton(text=f"刷屏检测开关: {'开' if flood_enabled else '关'}", callback_data=f"panel:toggle:{chat_id}:flood")],
            [InlineKeyboardButton(text=f"处罚模式切换: {mode_label}", callback_data=f"panel:toggle:{chat_id}:mode")],
            [InlineKeyboardButton(text="返回群控制台", callback_data=f"panel:g:{chat_id}")],
            [InlineKeyboardButton(text="返回首页", callback_data="panel:home")],
        ]
    )


def export_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="导出 JSON（需确认）", callback_data=f"panel:expask:{chat_id}:json")],
            [InlineKeyboardButton(text="导出 CSV（需确认）", callback_data=f"panel:expask:{chat_id}:csv")],
            [InlineKeyboardButton(text="返回群控制台", callback_data=f"panel:g:{chat_id}")],
        ]
    )


def export_confirm_keyboard(chat_id: int, fmt: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="确认导出", callback_data=f"panel:expdo:{chat_id}:{fmt}")],
            [InlineKeyboardButton(text="取消", callback_data=f"panel:g:{chat_id}")],
        ]
    )


def back_home_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="返回群控制台", callback_data=f"panel:g:{chat_id}")],
            [InlineKeyboardButton(text="返回首页", callback_data="panel:home")],
        ]
    )
