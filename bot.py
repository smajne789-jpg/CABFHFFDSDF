import asyncio
import html
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatMemberStatus, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from storage import Storage


logging.basicConfig(level=logging.INFO)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Не хватает переменной окружения: {name}")
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    return float(value.replace(",", "."))


def clean_amount(raw: str) -> float:
    return round(float(raw.replace(",", ".")), 8)


def fmt_amount(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text if text else "0"


def ce(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def premium_button_icon(slot: str) -> str | None:
    return PREMIUM_BUTTON_EMOJI_IDS.get(slot)


@dataclass
class Config:
    bot_token: str
    crypto_token: str
    admin_ids: set[int]
    logs_chat_id: int | None
    asset: str
    db_path: Path
    poll_interval: int
    crypto_base_url: str
    min_deposit: float
    min_withdraw: float
    auto_withdraw: bool
    withdraw_gate_enabled: bool
    withdraw_gate_amount: float
    darts_center_value: int
    darts_miss_value: int


def load_config() -> Config:
    admin_ids = {
        int(part.strip())
        for part in require_env("ADMIN_IDS").split(",")
        if part.strip()
    }
    logs_chat = os.getenv("LOGS_CHAT_ID")
    return Config(
        bot_token=require_env("BOT_TOKEN"),
        crypto_token=require_env("CRYPTO_PAY_API_TOKEN"),
        admin_ids=admin_ids,
        logs_chat_id=int(logs_chat) if logs_chat else None,
        asset=os.getenv("CRYPTO_PAY_ASSET", "USDT").upper(),
        db_path=Path(os.getenv("DB_PATH", "work/bot.sqlite")),
        poll_interval=int(os.getenv("INVOICE_POLL_INTERVAL", "20")),
        crypto_base_url=os.getenv("CRYPTO_PAY_BASE_URL", "https://pay.crypt.bot/api"),
        min_deposit=env_float("DEFAULT_MIN_DEPOSIT", 1.0),
        min_withdraw=env_float("DEFAULT_MIN_WITHDRAW", 1.0),
        auto_withdraw=env_bool("DEFAULT_AUTO_WITHDRAW", True),
        withdraw_gate_enabled=env_bool("DEFAULT_WITHDRAW_REQUIRES_DEPOSIT", False),
        withdraw_gate_amount=env_float("DEFAULT_WITHDRAW_REQUIRED_DEPOSIT_AMOUNT", 0.0),
        darts_center_value=int(os.getenv("DARTS_CENTER_VALUE", "6")),
        darts_miss_value=int(os.getenv("DARTS_MISS_VALUE", "1")),
    )


ICONS = {
    "home": ce("5291944933295406788", "🏠"),
    "profile": ce("5424972470023104089", "👤"),
    "wallet": ce("5291914649481007565", "💼"),
    "deposit": ce("5197434882321567830", "➕"),
    "withdraw": ce("5260379144167890225", "➖"),
    "games": ce("5267014542222723292", "🎮"),
    "dice": ce("5890971177484029249", "🎲"),
    "darts": ce("5310278924616356636", "🎯"),
    "admin": ce("5271912827869737544", "🛠"),
    "logs": ce("5271912827869737544", "📡"),
    "success": ce("5462919317832082236", "✅"),
    "error": ce("5210952531676504517", "❌"),
    "warning": ce("5210952531676504517", "⚠️"),
    "gift": ce("5312123810638483121", "🎁"),
    "money": ce("5312123810638483121", "💸"),
    "settings": ce("5312123810638483121", "⚙️"),
    "channel": ce("5229073750317612510", "📣"),
}

# Telegram supports custom emoji IDs in HTML message text, but not in inline keyboard button labels.
BUTTON_ICONS = {
    "home": "🏠",
    "profile": "👤",
    "profile_alt": "🪪",
    "wallet": "💼",
    "deposit": "💳",
    "withdraw": "💸",
    "games": "🎮",
    "dice": "🎲",
    "darts": "🎯",
    "admin": "🛠",
    "logs": "📡",
    "success": "✅",
    "error": "⛔",
    "warning": "⚠️",
    "gift": "🎁",
    "money_add": "💵",
    "money_remove": "💶",
    "settings_deposit": "📥",
    "settings_withdraw": "📤",
    "settings_auto": "♻️",
    "settings_gate": "🔐",
    "settings_gate_amount": "📏",
    "channel_add": "📣",
    "channel_remove": "🗑️",
}

PREMIUM_BUTTON_EMOJI_IDS = {
    "home": "5291944933295406788",
    "profile": "5258204546391351475",
    "wallet": "5291914649481007565",
    "deposit": "5197434882321567830",
    "withdraw": "5260379144167890225",
    "games": "5267014542222723292",
    "dice": "5890971177484029249",
    "darts": "5310278924616356636",
    "admin": "5271912827869737544",
    "logs": "5271912827869737544",
    "success": "5462919317832082236",
    "error": "5210952531676504517",
    "warning": "5210952531676504517",
    "gift": "5312123810638483121",
    "money": "5312123810638483121",
    "settings": "5312123810638483121",
    "channel": "5229073750317612510",
}


GAME_RULES = {
    "dice_even": {"title": "Куб чет", "multiplier": 2.0, "emoji": ICONS["dice"]},
    "dice_odd": {"title": "Куб нечет", "multiplier": 2.0, "emoji": ICONS["dice"]},
    "dice_product_18_plus": {"title": "Куб произведение 18+", "multiplier": 5.0, "emoji": ICONS["dice"]},
    "dice_sum_7": {"title": "Куб 7", "multiplier": 5.0, "emoji": ICONS["dice"]},
    "dice_sum_7_plus": {"title": "Куб 7+", "multiplier": 2.0, "emoji": ICONS["dice"]},
    "darts_center": {"title": "Дартс центр", "multiplier": 5.0, "emoji": ICONS["darts"]},
    "darts_miss": {"title": "Дартс мимо", "multiplier": 5.0, "emoji": ICONS["darts"]},
}


class CryptoPay:
    def __init__(self, token: str, base_url: str) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        self.session = aiohttp.ClientSession(
            headers={"Crypto-Pay-API-Token": self.token},
            timeout=aiohttp.ClientTimeout(total=30),
        )

    async def close(self) -> None:
        if self.session:
            await self.session.close()

    async def _request(self, method: str, payload: dict | None = None) -> dict:
        if not self.session:
            raise RuntimeError("CryptoPay session is not started")
        async with self.session.post(f"{self.base_url}/{method}", json=payload or {}) as response:
            data = await response.json()
            if not data.get("ok"):
                raise RuntimeError(data.get("error", "Crypto Pay API error"))
            return data["result"]

    async def create_invoice(self, asset: str, amount: float, description: str, payload: str) -> dict:
        return await self._request(
            "createInvoice",
            {
                "asset": asset,
                "amount": fmt_amount(amount),
                "description": description,
                "payload": payload,
                "allow_comments": False,
                "allow_anonymous": True,
            },
        )

    async def get_invoices(self, invoice_ids: list[int]) -> list[dict]:
        if not invoice_ids:
            return []
        return await self._request("getInvoices", {"invoice_ids": ",".join(map(str, invoice_ids))})

    async def create_check(self, asset: str, amount: float) -> dict:
        return await self._request("createCheck", {"asset": asset, "amount": fmt_amount(amount)})

    async def get_balance(self) -> list[dict]:
        return await self._request("getBalance")


class AdminStates(StatesGroup):
    waiting_deposit_amount = State()
    waiting_withdraw_amount = State()
    waiting_game_stake = State()
    waiting_check_amount = State()
    waiting_check_activations = State()
    waiting_check_deposit_required = State()
    waiting_add_channel = State()
    waiting_add_balance_username = State()
    waiting_add_balance_amount = State()
    waiting_remove_balance_username = State()
    waiting_remove_balance_amount = State()
    waiting_set_min_deposit = State()
    waiting_set_min_withdraw = State()
    waiting_set_withdraw_gate_amount = State()


router = Router()
config = load_config()
config.db_path.parent.mkdir(parents=True, exist_ok=True)
storage = Storage(config.db_path)
storage.ensure_defaults(
    {
        "min_deposit": str(config.min_deposit),
        "min_withdraw": str(config.min_withdraw),
        "auto_withdraw": "1" if config.auto_withdraw else "0",
        "withdraw_gate_enabled": "1" if config.withdraw_gate_enabled else "0",
        "withdraw_gate_amount": str(config.withdraw_gate_amount),
    }
)
crypto = CryptoPay(config.crypto_token, config.crypto_base_url)
bot_username = ""


def is_admin(user_id: int) -> bool:
    return user_id in config.admin_ids


def get_min_deposit() -> float:
    return float(storage.get_setting("min_deposit", "1") or "1")


def get_min_withdraw() -> float:
    return float(storage.get_setting("min_withdraw", "1") or "1")


def auto_withdraw_enabled() -> bool:
    return (storage.get_setting("auto_withdraw", "1") or "1") == "1"


def withdraw_gate_enabled() -> bool:
    return (storage.get_setting("withdraw_gate_enabled", "0") or "0") == "1"


def get_withdraw_gate_amount() -> float:
    return float(storage.get_setting("withdraw_gate_amount", "0") or "0")


async def log_action(text: str) -> None:
    if not config.logs_chat_id:
        return
    try:
        await bot.send_message(config.logs_chat_id, text)
    except Exception as exc:  # noqa: BLE001
        logging.warning("Не удалось отправить лог: %s", exc)


def main_menu(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=f"{BUTTON_ICONS['profile_alt']} Профиль", callback_data="menu:profile")
    builder.button(text=f"{BUTTON_ICONS['games']} Играть", callback_data="menu:games")
    builder.button(text=f"{BUTTON_ICONS['deposit']} Пополнить", callback_data="menu:deposit")
    builder.button(text=f"{BUTTON_ICONS['withdraw']} Вывести", callback_data="menu:withdraw")
    if is_admin(user_id):
        builder.button(text=f"{BUTTON_ICONS['admin']} Админ-панель", callback_data="menu:admin")
    builder.adjust(2, 2, 1)
    return builder.as_markup()


def main_reply_menu(user_id: int) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.row(
        KeyboardButton(text="Профиль", icon_custom_emoji_id=premium_button_icon("profile")),
        KeyboardButton(text="Играть", icon_custom_emoji_id=premium_button_icon("games")),
    )
    if is_admin(user_id):
        builder.row(KeyboardButton(text="Админ-панель", icon_custom_emoji_id=premium_button_icon("admin")))
    return builder.as_markup(resize_keyboard=True, is_persistent=True)


def games_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Кубы", callback_data="games:dice", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Дартс", callback_data="games:darts", icon_custom_emoji_id=premium_button_icon("darts"))
    builder.button(text="Назад", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))
    builder.adjust(1)
    return builder.as_markup()


