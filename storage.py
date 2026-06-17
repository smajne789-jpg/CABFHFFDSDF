import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.init_db()

    @contextmanager
    def cursor(self):
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        finally:
            cur.close()

    def init_db(self) -> None:
        with self.cursor() as cur:
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    balance REAL NOT NULL DEFAULT 0,
                    total_deposit REAL NOT NULL DEFAULT 0,
                    total_withdraw REAL NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS force_channels (
                    chat_id INTEGER PRIMARY KEY,
                    title TEXT,
                    invite_link TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS invoices (
                    invoice_id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    asset TEXT NOT NULL,
                    pay_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    asset TEXT NOT NULL,
                    status TEXT NOT NULL,
                    check_id INTEGER,
                    check_url TEXT,
                    note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_checks (
                    token TEXT PRIMARY KEY,
                    amount REAL NOT NULL,
                    activations_total INTEGER NOT NULL,
                    activations_left INTEGER NOT NULL,
                    deposit_required REAL NOT NULL DEFAULT 0,
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_activations (
                    token TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (token, user_id)
                );

                CREATE TABLE IF NOT EXISTS games (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    game_key TEXT NOT NULL,
                    stake REAL NOT NULL,
                    multiplier REAL NOT NULL,
                    result_value TEXT NOT NULL,
                    win INTEGER NOT NULL,
                    payout REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def ensure_defaults(self, defaults: dict[str, str]) -> None:
        with self.cursor() as cur:
            for key, value in defaults.items():
                cur.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, str(value)),
                )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cur.fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )

    def upsert_user(self, user_id: int, username: str | None, first_name: str | None) -> None:
        now = utc_now()
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (user_id, username, first_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    updated_at = excluded.updated_at
                """,
                (user_id, username, first_name, now, now),
            )

    def get_user(self, user_id: int) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            return cur.fetchone()

    def get_user_by_username(self, username: str) -> sqlite3.Row | None:
        uname = username.lstrip("@").lower()
        with self.cursor() as cur:
            cur.execute(
                "SELECT * FROM users WHERE lower(coalesce(username, '')) = ?",
                (uname,),
            )
            return cur.fetchone()

    def add_balance(self, user_id: int, amount: float) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET balance = round(balance + ?, 8), updated_at = ?
                WHERE user_id = ?
                """,
                (amount, utc_now(), user_id),
            )

    def subtract_balance(self, user_id: int, amount: float) -> None:
        self.add_balance(user_id, -amount)

    def add_deposit_total(self, user_id: int, amount: float) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET total_deposit = round(total_deposit + ?, 8), updated_at = ?
                WHERE user_id = ?
                """,
                (amount, utc_now(), user_id),
            )

    def add_withdraw_total(self, user_id: int, amount: float) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE users
                SET total_withdraw = round(total_withdraw + ?, 8), updated_at = ?
                WHERE user_id = ?
                """,
                (amount, utc_now(), user_id),
            )

    def add_force_channel(self, chat_id: int, title: str | None, invite_link: str | None) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO force_channels (chat_id, title, invite_link, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    invite_link = excluded.invite_link
                """,
                (chat_id, title, invite_link, utc_now()),
            )

    def remove_force_channel(self, chat_id: int) -> None:
        with self.cursor() as cur:
            cur.execute("DELETE FROM force_channels WHERE chat_id = ?", (chat_id,))

    def list_force_channels(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM force_channels ORDER BY created_at")
            return cur.fetchall()

    def create_invoice(self, invoice_id: int, user_id: int, amount: float, asset: str, pay_url: str) -> None:
        now = utc_now()
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO invoices (invoice_id, user_id, amount, asset, pay_url, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (invoice_id, user_id, amount, asset, pay_url, now, now),
            )

    def get_active_invoices(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE status = 'active' ORDER BY created_at")
            return cur.fetchall()

    def mark_invoice_paid(self, invoice_id: int) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
            row = cur.fetchone()
            if not row:
                return None
            if row["status"] == "paid":
                return None
            cur.execute(
                "UPDATE invoices SET status = 'paid', updated_at = ? WHERE invoice_id = ?",
                (utc_now(), invoice_id),
            )
            cur.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,))
            return cur.fetchone()

    def create_withdrawal(self, user_id: int, amount: float, asset: str, status: str, note: str | None = None) -> int:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO withdrawals (user_id, amount, asset, status, note, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, amount, asset, status, note, utc_now(), utc_now()),
            )
            return int(cur.lastrowid)

    def update_withdrawal(
        self,
        withdrawal_id: int,
        status: str,
        check_id: int | None = None,
        check_url: str | None = None,
        note: str | None = None,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE withdrawals
                SET status = ?, check_id = ?, check_url = ?, note = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, check_id, check_url, note, utc_now(), withdrawal_id),
            )

    def get_withdrawal(self, withdrawal_id: int) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM withdrawals WHERE id = ?", (withdrawal_id,))
            return cur.fetchone()

    def list_pending_withdrawals(self) -> list[sqlite3.Row]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM withdrawals WHERE status = 'pending' ORDER BY created_at")
            return cur.fetchall()

    def create_promo_check(
        self,
        amount: float,
        activations_total: int,
        deposit_required: float,
        created_by: int,
    ) -> str:
        token = secrets.token_urlsafe(12)
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO promo_checks (
                    token, amount, activations_total, activations_left, deposit_required, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token,
                    amount,
                    activations_total,
                    activations_total,
                    deposit_required,
                    created_by,
                    utc_now(),
                ),
            )
        return token

    def get_promo_check(self, token: str) -> sqlite3.Row | None:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM promo_checks WHERE token = ?", (token,))
            return cur.fetchone()

    def activate_promo_check(self, token: str, user_id: int) -> tuple[bool, str]:
        with self.cursor() as cur:
            cur.execute("SELECT * FROM promo_checks WHERE token = ?", (token,))
            check = cur.fetchone()
            if not check:
                return False, "Чек не найден."
            if check["activations_left"] <= 0:
                return False, "Лимит активаций у этого чека уже закончился."
            cur.execute(
                "SELECT 1 FROM promo_activations WHERE token = ? AND user_id = ?",
                (token, user_id),
            )
            if cur.fetchone():
                return False, "Ты уже активировал этот чек."
            cur.execute(
                """
                INSERT INTO promo_activations (token, user_id, created_at)
                VALUES (?, ?, ?)
                """,
                (token, user_id, utc_now()),
            )
            cur.execute(
                """
                UPDATE promo_checks
                SET activations_left = activations_left - 1
                WHERE token = ?
                """,
                (token,),
            )
            cur.execute(
                """
                UPDATE users
                SET balance = round(balance + ?, 8), updated_at = ?
                WHERE user_id = ?
                """,
                (check["amount"], utc_now(), user_id),
            )
            return True, "ok"

    def create_game(
        self,
        user_id: int,
        game_key: str,
        stake: float,
        multiplier: float,
        result_value: str,
        win: bool,
        payout: float,
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT INTO games (user_id, game_key, stake, multiplier, result_value, win, payout, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, game_key, stake, multiplier, result_value, int(win), payout, utc_now()),
            )