def dice_games_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Куб чет x2", callback_data="game:dice_even", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Куб нечет x2", callback_data="game:dice_odd", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Куб произведение 18+ x5", callback_data="game:dice_product_18_plus", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Куб 7 x5", callback_data="game:dice_sum_7", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Куб 7+ x2", callback_data="game:dice_sum_7_plus", icon_custom_emoji_id=premium_button_icon("dice"))
    builder.button(text="Назад", callback_data="menu:games", icon_custom_emoji_id=premium_button_icon("home"))
    builder.adjust(1)
    return builder.as_markup()


def darts_games_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Дартс центр x5", callback_data="game:darts_center", icon_custom_emoji_id=premium_button_icon("darts"))
    builder.button(text="Дартс мимо x5", callback_data="game:darts_miss", icon_custom_emoji_id=premium_button_icon("darts"))
    builder.button(text="Назад", callback_data="menu:games", icon_custom_emoji_id=premium_button_icon("home"))
    builder.adjust(1)
    return builder.as_markup()


def profile_actions_menu(user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Пополнить", callback_data="menu:deposit", icon_custom_emoji_id=premium_button_icon("deposit"))
    builder.button(text="Вывести", callback_data="menu:withdraw", icon_custom_emoji_id=premium_button_icon("withdraw"))
    builder.button(text="Статистика", callback_data="menu:stats", icon_custom_emoji_id=premium_button_icon("wallet"))
    builder.button(text="В меню", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))
    if is_admin(user_id):
        builder.button(text="Админ-панель", callback_data="menu:admin", icon_custom_emoji_id=premium_button_icon("admin"))
    builder.adjust(2, 1, 1, 1)
    return builder.as_markup()


def game_result_menu(game_key: str) -> InlineKeyboardMarkup:
    if game_key.startswith("darts_"):
        return darts_games_menu()
    return dice_games_menu()


def render_game_result_text(
    *,
    title: str,
    amount: float,
    multiplier: float,
    details: str,
    won: bool,
    payout: float,
    balance: float,
) -> str:
    status_icon = ICONS["success"] if won else ICONS["error"]
    status_text = "Выигрыш" if won else "Проигрыш"
    payout_line = (
        f"{ICONS['money']} Выплата: <b>{fmt_amount(payout)} {config.asset}</b>\n"
        if won
        else ""
    )
    return (
        f"{status_icon} <b>{title}</b>\n\n"
        f"{ICONS['wallet']} Ставка: <b>{fmt_amount(amount)} {config.asset}</b>\n"
        f"{ICONS['games']} Коэффициент: <b>x{multiplier}</b>\n"
        f"{details}\n\n"
        f"{status_icon} <b>{status_text}</b>\n"
        f"{payout_line}"
        f"{ICONS['wallet']} Баланс: <b>{fmt_amount(balance)} {config.asset}</b>"
    )


def admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Создать чек", callback_data="admin:create_check", icon_custom_emoji_id=premium_button_icon("gift"))
    builder.button(text="Добавить канал", callback_data="admin:add_channel", icon_custom_emoji_id=premium_button_icon("channel"))
    builder.button(text="Удалить канал", callback_data="admin:remove_channel", icon_custom_emoji_id=premium_button_icon("channel"))
    builder.button(text="+Баланс", callback_data="admin:add_balance", icon_custom_emoji_id=premium_button_icon("money"))
    builder.button(text="-Баланс", callback_data="admin:remove_balance", icon_custom_emoji_id=premium_button_icon("money"))
    builder.button(text="Мин. депозит", callback_data="admin:set_min_deposit", icon_custom_emoji_id=premium_button_icon("settings"))
    builder.button(text="Мин. вывод", callback_data="admin:set_min_withdraw", icon_custom_emoji_id=premium_button_icon("settings"))
    builder.button(text="Авто вывод", callback_data="admin:toggle_auto_withdraw", icon_custom_emoji_id=premium_button_icon("settings"))
    builder.button(text="Вывод с депозитом", callback_data="admin:toggle_withdraw_gate", icon_custom_emoji_id=premium_button_icon("settings"))
    builder.button(text="Сумма депозита для вывода", callback_data="admin:set_withdraw_gate_amount", icon_custom_emoji_id=premium_button_icon("settings"))
    builder.button(text="Заявки на вывод", callback_data="admin:list_withdrawals", icon_custom_emoji_id=premium_button_icon("wallet"))
    builder.button(text="Назад", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))
    builder.adjust(2)
    return builder.as_markup()


def withdrawal_actions(withdrawal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Подтвердить", callback_data=f"withdraw:approve:{withdrawal_id}", icon_custom_emoji_id=premium_button_icon("success"))
    builder.button(text="Отклонить", callback_data=f"withdraw:reject:{withdrawal_id}", icon_custom_emoji_id=premium_button_icon("error"))
    builder.adjust(2)
    return builder.as_markup()


def back_to_home() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))]]
    )


async def ensure_user(message_or_query: Message | CallbackQuery) -> int:
    user = message_or_query.from_user
    if not user:
        raise RuntimeError("User not found")
    storage.upsert_user(user.id, user.username, user.first_name)
    return user.id


async def get_profile_text(user_id: int) -> str:
    user = storage.get_user(user_id)
    if not user:
        return f"{ICONS['error']} Профиль не найден."
    return (
        f"{ICONS['profile']} <b>Профиль</b>\n\n"
        f"{ICONS['wallet']} Баланс: <b>{fmt_amount(user['balance'])} {config.asset}</b>\n"
        f"{ICONS['settings']} Мин. депозит: <b>{fmt_amount(get_min_deposit())} {config.asset}</b>\n"
        f"{ICONS['settings']} Мин. вывод: <b>{fmt_amount(get_min_withdraw())} {config.asset}</b>\n\n"
        f"{ICONS['wallet']} Всё остальное смотри в статистике ниже."
    )


async def get_stats_text(user_id: int) -> str:
    user = storage.get_user(user_id)
    if not user:
        return f"{ICONS['error']} Статистика не найдена."
    profit = round(float(user["balance"]) + float(user["total_withdraw"]) - float(user["total_deposit"]), 8)
    profit_icon = ICONS["success"] if profit >= 0 else ICONS["error"]
    return (
        f"{ICONS['wallet']} <b>Статистика</b>\n\n"
        f"{ICONS['deposit']} Пополнено: <b>{fmt_amount(user['total_deposit'])} {config.asset}</b>\n"
        f"{ICONS['withdraw']} Выведено: <b>{fmt_amount(user['total_withdraw'])} {config.asset}</b>\n"
        f"{ICONS['wallet']} Текущий баланс: <b>{fmt_amount(user['balance'])} {config.asset}</b>\n"
        f"{profit_icon} Плюс/минус: <b>{fmt_amount(profit)} {config.asset}</b>"
    )


async def check_required_subscriptions(user_id: int) -> tuple[bool, list[str]]:
    channels = storage.list_force_channels()
    missing: list[str] = []
    for channel in channels:
        try:
            member = await bot.get_chat_member(channel["chat_id"], user_id)
            if member.status in {
                ChatMemberStatus.LEFT,
                ChatMemberStatus.KICKED,
            }:
                title = channel["title"] or str(channel["chat_id"])
                link = channel["invite_link"] or ""
                missing.append(f"{title} {link}".strip())
        except TelegramBadRequest:
            title = channel["title"] or str(channel["chat_id"])
            link = channel["invite_link"] or ""
            missing.append(f"{title} {link}".strip())
    return (len(missing) == 0, missing)


async def enforce_subscription(target: Message | CallbackQuery) -> bool:
    user = target.from_user
    if not user:
        return False
    ok, missing = await check_required_subscriptions(user.id)
    if ok:
        return True
    text = (
        f"{ICONS['warning']} <b>Доступ закрыт</b>\n\n"
        "Подпишись на все обязательные каналы, потом нажми /start:\n\n"
        + "\n".join(f"• {html.escape(item)}" for item in missing)
    )
    if isinstance(target, CallbackQuery):
        await target.answer("Сначала подпишись на обязательные каналы.", show_alert=True)
        await target.message.answer(text, reply_markup=back_to_home())
    else:
        await target.answer(text, reply_markup=back_to_home())
    return False


async def show_home(target: Message | CallbackQuery, user_id: int) -> None:
    text = (
        f"{ICONS['home']} <b>Главное меню</b>\n\n"
        f"{ICONS['profile']} Открой профиль, чтобы пополнить баланс или вывести средства.\n"
        f"{ICONS['games']} Открой раздел игр, чтобы играть в кубы и дартс на реальных Telegram бросках."
    )
    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text)
        await target.message.answer(
            f"{ICONS['home']} Нижнее меню обновлено.",
            reply_markup=main_reply_menu(user_id),
        )
        await target.answer()
    else:
        await target.answer(text, reply_markup=main_reply_menu(user_id))


async def create_withdraw_check(amount: float) -> tuple[bool, str, int | None, str | None]:
    try:
        balances = await crypto.get_balance()
        asset_balance = next((item for item in balances if item["currency_code"] == config.asset), None)
        available = float(asset_balance["available"]) if asset_balance else 0.0
        if available < amount:
            return False, "Нету казны для выплаты.", None, None
        check = await crypto.create_check(config.asset, amount)
        return True, "ok", int(check["check_id"]), check["bot_check_url"]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc), None, None


async def settle_invoice(invoice: dict) -> None:
    stored = storage.mark_invoice_paid(int(invoice["invoice_id"]))
    if not stored or stored["status"] != "paid":
        return
    user = storage.get_user(stored["user_id"])
    if not user:
        return
    storage.add_balance(stored["user_id"], float(stored["amount"]))
    storage.add_deposit_total(stored["user_id"], float(stored["amount"]))
    await bot.send_message(
        stored["user_id"],
        (
            f"{ICONS['success']} <b>Пополнение зачислено</b>\n\n"
            f"Сумма: <b>{fmt_amount(stored['amount'])} {config.asset}</b>\n"
            f"Новый баланс: <b>{fmt_amount(storage.get_user(stored['user_id'])['balance'])} {config.asset}</b>"
        ),
        reply_markup=back_to_home(),
    )
    await log_action(
        f"{ICONS['deposit']} <b>Депозит оплачен</b>\n"
        f"User: @{html.escape(user['username'] or 'no_username')} | <code>{stored['user_id']}</code>\n"
        f"Amount: <b>{fmt_amount(stored['amount'])} {config.asset}</b>\n"
        f"Invoice: <code>{stored['invoice_id']}</code>"
    )


async def poll_invoices() -> None:
    while True:
        try:
            active = storage.get_active_invoices()
            if active:
                invoices = await crypto.get_invoices([row["invoice_id"] for row in active])
                for invoice in invoices:
                    if invoice.get("status") == "paid":
                        await settle_invoice(invoice)
        except Exception as exc:  # noqa: BLE001
            logging.warning("Ошибка опроса инвойсов: %s", exc)
        await asyncio.sleep(config.poll_interval)


async def play_game(chat_id: int, user_id: int, game_key: str, stake: float) -> None:
    user = storage.get_user(user_id)
    if not user or user["balance"] < stake:
        await bot.send_message(chat_id, f"{ICONS['error']} Недостаточно баланса.", reply_markup=game_result_menu(game_key))
        return
    storage.subtract_balance(user_id, stake)
    rule = GAME_RULES[game_key]
    details = ""
    win = False
    payout = 0.0

    if game_key in {"dice_even", "dice_odd"}:
        dice_message = await bot.send_dice(chat_id, emoji="🎲")
        value = dice_message.dice.value
        win = (value % 2 == 0) if game_key == "dice_even" else (value % 2 == 1)
        details = f"{ICONS['dice']} Выпало: <b>{value}</b>"
    elif game_key in {"dice_product_18_plus", "dice_sum_7", "dice_sum_7_plus"}:
        first = await bot.send_dice(chat_id, emoji="🎲")
        second = await bot.send_dice(chat_id, emoji="🎲")
        v1 = first.dice.value
        v2 = second.dice.value
        product = v1 * v2
        total = v1 + v2
        if game_key == "dice_product_18_plus":
            win = product > 18
            details = (
                f"{ICONS['dice']} Кубы: <b>{v1}</b> и <b>{v2}</b>\n"
                f"{ICONS['wallet']} Сумма: <b>{total}</b>\n"
                f"{ICONS['games']} Произведение: <b>{product}</b>"
            )
        elif game_key == "dice_sum_7":
            win = total == 7
            details = f"{ICONS['dice']} Кубы: <b>{v1}</b> и <b>{v2}</b>\n{ICONS['wallet']} Сумма: <b>{total}</b>"
        else:
            win = total > 7
            details = f"{ICONS['dice']} Кубы: <b>{v1}</b> и <b>{v2}</b>\n{ICONS['wallet']} Сумма: <b>{total}</b>"
    elif game_key in {"darts_center", "darts_miss"}:
        dart_message = await bot.send_dice(chat_id, emoji="🎯")
        value = dart_message.dice.value
        if game_key == "darts_center":
            win = value == config.darts_center_value
        else:
            win = value == config.darts_miss_value
        details = f"{ICONS['darts']} Результат дартса: <b>{value}</b>"

    if win:
        payout = round(stake * rule["multiplier"], 8)
        storage.add_balance(user_id, payout)
    balance_now = storage.get_user(user_id)["balance"]
    storage.create_game(
        user_id=user_id,
        game_key=game_key,
        stake=stake,
        multiplier=rule["multiplier"],
        result_value=details,
        win=win,
        payout=payout,
    )
    await bot.send_message(
        chat_id,
        render_game_result_text(
            title=rule["title"],
            amount=stake,
            multiplier=rule["multiplier"],
            details=details,
            won=win,
            payout=payout,
            balance=balance_now,
        ),
        reply_markup=game_result_menu(game_key),
    )
    await log_action(
        f"{rule['emoji']} <b>Ставка</b>\n"
        f"User: <code>{user_id}</code>\n"
        f"Game: <b>{html.escape(rule['title'])}</b>\n"
        f"Stake: <b>{fmt_amount(stake)} {config.asset}</b>\n"
        f"Result: <b>{'WIN' if win else 'LOSE'}</b>\n"
        f"Payout: <b>{fmt_amount(payout)} {config.asset}</b>"
    )


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    await state.clear()
    if not await enforce_subscription(message):
        return
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("check_"):
        token = args[1][6:]
        check = storage.get_promo_check(token)
        if not check:
            await message.answer(f"{ICONS['error']} Чек не найден.", reply_markup=main_reply_menu(user_id))
            return
        user = storage.get_user(user_id)
        needed = float(check["deposit_required"])
        if needed > 0 and user["total_deposit"] < needed:
            await message.answer(
                (
                    f"{ICONS['warning']} <b>Чек на {fmt_amount(check['amount'])}$</b>\n\n"
                    f"Чтобы активировать его, нужно совершить депозит "
                    f"<b>{fmt_amount(needed)}$</b>."
                ),
                reply_markup=main_reply_menu(user_id),
            )
            return
        ok, reason = storage.activate_promo_check(token, user_id)
        if ok:
            updated = storage.get_user(user_id)
            await message.answer(
                (
                    f"{ICONS['success']} Чек активирован.\n\n"
                    f"Начислено: <b>{fmt_amount(check['amount'])} {config.asset}</b>\n"
                    f"Баланс: <b>{fmt_amount(updated['balance'])} {config.asset}</b>"
                ),
                reply_markup=main_reply_menu(user_id),
            )
            await log_action(
                f"{ICONS['gift']} <b>Активирован промо-чек</b>\n"
                f"User: <code>{user_id}</code>\n"
                f"Token: <code>{html.escape(token)}</code>\n"
                f"Amount: <b>{fmt_amount(check['amount'])} {config.asset}</b>"
            )
            return
        await message.answer(f"{ICONS['error']} {reason}", reply_markup=main_reply_menu(user_id))
        return

    await log_action(
        f"{ICONS['home']} <b>Вход в бота</b>\n"
        f"User: @{html.escape(message.from_user.username or 'no_username')} | <code>{user_id}</code>"
    )
    await show_home(message, user_id)


@router.message(Command("cancel"))
async def cancel_handler(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = await ensure_user(message)
    await message.answer(f"{ICONS['warning']} Действие отменено.", reply_markup=main_reply_menu(user_id))


@router.message(F.text == "Профиль")
async def profile_button_handler(message: Message) -> None:
    user_id = await ensure_user(message)
    if not await enforce_subscription(message):
        return
    await message.answer(await get_profile_text(user_id), reply_markup=profile_actions_menu(user_id))


@router.message(F.text == "Играть")
async def games_button_handler(message: Message) -> None:
    await ensure_user(message)
    if not await enforce_subscription(message):
        return
    await message.answer(
        (
            f"{ICONS['games']} <b>Раздел Играть</b>\n\n"
            "Выбери нужный раздел ниже."
        ),
        reply_markup=games_menu(),
    )


@router.message(F.text == "Админ-панель")
async def admin_button_handler(message: Message) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    await message.answer(
        (
            f"{ICONS['admin']} <b>Админ-панель</b>\n\n"
            f"Автовывод: <b>{'включен' if auto_withdraw_enabled() else 'выключен'}</b>\n"
            f"Вывод с депозитом: <b>{'включен' if withdraw_gate_enabled() else 'выключен'}</b>\n"
            f"Мин. депозит: <b>{fmt_amount(get_min_deposit())} {config.asset}</b>\n"
            f"Мин. вывод: <b>{fmt_amount(get_min_withdraw())} {config.asset}</b>"
        ),
        reply_markup=admin_menu(),
    )


@router.callback_query(F.data == "menu:home")
async def menu_home_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    await state.clear()
    if not await enforce_subscription(query):
        return
    await show_home(query, user_id)


@router.callback_query(F.data == "menu:profile")
async def profile_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await query.message.edit_text(await get_profile_text(user_id), reply_markup=profile_actions_menu(user_id))
    await query.answer()


@router.callback_query(F.data == "menu:stats")
async def stats_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await query.message.edit_text(await get_stats_text(user_id), reply_markup=profile_actions_menu(user_id))
    await query.answer()


@router.callback_query(F.data == "menu:games")
async def games_handler(query: CallbackQuery) -> None:
    await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await query.message.edit_text(
        (
            f"{ICONS['games']} <b>Раздел Играть</b>\n\n"
            "Выбери нужный раздел ниже."
        ),
        reply_markup=games_menu(),
    )
    await query.answer()


@router.callback_query(F.data == "games:dice")
async def games_dice_handler(query: CallbackQuery) -> None:
    await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await query.message.edit_text(
        (
            f"{ICONS['dice']} <b>Раздел Кубы</b>\n\n"
            "Выбери игру на кубах."
        ),
        reply_markup=dice_games_menu(),
    )
    await query.answer()


@router.callback_query(F.data == "games:darts")
async def games_darts_handler(query: CallbackQuery) -> None:
    await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await query.message.edit_text(
        (
            f"{ICONS['darts']} <b>Раздел Дартс</b>\n\n"
            "Выбери игру на дартсе."
        ),
        reply_markup=darts_games_menu(),
    )
    await query.answer()


@router.callback_query(F.data.startswith("game:"))
async def game_choice_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not await enforce_subscription(query):
        return
    game_key = query.data.split(":", maxsplit=1)[1]
    await state.set_state(AdminStates.waiting_game_stake)
    await state.update_data(game_key=game_key)
    await query.message.answer(
        (
            f"{ICONS['games']} Игра: <b>{GAME_RULES[game_key]['title']}</b>\n"
            f"Отправь сумму ставки в {config.asset}."
        ),
        reply_markup=back_to_home(),
    )
    await query.answer()
    await log_action(
        f"{ICONS['games']} <b>Открыта игра</b>\nUser: <code>{user_id}</code>\nGame: <b>{html.escape(GAME_RULES[game_key]['title'])}</b>"
    )


@router.message(AdminStates.waiting_game_stake)
async def game_stake_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not await enforce_subscription(message):
        return
    try:
        stake = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    if stake <= 0:
        await message.answer(f"{ICONS['error']} Ставка должна быть больше 0.")
        return
    data = await state.get_data()
    await state.clear()
    await play_game(message.chat.id, user_id, data["game_key"], stake)


@router.callback_query(F.data == "menu:deposit")
async def deposit_menu_handler(query: CallbackQuery, state: FSMContext) -> None:
    await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await state.set_state(AdminStates.waiting_deposit_amount)
    await query.message.answer(
        (
            f"{ICONS['deposit']} Отправь сумму депозита.\n"
            f"Минимум: <b>{fmt_amount(get_min_deposit())} {config.asset}</b>"
        ),
        reply_markup=back_to_home(),
    )
    await query.answer()


@router.message(AdminStates.waiting_deposit_amount)
async def deposit_amount_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not await enforce_subscription(message):
        return
    try:
        amount = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    if amount < get_min_deposit():
        await message.answer(
            f"{ICONS['error']} Минимальный депозит: <b>{fmt_amount(get_min_deposit())} {config.asset}</b>."
        )
        return
    try:
        invoice = await crypto.create_invoice(
            asset=config.asset,
            amount=amount,
            description=f"Deposit for user {user_id}",
            payload=str(user_id),
        )
        storage.create_invoice(int(invoice["invoice_id"]), user_id, amount, config.asset, invoice["bot_invoice_url"])
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Оплатить", url=invoice["bot_invoice_url"], icon_custom_emoji_id=premium_button_icon("deposit"))],
                [InlineKeyboardButton(text="В меню", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))],
            ]
        )
        await state.clear()
        await message.answer(
            (
                f"{ICONS['deposit']} <b>Инвойс создан</b>\n\n"
                f"Сумма: <b>{fmt_amount(amount)} {config.asset}</b>\n"
                "После оплаты бот зачислит депозит автоматически."
            ),
            reply_markup=keyboard,
        )
        await log_action(
            f"{ICONS['deposit']} <b>Создан инвойс</b>\nUser: <code>{user_id}</code>\n"
            f"Amount: <b>{fmt_amount(amount)} {config.asset}</b>\nInvoice: <code>{invoice['invoice_id']}</code>"
        )
    except Exception as exc:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Не удалось создать инвойс: <code>{html.escape(str(exc))}</code>")


@router.callback_query(F.data == "menu:withdraw")
async def withdraw_menu_handler(query: CallbackQuery, state: FSMContext) -> None:
    await ensure_user(query)
    if not await enforce_subscription(query):
        return
    await state.set_state(AdminStates.waiting_withdraw_amount)
    gate_text = ""
    if withdraw_gate_enabled():
        gate_text = (
            f"\nТребуется общий депозит: <b>{fmt_amount(get_withdraw_gate_amount())} {config.asset}</b>"
        )
    await query.message.answer(
        (
            f"{ICONS['withdraw']} Отправь сумму вывода.\n"
            f"Минимум: <b>{fmt_amount(get_min_withdraw())} {config.asset}</b>"
            f"{gate_text}"
        ),
        reply_markup=back_to_home(),
    )
    await query.answer()


@router.message(AdminStates.waiting_withdraw_amount)
async def withdraw_amount_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not await enforce_subscription(message):
        return
    user = storage.get_user(user_id)
    try:
        amount = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    if amount < get_min_withdraw():
        await message.answer(
            f"{ICONS['error']} Минимальный вывод: <b>{fmt_amount(get_min_withdraw())} {config.asset}</b>."
        )
        return
    if user["balance"] < amount:
        await message.answer(f"{ICONS['error']} Недостаточно баланса.")
        return
    if withdraw_gate_enabled() and user["total_deposit"] < get_withdraw_gate_amount():
        await message.answer(
            (
                f"{ICONS['error']} Вывод доступен только после депозита "
                f"от <b>{fmt_amount(get_withdraw_gate_amount())} {config.asset}</b>."
            )
        )
        return

    await state.clear()
    if auto_withdraw_enabled():
        ok, reason, check_id, check_url = await create_withdraw_check(amount)
        if not ok or not check_url:
            await message.answer(f"{ICONS['error']} {reason}", reply_markup=back_to_home())
            await log_action(
                f"{ICONS['error']} <b>Автовывод не выполнен</b>\nUser: <code>{user_id}</code>\n"
                f"Amount: <b>{fmt_amount(amount)} {config.asset}</b>\nReason: <code>{html.escape(reason)}</code>"
            )
            return
        storage.subtract_balance(user_id, amount)
        storage.add_withdraw_total(user_id, amount)
        withdrawal_id = storage.create_withdrawal(user_id, amount, config.asset, "paid", note="auto")
        storage.update_withdrawal(withdrawal_id, "paid", check_id=check_id, check_url=check_url, note="auto")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Забрать чек", url=check_url, icon_custom_emoji_id=premium_button_icon("wallet"))],
                [InlineKeyboardButton(text="В меню", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))],
            ]
        )
        await message.answer(
            (
                f"{ICONS['success']} <b>Вывод выполнен</b>\n\n"
                f"Сумма: <b>{fmt_amount(amount)} {config.asset}</b>\n"
                "Нажми кнопку ниже, чтобы забрать чек."
            ),
            reply_markup=keyboard,
        )
        await log_action(
            f"{ICONS['withdraw']} <b>Автовывод выполнен</b>\nUser: <code>{user_id}</code>\n"
            f"Amount: <b>{fmt_amount(amount)} {config.asset}</b>\nWithdrawal: <code>{withdrawal_id}</code>"
        )
        return

    storage.subtract_balance(user_id, amount)
    withdrawal_id = storage.create_withdrawal(user_id, amount, config.asset, "pending", note="manual")
    await message.answer(
        (
            f"{ICONS['warning']} <b>Заявка на вывод создана</b>\n\n"
            f"Сумма: <b>{fmt_amount(amount)} {config.asset}</b>\n"
            "Ожидай решение администратора."
        ),
        reply_markup=back_to_home(),
    )
    await log_action(
        f"{ICONS['withdraw']} <b>Новая заявка на вывод</b>\nUser: <code>{user_id}</code>\n"
        f"Amount: <b>{fmt_amount(amount)} {config.asset}</b>\nWithdrawal: <code>{withdrawal_id}</code>"
    )
    for admin_id in config.admin_ids:
        await bot.send_message(
            admin_id,
            (
                f"{ICONS['withdraw']} <b>Заявка на вывод</b>\n\n"
                f"ID: <code>{withdrawal_id}</code>\n"
                f"User: <code>{user_id}</code>\n"
                f"Amount: <b>{fmt_amount(amount)} {config.asset}</b>"
            ),
            reply_markup=withdrawal_actions(withdrawal_id),
        )


@router.callback_query(F.data == "menu:admin")
async def admin_panel_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await query.message.edit_text(
        (
            f"{ICONS['admin']} <b>Админ-панель</b>\n\n"
            f"Автовывод: <b>{'включен' if auto_withdraw_enabled() else 'выключен'}</b>\n"
            f"Вывод с депозитом: <b>{'включен' if withdraw_gate_enabled() else 'выключен'}</b>\n"
            f"Мин. депозит: <b>{fmt_amount(get_min_deposit())} {config.asset}</b>\n"
            f"Мин. вывод: <b>{fmt_amount(get_min_withdraw())} {config.asset}</b>"
        ),
        reply_markup=admin_menu(),
    )
    await query.answer()


@router.callback_query(F.data == "admin:create_check")
async def admin_create_check_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_check_amount)
    await query.message.answer(
        f"{ICONS['gift']} Введи сумму админ-чека в {config.asset}.",
        reply_markup=back_to_home(),
    )
    await query.answer()


@router.message(AdminStates.waiting_check_amount)
async def admin_check_amount_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    try:
        amount = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    if amount <= 0:
        await message.answer(f"{ICONS['error']} Сумма должна быть больше 0.")
        return
    await state.update_data(check_amount=amount)
    await state.set_state(AdminStates.waiting_check_activations)
    await message.answer("Введи количество активаций для этого чека.")


@router.message(AdminStates.waiting_check_activations)
async def admin_check_activations_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    try:
        activations = int(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи целое число.")
        return
    if activations <= 0:
        await message.answer(f"{ICONS['error']} Количество активаций должно быть больше 0.")
        return
    await state.update_data(check_activations=activations)
    await state.set_state(AdminStates.waiting_check_deposit_required)
    await message.answer(
        (
            "Нужен ли депозит для активации?\n"
            "Отправь сумму обязательного депозита или 0 если не нужен."
        )
    )


@router.message(AdminStates.waiting_check_deposit_required)
async def admin_check_deposit_required_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    try:
        deposit_required = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    data = await state.get_data()
    await state.clear()
    token = storage.create_promo_check(
        amount=float(data["check_amount"]),
        activations_total=int(data["check_activations"]),
        deposit_required=deposit_required,
        created_by=user_id,
    )
    link = f"https://t.me/{bot_username}?start=check_{token}"
    await message.answer(
        (
            f"{ICONS['success']} <b>Чек создан</b>\n\n"
            f"Сумма: <b>{fmt_amount(data['check_amount'])} {config.asset}</b>\n"
            f"Активации: <b>{data['check_activations']}</b>\n"
            f"Депозит для активации: <b>{fmt_amount(deposit_required)} {config.asset}</b>\n"
            f"Ссылка:\n{link}"
        ),
        reply_markup=admin_menu(),
    )
    await log_action(
        f"{ICONS['gift']} <b>Админ создал чек</b>\nAdmin: <code>{user_id}</code>\n"
        f"Amount: <b>{fmt_amount(data['check_amount'])} {config.asset}</b>\n"
        f"Activations: <b>{data['check_activations']}</b>\n"
        f"Deposit required: <b>{fmt_amount(deposit_required)} {config.asset}</b>\n"
        f"Link: {html.escape(link)}"
    )


@router.callback_query(F.data == "admin:add_channel")
async def admin_add_channel_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_add_channel)
    await query.message.answer(
        (
            f"{ICONS['channel']} Отправь данные канала одной строкой:\n"
            "<code>chat_id | название | ссылка_или_invite_link</code>"
        ),
        reply_markup=back_to_home(),
    )
    await query.answer()


@router.message(AdminStates.waiting_add_channel)
async def admin_add_channel_message_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    try:
        raw_chat_id, title, link = [part.strip() for part in message.text.split("|", maxsplit=2)]
        chat_id = int(raw_chat_id)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Формат неверный. Используй: chat_id | название | ссылка")
        return
    storage.add_force_channel(chat_id, title, link)
    await state.clear()
    await message.answer(f"{ICONS['success']} Канал добавлен.", reply_markup=admin_menu())
    await log_action(
        f"{ICONS['channel']} <b>Добавлен канал подписки</b>\nAdmin: <code>{user_id}</code>\n"
        f"Chat ID: <code>{chat_id}</code>\nTitle: <b>{html.escape(title)}</b>"
    )


@router.callback_query(F.data == "admin:remove_channel")
async def admin_remove_channel_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    channels = storage.list_force_channels()
    if not channels:
        await query.answer("Список пуст.", show_alert=True)
        return
    builder = InlineKeyboardBuilder()
    for channel in channels:
        title = channel["title"] or str(channel["chat_id"])
        builder.button(text=title, callback_data=f"admin:remove_channel:{channel['chat_id']}", icon_custom_emoji_id=premium_button_icon("channel"))
    builder.button(text="Назад", callback_data="menu:admin", icon_custom_emoji_id=premium_button_icon("home"))
    builder.adjust(1)
    await query.message.edit_text("Выбери канал для удаления.", reply_markup=builder.as_markup())
    await query.answer()


@router.callback_query(F.data.startswith("admin:remove_channel:"))
async def admin_remove_channel_confirm_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    chat_id = int(query.data.rsplit(":", maxsplit=1)[1])
    storage.remove_force_channel(chat_id)
    await query.message.edit_text(f"{ICONS['success']} Канал удален.", reply_markup=admin_menu())
    await query.answer()
    await log_action(
        f"{ICONS['channel']} <b>Удален канал подписки</b>\nAdmin: <code>{user_id}</code>\nChat ID: <code>{chat_id}</code>"
    )


@router.callback_query(F.data == "admin:add_balance")
async def admin_add_balance_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_add_balance_username)
    await query.message.answer("Отправь username пользователя для пополнения, например: @username")
    await query.answer()


@router.message(AdminStates.waiting_add_balance_username)
async def admin_add_balance_username_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    user = storage.get_user_by_username(message.text.strip())
    if not user:
        await message.answer(f"{ICONS['error']} Пользователь с таким username не найден в базе бота.")
        return
    await state.update_data(target_user_id=user["user_id"], target_username=user["username"])
    await state.set_state(AdminStates.waiting_add_balance_amount)
    await message.answer("Теперь отправь сумму пополнения.")


@router.message(AdminStates.waiting_add_balance_amount)
async def admin_add_balance_amount_handler(message: Message, state: FSMContext) -> None:
    admin_id = await ensure_user(message)
    if not is_admin(admin_id):
        return
    try:
        amount = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    if amount <= 0:
        await message.answer(f"{ICONS['error']} Сумма должна быть больше 0.")
        return
    data = await state.get_data()
    storage.add_balance(int(data["target_user_id"]), amount)
    await state.clear()
    await message.answer(f"{ICONS['success']} Баланс пополнен.", reply_markup=admin_menu())
    await bot.send_message(
        int(data["target_user_id"]),
        f"{ICONS['success']} Администратор пополнил тебе баланс на <b>{fmt_amount(amount)} {config.asset}</b>.",
        reply_markup=back_to_home(),
    )
    await log_action(
        f"{ICONS['money']} <b>Админ пополнил баланс</b>\nAdmin: <code>{admin_id}</code>\n"
        f"User: @{html.escape(data['target_username'])}\nAmount: <b>{fmt_amount(amount)} {config.asset}</b>"
    )


@router.callback_query(F.data == "admin:remove_balance")
async def admin_remove_balance_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_remove_balance_username)
    await query.message.answer("Отправь username пользователя для списания, например: @username")
    await query.answer()


@router.message(AdminStates.waiting_remove_balance_username)
async def admin_remove_balance_username_handler(message: Message, state: FSMContext) -> None:
    user_id = await ensure_user(message)
    if not is_admin(user_id):
        return
    user = storage.get_user_by_username(message.text.strip())
    if not user:
        await message.answer(f"{ICONS['error']} Пользователь с таким username не найден в базе бота.")
        return
    await state.update_data(target_user_id=user["user_id"], target_username=user["username"], target_balance=user["balance"])
    await state.set_state(AdminStates.waiting_remove_balance_amount)
    await message.answer("Теперь отправь сумму списания.")


@router.message(AdminStates.waiting_remove_balance_amount)
async def admin_remove_balance_amount_handler(message: Message, state: FSMContext) -> None:
    admin_id = await ensure_user(message)
    if not is_admin(admin_id):
        return
    try:
        amount = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи сумму числом.")
        return
    data = await state.get_data()
    if amount <= 0:
        await message.answer(f"{ICONS['error']} Сумма должна быть больше 0.")
        return
    if float(data["target_balance"]) < amount:
        await message.answer(f"{ICONS['error']} На балансе пользователя недостаточно средств.")
        return
    storage.subtract_balance(int(data["target_user_id"]), amount)
    await state.clear()
    await message.answer(f"{ICONS['success']} Баланс уменьшен.", reply_markup=admin_menu())
    await bot.send_message(
        int(data["target_user_id"]),
        f"{ICONS['warning']} Администратор списал с твоего баланса <b>{fmt_amount(amount)} {config.asset}</b>.",
        reply_markup=back_to_home(),
    )
    await log_action(
        f"{ICONS['money']} <b>Админ снял баланс</b>\nAdmin: <code>{admin_id}</code>\n"
        f"User: @{html.escape(data['target_username'])}\nAmount: <b>{fmt_amount(amount)} {config.asset}</b>"
    )


@router.callback_query(F.data == "admin:set_min_deposit")
async def admin_set_min_deposit_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_set_min_deposit)
    await query.message.answer("Отправь новое значение минимального депозита.")
    await query.answer()


@router.message(AdminStates.waiting_set_min_deposit)
async def admin_set_min_deposit_message_handler(message: Message, state: FSMContext) -> None:
    admin_id = await ensure_user(message)
    if not is_admin(admin_id):
        return
    try:
        value = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи число.")
        return
    storage.set_setting("min_deposit", value)
    await state.clear()
    await message.answer(f"{ICONS['success']} Минимальный депозит обновлен.", reply_markup=admin_menu())
    await log_action(
        f"{ICONS['settings']} <b>Изменен мин. депозит</b>\nAdmin: <code>{admin_id}</code>\n"
        f"Value: <b>{fmt_amount(value)} {config.asset}</b>"
    )


@router.callback_query(F.data == "admin:set_min_withdraw")
async def admin_set_min_withdraw_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_set_min_withdraw)
    await query.message.answer("Отправь новое значение минимального вывода.")
    await query.answer()


@router.message(AdminStates.waiting_set_min_withdraw)
async def admin_set_min_withdraw_message_handler(message: Message, state: FSMContext) -> None:
    admin_id = await ensure_user(message)
    if not is_admin(admin_id):
        return
    try:
        value = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи число.")
        return
    storage.set_setting("min_withdraw", value)
    await state.clear()
    await message.answer(f"{ICONS['success']} Минимальный вывод обновлен.", reply_markup=admin_menu())
    await log_action(
        f"{ICONS['settings']} <b>Изменен мин. вывод</b>\nAdmin: <code>{admin_id}</code>\n"
        f"Value: <b>{fmt_amount(value)} {config.asset}</b>"
    )


@router.callback_query(F.data == "admin:toggle_auto_withdraw")
async def admin_toggle_auto_withdraw_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    new_value = "0" if auto_withdraw_enabled() else "1"
    storage.set_setting("auto_withdraw", new_value)
    await query.message.edit_text(
        f"{ICONS['success']} Автовывод теперь {'включен' if new_value == '1' else 'выключен'}.",
        reply_markup=admin_menu(),
    )
    await query.answer()
    await log_action(
        f"{ICONS['settings']} <b>Переключен автовывод</b>\nAdmin: <code>{user_id}</code>\n"
        f"State: <b>{'ON' if new_value == '1' else 'OFF'}</b>"
    )


@router.callback_query(F.data == "admin:toggle_withdraw_gate")
async def admin_toggle_withdraw_gate_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    new_value = "0" if withdraw_gate_enabled() else "1"
    storage.set_setting("withdraw_gate_enabled", new_value)
    await query.message.edit_text(
        f"{ICONS['success']} Вывод только с депозитом теперь {'включен' if new_value == '1' else 'выключен'}.",
        reply_markup=admin_menu(),
    )
    await query.answer()
    await log_action(
        f"{ICONS['settings']} <b>Переключен режим вывода с депозитом</b>\nAdmin: <code>{user_id}</code>\n"
        f"State: <b>{'ON' if new_value == '1' else 'OFF'}</b>"
    )


@router.callback_query(F.data == "admin:set_withdraw_gate_amount")
async def admin_set_withdraw_gate_amount_handler(query: CallbackQuery, state: FSMContext) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_set_withdraw_gate_amount)
    await query.message.answer("Отправь сумму обязательного депозита для доступа к выводу.")
    await query.answer()


@router.message(AdminStates.waiting_set_withdraw_gate_amount)
async def admin_set_withdraw_gate_amount_message_handler(message: Message, state: FSMContext) -> None:
    admin_id = await ensure_user(message)
    if not is_admin(admin_id):
        return
    try:
        value = clean_amount(message.text)
    except Exception:  # noqa: BLE001
        await message.answer(f"{ICONS['error']} Введи число.")
        return
    storage.set_setting("withdraw_gate_amount", value)
    await state.clear()
    await message.answer(f"{ICONS['success']} Порог депозита для вывода обновлен.", reply_markup=admin_menu())
    await log_action(
        f"{ICONS['settings']} <b>Изменен порог депозита для вывода</b>\nAdmin: <code>{admin_id}</code>\n"
        f"Value: <b>{fmt_amount(value)} {config.asset}</b>"
    )


@router.callback_query(F.data == "admin:list_withdrawals")
async def admin_list_withdrawals_handler(query: CallbackQuery) -> None:
    user_id = await ensure_user(query)
    if not is_admin(user_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    items = storage.list_pending_withdrawals()
    if not items:
        await query.message.edit_text("Ожидающих заявок нет.", reply_markup=admin_menu())
        await query.answer()
        return
    text = "\n\n".join(
        f"ID: <code>{item['id']}</code>\nUser: <code>{item['user_id']}</code>\nAmount: <b>{fmt_amount(item['amount'])} {config.asset}</b>"
        for item in items[:20]
    )
    await query.message.edit_text(text, reply_markup=admin_menu())
    await query.answer()


@router.callback_query(F.data.startswith("withdraw:approve:"))
async def approve_withdraw_handler(query: CallbackQuery) -> None:
    admin_id = await ensure_user(query)
    if not is_admin(admin_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    withdrawal_id = int(query.data.rsplit(":", maxsplit=1)[1])
    item = storage.get_withdrawal(withdrawal_id)
    if not item or item["status"] != "pending":
        await query.answer("Заявка уже обработана.", show_alert=True)
        return
    ok, reason, check_id, check_url = await create_withdraw_check(float(item["amount"]))
    if not ok or not check_url:
        await query.answer(reason, show_alert=True)
        await bot.send_message(item["user_id"], f"{ICONS['error']} Нету казны. Попробуйте позже.", reply_markup=back_to_home())
        await log_action(
            f"{ICONS['error']} <b>Не удалось подтвердить вывод</b>\nAdmin: <code>{admin_id}</code>\n"
            f"Withdrawal: <code>{withdrawal_id}</code>\nReason: <code>{html.escape(reason)}</code>"
        )
        return
    storage.update_withdrawal(withdrawal_id, "paid", check_id=check_id, check_url=check_url, note="manual_approved")
    storage.add_withdraw_total(item["user_id"], float(item["amount"]))
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Забрать чек", url=check_url, icon_custom_emoji_id=premium_button_icon("wallet"))],
            [InlineKeyboardButton(text="В меню", callback_data="menu:home", icon_custom_emoji_id=premium_button_icon("home"))],
        ]
    )
    await bot.send_message(
        item["user_id"],
        (
            f"{ICONS['success']} <b>Вывод подтвержден</b>\n\n"
            f"Сумма: <b>{fmt_amount(item['amount'])} {config.asset}</b>"
        ),
        reply_markup=keyboard,
    )
    await query.message.edit_text(f"{ICONS['success']} Вывод #{withdrawal_id} подтвержден.")
    await query.answer()
    await log_action(
        f"{ICONS['withdraw']} <b>Вывод подтвержден</b>\nAdmin: <code>{admin_id}</code>\n"
        f"Withdrawal: <code>{withdrawal_id}</code>\nAmount: <b>{fmt_amount(item['amount'])} {config.asset}</b>"
    )


@router.callback_query(F.data.startswith("withdraw:reject:"))
async def reject_withdraw_handler(query: CallbackQuery) -> None:
    admin_id = await ensure_user(query)
    if not is_admin(admin_id):
        await query.answer("Нет доступа.", show_alert=True)
        return
    withdrawal_id = int(query.data.rsplit(":", maxsplit=1)[1])
    item = storage.get_withdrawal(withdrawal_id)
    if not item or item["status"] != "pending":
        await query.answer("Заявка уже обработана.", show_alert=True)
        return
    storage.update_withdrawal(withdrawal_id, "rejected", note="manual_rejected")
    storage.add_balance(item["user_id"], float(item["amount"]))
    await bot.send_message(
        item["user_id"],
        (
            f"{ICONS['error']} Вывод отклонен! Попробуйте снова.\n\n"
            f"Сумма возвращена на баланс: <b>{fmt_amount(item['amount'])} {config.asset}</b>"
        ),
        reply_markup=back_to_home(),
    )
    await query.message.edit_text(f"{ICONS['warning']} Вывод #{withdrawal_id} отклонен.")
    await query.answer()
    await log_action(
        f"{ICONS['error']} <b>Вывод отклонен</b>\nAdmin: <code>{admin_id}</code>\n"
        f"Withdrawal: <code>{withdrawal_id}</code>\nAmount: <b>{fmt_amount(item['amount'])} {config.asset}</b>"
    )


async def main() -> None:
    global bot
    global bot_username
    await crypto.start()
    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    me = await bot.get_me()
    bot_username = me.username
    dp = Dispatcher()
    dp.include_router(router)
    asyncio.create_task(poll_invoices())
    await log_action(f"{ICONS['success']} <b>Бот запущен</b>\nBot: @{html.escape(bot_username)}")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            asyncio.run(crypto.close())
        except Exception:  # noqa: BLE001
            pass
