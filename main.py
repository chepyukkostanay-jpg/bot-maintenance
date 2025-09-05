import os
import sqlite3
import time
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
from flask import Flask, request

# ---- грузим .env ----
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
DB_PATH = os.path.join(os.path.dirname(__file__), "issues.db")
ADMINS = set(map(int, filter(None, os.getenv("ADMINS", "").split(","))))

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

app = Flask(__name__)

# === Webhook endpoint ===
@app.route("/webhook", methods=["POST"])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_str = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
        return "OK", 200
    return "Unsupported Media Type", 415

@app.route("/")
def health():
    return "OK", 200

# --- DB helpers ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    c.close()
    return conn

# --- безопасное редактирование ---
def safe_edit_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            try:
                bot.edit_message_reply_markup(chat_id, message_id, reply_markup=reply_markup)
            except ApiTelegramException:
                pass
        else:
            raise

# Пример простого меню
def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📣 Сообщить о проблеме", callback_data="report|start"))
    kb.add(types.InlineKeyboardButton("👤 Профиль", callback_data="profile|view"))
    return kb

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    bot.send_message(message.chat.id, "Добро пожаловать! Выберите действие:", reply_markup=main_menu())

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("report|"))
def report_callbacks(cq: types.CallbackQuery):
    safe_edit_text(cq.message.chat.id, cq.message.message_id, "Форма для заявки пока в разработке…", reply_markup=main_menu())
    bot.answer_callback_query(cq.id)

@bot.callback_query_handler(func=lambda cq: cq.data.startswith("profile|"))
def profile_callbacks(cq: types.CallbackQuery):
    safe_edit_text(cq.message.chat.id, cq.message.message_id, "Раздел профиля пока в разработке…", reply_markup=main_menu())
    bot.answer_callback_query(cq.id)

if __name__ == "__main__":
    # Для локального теста можно включить polling, если TELEGRAM_MODE=polling
    mode = os.getenv("TELEGRAM_MODE", "webhook")
    if mode == "polling":
        print("🤖 Бот запущен (polling)")
        bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
    else:
        print("🌐 Webhook режим. Flask-приложение запущено.")
        app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))

# =====================
# 📚 СПРАВОЧНИКИ
# =====================
ROLES = [
    "инженер",
    "электрик КИПиА",
    "мастер цеха",
    "слесарь",
    "механик",
    "технолог",
]

PRODUCTION_MACHINES = ["Станок №1", "Станок №12", "Станок №8", "Станок №9", "Станок №11", "Станок №11А"]
PACKING_LINES = ["0.3Н", "0.3Б", "0.8", "2.5", "Элита"]

# Фасовка: после выбора линии (кроме "2.5") — выбираем узел
PACKING_COMPONENTS_DEFAULT = [
    "бункер", "конвейер. лента", "корзина", "принтер", "фото-метка",
    "формовка пакета", "встряхиватель", "замена трубы", "другое",
]

# Производство: узлы по станкам
PRODUCTION_COMPONENTS = {
    "Станок №1": ["ванна", "просеиватель", "пружина", "дозатор", "замес",
                  "питатель", "пресс", "нож", "трабатта", "лоткоподача", "штабелер", "другое"],
    "Станок №12": ["ванна", "просеиватель", "пружина", "дозатор", "замес",
                   "питатель", "пресс", "нож", "трабатта", "лоткоподача", "штабелер", "другое"],
    "Станок №8": ["ванна", "просеиватель", "пружина", "дозатор", "замес",
                  "питатель", "пресс", "нож", "трабатта", "группорезка", "другое"],
    "Станок №9": ["ванна", "просеиватель", "пружина", "дозатор", "замес",
                  "питатель", "пресс", "нож", "трабатта"],
    "Станок №11": ["ванна", "просеиватель", "бункер замеса", "раскатка",
                   "калибратор", "нож", "лоткоподача", "другое"],
    "Станок №11А": ["ванна", "просеиватель", "бункер замеса", "раскатка",
                    "калибратор", "нож", "лоткоподача", "штабелер", "другое"],
}

# Станок №8 → группорезка → подузлы
PROD_GROUP_CUT_SUB = ["лоткоподача", "другое"]

# Транспорт
TRANSPORT_TYPES = ["Грузовой транспорт", "Погрузчики"]

# Техническое оборудование
TECH_EQUIPMENT = ["компрессор", "котельная", "приточ. вентиляция", "другое"]

# =====================
# 🧠 СЕССИИ (in-memory)
# =====================
SESSION: dict[int, dict] = {}

def ensure_session(user_id: int) -> dict:
    if user_id not in SESSION:
        SESSION[user_id] = {"step": None, "data": {}}
    return SESSION[user_id]

def reset_session(user_id: int) -> None:
    SESSION[user_id] = {"step": None, "data": {}}

# =====================
# 🗄️ БАЗА ДАННЫХ + устойчивость к конкуренции
# =====================
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c = conn.cursor()
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA busy_timeout=5000;")
    c.close()
    return conn

def with_retry(fn, retries=3, pause=0.2):
    last = None
    for i in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as e:
            last = e
            time.sleep(pause * (i + 1))
    raise last

def db_init() -> None:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                user_name TEXT,
                area TEXT,
                subarea TEXT,
                equipment TEXT,
                description TEXT NOT NULL,
                status TEXT NOT NULL,
                resolved_at TEXT,
                resolver_id INTEGER,
                resolver_name TEXT,
                user_fio_snapshot TEXT,
                user_role_snapshot TEXT
            )
            """
        )
        conn.commit()

def db_init_users_and_migration() -> None:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                fio TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        c.execute("PRAGMA table_info(issues)")
        cols = {row[1] for row in c.fetchall()}
        if "user_role_snapshot" not in cols:
            c.execute("ALTER TABLE issues ADD COLUMN user_role_snapshot TEXT")
        if "user_fio_snapshot" not in cols:
            c.execute("ALTER TABLE issues ADD COLUMN user_fio_snapshot TEXT")
        conn.commit()

# users
def user_get(user_id: int) -> Optional[tuple]:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id, fio, role, created_at FROM users WHERE user_id=?", (user_id,))
        return c.fetchone()

def user_upsert(user_id: int, fio: str, role: str) -> None:
    def _do():
        with get_conn() as conn:
            c = conn.cursor()
            c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,))
            exists = c.fetchone() is not None
            if exists:
                c.execute("UPDATE users SET fio=?, role=? WHERE user_id=?", (fio, role, user_id))
            else:
                c.execute(
                    "INSERT INTO users (user_id, fio, role, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, fio, role, datetime.now().isoformat(timespec="seconds")),
                )
            conn.commit()
    return with_retry(_do)

# access levels
def is_admin(user_id: int) -> bool:
    return user_id in ADMINS

def user_level(user_id: int) -> int:
    if is_admin(user_id):
        return 3
    u = user_get(user_id)
    role = (u[2] if u else "").lower()
    if role == "технолог":
        return 1
    return 2

# issues
def issue_create(user_id: int, user_name: str, area: Optional[str], subarea: Optional[str],
                 equipment: Optional[str], description: str) -> int:
    u = user_get(user_id)
    fio_snapshot = u[1] if u else (user_name or "")
    role_snapshot = u[2] if u else ""
    def _do():
        with get_conn() as conn:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO issues (
                    created_at, user_id, user_name,
                    area, subarea, equipment, description,
                    status,
                    user_fio_snapshot, user_role_snapshot
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"), user_id, user_name,
                    area, subarea, equipment, description,
                    fio_snapshot, role_snapshot,
                ),
            )
            conn.commit()
            return c.lastrowid
    return with_retry(_do)

def issues_open(limit: int = 20):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT id, created_at, user_name, area, subarea, equipment, description
            FROM issues
            WHERE status='open'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return c.fetchall()

def issues_by_user(user_id: int, limit: int = 20):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT id, created_at, status, area, subarea, equipment, description, resolved_at,
                   user_fio_snapshot, user_role_snapshot
            FROM issues
            WHERE user_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return c.fetchall()

def issue_close(issue_id: int, resolver_id: int, resolver_name: str) -> bool:
    def _do():
        with get_conn() as conn:
            c = conn.cursor()
            c.execute(
                """
                UPDATE issues
                SET status='closed', resolved_at=?, resolver_id=?, resolver_name=?
                WHERE id=? AND status='open'
                """,
                (datetime.now().isoformat(timespec="seconds"), resolver_id, resolver_name, issue_id),
            )
            conn.commit()
            return c.rowcount > 0
    return with_retry(_do)

def issues_all(status: Optional[str] = None, by_user_id: Optional[int] = None, limit: Optional[int] = None):
    q = ("SELECT id, created_at, user_name, area, subarea, equipment, description, status, "
         "resolved_at, resolver_name, user_fio_snapshot, user_role_snapshot FROM issues")
    conds, params = [], []
    if status in ("open", "closed"):
        conds.append("status = ?")
        params.append(status)
    if by_user_id is not None:
        conds.append("user_id = ?")
        params.append(by_user_id)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC"
    if limit:
        q += f" LIMIT {int(limit)}"
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(q, tuple(params))
        return c.fetchall()

def export_to_excel(path: str, status: Optional[str] = None, by_user_id: Optional[int] = None, limit: Optional[int] = None):
    try:
        import pandas as pd  # pip install pandas openpyxl
    except Exception as e:
        raise RuntimeError("Для экспорта установите пакеты: pandas, openpyxl") from e
    rows = issues_all(status=status, by_user_id=by_user_id, limit=limit)
    cols = [
        "id", "created_at", "user_name", "area", "subarea", "equipment", "description",
        "status", "resolved_at", "resolver_name", "user_fio_snapshot", "user_role_snapshot",
    ]
    df = pd.DataFrame(rows, columns=cols)
    for col in ("created_at", "resolved_at"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="issues", index=False)

# =====================
# 🛡️ Безопасное редактирование
# =====================
def safe_edit_text(chat_id: int, message_id: int, text: str, reply_markup=None):
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
    except ApiTelegramException as e:
        if "message is not modified" in str(e).lower():
            try:
                bot.edit_message_reply_markup(chat_id, message_id, reply_markup=reply_markup)
            except ApiTelegramException:
                pass
        else:
            raise

# =====================
# 🧩 КЛАВИАТУРЫ
# =====================
def main_menu_for(user_id: int) -> types.ReplyKeyboardMarkup:
    lvl = user_level(user_id)
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("📣 Сообщить о проблеме")
    if lvl >= 2:
        kb.add("✅ Сообщить о решении")
        kb.row("📜 История (мои)", "📚 История (все)")
    else:
        kb.row("📜 История (мои)")
    if lvl >= 3:
        kb.add("📤 Экспорт Excel")
    kb.row("👤 Профиль")
    return kb

def roles_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for r in ROLES:
        kb.add(types.InlineKeyboardButton(r.title(), callback_data=f"profile|role|{r}"))
    kb.add(types.InlineKeyboardButton("Отмена", callback_data="profile|cancel"))
    return kb

# --- report меню (инлайн) ---
def menu_layer1() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Цех", callback_data="report|area|Цех"))
    kb.add(types.InlineKeyboardButton("Транспорт", callback_data="report|area|Транспорт"))
    return kb

def menu_layer2_for_ceh() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Производство", callback_data="report|subarea|Производство"))
    kb.add(types.InlineKeyboardButton("Фасовка", callback_data="report|subarea|Фасовка"))
    kb.add(types.InlineKeyboardButton("Техническое оборудование", callback_data="report|subarea|Техническое оборудование"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|1"))
    return kb

def menu_layer2_transport() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for t in TRANSPORT_TYPES:
        kb.add(types.InlineKeyboardButton(t, callback_data=f"report|transport|{t}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|1"))
    return kb

def menu_layer3_production() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for name in PRODUCTION_MACHINES:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"report|equipment|{name}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|2_ceh"))
    return kb

def menu_layer3_packing() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for name in PACKING_LINES:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"report|equipment|{name}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|2_ceh"))
    return kb

def menu_layer3_tech() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for name in TECH_EQUIPMENT:
        kb.add(types.InlineKeyboardButton(name, callback_data=f"report|tech|{name}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|2_ceh"))
    return kb

def menu_layer4_pack_components() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for comp in PACKING_COMPONENTS_DEFAULT:
        kb.add(types.InlineKeyboardButton(comp, callback_data=f"report|packcomp|{comp}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|3_pack"))
    return kb

def menu_layer4_prod_components(machine: str) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for comp in PRODUCTION_COMPONENTS.get(machine, []):
        kb.add(types.InlineKeyboardButton(comp, callback_data=f"report|prodcomp|{comp}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|3_prod"))
    return kb

def menu_layer5_groupcut_sub() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for sub in PROD_GROUP_CUT_SUB:
        kb.add(types.InlineKeyboardButton(sub, callback_data=f"report|prodsubcomp|{sub}"))
    kb.add(types.InlineKeyboardButton("⬅ Назад", callback_data="report|back|4_group"))
    return kb

def open_issues_inline() -> types.InlineKeyboardMarkup:
    data = issues_open(limit=20)
    kb = types.InlineKeyboardMarkup()
    if not data:
        kb.add(types.InlineKeyboardButton("Нет открытых заявок", callback_data="noop"))
    else:
        for row in data:
            _id, created_at, user_name, area, subarea, equipment, desc = row
            label_parts = [p for p in [str(_id), area, subarea, equipment] if p]
            label = "#" + label_parts[0] + " " + "/".join(label_parts[1:]) if len(label_parts) > 1 else f"#{_id}"
            kb.add(types.InlineKeyboardButton(label[:64], callback_data=f"fix|pick|{_id}"))
    kb.add(types.InlineKeyboardButton("Обновить", callback_data="fix|refresh"))
    return kb

# =====================
# =====================
# 👤 ПРОФИЛЬ
# =====================
import re  # убедись, что импорт есть в шапке файла

FIO_RE = re.compile(
    r"^[А-ЯЁA-Z][а-яёa-z]+(?:[- ][А-ЯЁA-Z][а-яёa-z]+)?\s+[А-ЯЁA-Z]\.?\s*[А-ЯЁA-Z]\.?$"
)

@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    user = message.from_user
    payload = None
    parts = message.text.split(maxsplit=1)
    if len(parts) == 2:
        payload = parts[1]

    u = user_get(user.id)
    if not u:
        s = ensure_session(user.id)
        s["step"] = "profile_wait_fio"
        bot.send_message(
            message.chat.id,
            "Добро пожаловать! Укажите <b>Фамилию и инициалы</b> в формате: <code>Иванов И.И.</code>\n"
            "Можно пробел после точек: <code>Иванов И. И.</code>"
        )
        if payload:
            s["data"]["start_payload"] = payload
        return

    equipment_from_payload = decode_equipment_from_payload(payload) if payload else None
    if equipment_from_payload:
        s = ensure_session(user.id)
        s["step"] = "report_description"
        s["data"] = {"area": None, "subarea": None, "equipment": equipment_from_payload}
        bot.send_message(message.chat.id, f"Оборудование: <b>{equipment_from_payload}</b>\nОпишите поломку:")
        return

    bot.send_message(
        message.chat.id,
        f"Привет, <b>{u[1]}</b> ({u[2]}). Выберите действие:",
        reply_markup=main_menu_for(user.id),
    )

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def on_profile_view(message: types.Message):
    """Показ текущего профиля с кнопками редактирования."""
    u = user_get(message.from_user.id)
    if not u:
        s = ensure_session(message.from_user.id)
        s["step"] = "profile_wait_fio"
        bot.reply_to(
            message,
            "Профиль не настроен. Введите ФИО (например, <code>Иванов И.И.</code>)."
        )
        return

    _, fio, role, created_at = u
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Изменить ФИО", callback_data="profile|edit_fio"))
    kb.add(types.InlineKeyboardButton("🔄 Сменить направление", callback_data="profile|edit_role"))
    bot.reply_to(
        message,
        f"Ваш профиль:\n<b>{fio}</b>\n{role}\nСоздан: {created_at}",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: ensure_session(m.from_user.id).get("step") == "profile_wait_fio")
def on_profile_fio(message: types.Message):
    """Первичная настройка: принимаем ФИО и предлагаем выбрать роль."""
    fio = message.text.strip()
    if not FIO_RE.match(fio):
        bot.reply_to(message, "Формат неверный. Пример: <code>Иванов И.И.</code>")
        return
    s = ensure_session(message.from_user.id)
    s["data"]["fio"] = fio
    s["step"] = "profile_wait_role"
    bot.send_message(message.chat.id, "Выберите ваше направление:", reply_markup=roles_keyboard())

@bot.message_handler(func=lambda m: ensure_session(m.from_user.id).get("step") == "profile_edit_fio")
def on_profile_edit_fio(message: types.Message):
    """Редактирование ФИО из меню профиля."""
    fio = message.text.strip()
    if not FIO_RE.match(fio):
        bot.reply_to(
            message,
            "Формат неверный. Пример: <code>Иванов И.И.</code>\n"
            "Допустимо и с пробелами: <code>Иванов И. И.</code>"
        )
        return
    u = user_get(message.from_user.id)
    role = u[2] if u else "инженер"
    user_upsert(message.from_user.id, fio, role)
    reset_session(message.from_user.id)
    bot.reply_to(
        message,
        f"ФИО обновлено: <b>{fio}</b>",
        reply_markup=main_menu_for(message.from_user.id)
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("profile|"))
def profile_callbacks(cq: types.CallbackQuery):
    """Единый обработчик всех inline-кнопок профиля."""
    parts = cq.data.split("|")
    action = parts[1]

    if action == "cancel":
        reset_session(cq.from_user.id)
        bot.edit_message_text(
            "Настройка профиля отменена. Запустите /start для начала.",
            cq.message.chat.id, cq.message.message_id
        )
        return

    if action == "edit_fio":
        s = ensure_session(cq.from_user.id)
        s["step"] = "profile_edit_fio"
        bot.edit_message_text(
            "Введите новое ФИО (формат: <code>Иванов И.И.</code>):",
            cq.message.chat.id, cq.message.message_id
        )
        return

    if action == "edit_role":
        # показываем клавиатуру выбора роли
        bot.edit_message_text(
            "Выберите новое направление:",
            cq.message.chat.id, cq.message.message_id,
            reply_markup=roles_keyboard()
        )
        return

    if action == "role":
        # сохраняем выбранную роль
        role = parts[2]
        if role not in ROLES:
            bot.answer_callback_query(cq.id, "Неизвестная роль. Выберите из списка.", show_alert=True)
            return
        s = ensure_session(cq.from_user.id)
        fio = s.get("data", {}).get("fio")
        if not fio:
            u = user_get(cq.from_user.id)
            if u:
                fio = u[1]
            else:
                bot.answer_callback_query(cq.id, "Сначала введите ФИО.", show_alert=True)
                return
        user_upsert(cq.from_user.id, fio, role)
        # очищаем сценарий и показываем главное меню
        reset_session(cq.from_user.id)
        bot.edit_message_text(
            f"Профиль сохранён: <b>{fio}</b> ({role}).",
            cq.message.chat.id, cq.message.message_id
        )
        bot.send_message(
            cq.message.chat.id, "Выберите действие:",
            reply_markup=main_menu_for(cq.from_user.id)
        )
        return

# =====================
# 🧾 РЕПОРТ / ФИКС / ИСТОРИЯ / ЭКСПОРТ
# =====================
@bot.message_handler(func=lambda m: m.text == "📣 Сообщить о проблеме")
def on_report_entry(message: types.Message):
    u = user_get(message.from_user.id)
    if not u:
        bot.reply_to(message, "Сначала настроим профиль. Введите ФИО (например, <code>Иванов И.И.</code>).")
        s = ensure_session(message.from_user.id)
        s["step"] = "profile_wait_fio"
        return
    s = ensure_session(message.from_user.id)
    s["step"] = "report_layer1"
    s["data"] = {"area": None, "subarea": None, "equipment": None, "component": None, "_machine_raw": None}
    bot.send_message(message.chat.id, "Поломка в:", reply_markup=types.ReplyKeyboardRemove())
    bot.send_message(message.chat.id, "Выберите область:", reply_markup=menu_layer1())

@bot.message_handler(func=lambda m: m.text == "✅ Сообщить о решении")
def on_fix_entry(message: types.Message):
    if user_level(message.from_user.id) < 2:
        bot.reply_to(message, "Недостаточно прав: закрывать заявки могут мастера и администраторы.")
        return
    u = user_get(message.from_user.id)
    if not u:
        bot.reply_to(message, "Сначала настроим профиль. Введите ФИО (например, <code>Иванов И.И.</code>).")
        s = ensure_session(message.from_user.id)
        s["step"] = "profile_wait_fio"
        return
    s = ensure_session(message.from_user.id)
    s["step"] = "fix_pick_issue"
    bot.send_message(message.chat.id, "Выберите заявку для закрытия:", reply_markup=open_issues_inline())

@bot.message_handler(func=lambda m: m.text == "👤 Профиль")
def on_profile_view(message: types.Message):
    u = user_get(message.from_user.id)
    if not u:
        # профиля нет — попросим ФИО и запустим настройку
        s = ensure_session(message.from_user.id)
        s["step"] = "profile_wait_fio"
        bot.reply_to(
            message,
            "Профиль не настроен. Введите ФИО (например, <code>Иванов И.И.</code>)."
        )
        return

    _, fio, role, created_at = u
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Изменить ФИО", callback_data="profile|edit_fio"))
    kb.add(types.InlineKeyboardButton("🔄 Сменить направление", callback_data="profile|edit_role"))
    bot.reply_to(
        message,
        f"Ваш профиль:\n<b>{fio}</b>\n{role}\nСоздан: {created_at}",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == "📜 История (мои)")
def on_history(message: types.Message):
    rows = issues_by_user(message.from_user.id, limit=15)
    if not rows:
        bot.reply_to(message, "История пуста.", reply_markup=main_menu_for(message.from_user.id))
        return
    lines = []
    for row in rows:
        _id, created_at, status, area, subarea, equipment, desc, resolved_at, fio_snap, role_snap = row
        tag = "🟩" if status == "closed" else "🟥"
        place = " / ".join([x for x in [area, subarea, equipment] if x])
        who = f"{fio_snap or '—'} ({role_snap or '—'})"
        res = f" → закрыта {resolved_at}" if (resolved_at and status == "closed") else ""
        lines.append(f"{tag} #{_id} [{created_at}] — {place}\n   👤 {who}\n   📝 {desc}{res}")
    bot.reply_to(message, "\n\n".join(lines[:10]), reply_markup=main_menu_for(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "📚 История (все)")
def on_history_all(message: types.Message):
    if user_level(message.from_user.id) < 2:
        bot.reply_to(message, "Недостаточно прав: общую историю видят мастера и администраторы.")
        return
    rows = issues_all(limit=30)
    if not rows:
        bot.reply_to(message, "Заявок нет.", reply_markup=main_menu_for(message.from_user.id))
        return
    lines = []
    for row in rows[:15]:
        _id, created_at, user_name, area, subarea, equipment, desc, status, resolved_at, resolver_name, fio_snap, role_snap = row
        tag = "🟩" if status == "closed" else "🟥"
        place = " / ".join([x for x in [area, subarea, equipment] if x])
        who = f"{fio_snap or user_name or '—'} ({role_snap or '—'})"
        res = f" → закрыта {resolved_at} (закрыл: {resolver_name})" if (resolved_at and status == "closed") else ""
        lines.append(f"{tag} #{_id} [{created_at}] — {place}\n   👤 {who}\n   📝 {desc}{res}")
    bot.reply_to(message, "\n\n".join(lines), reply_markup=main_menu_for(message.from_user.id))

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт Excel")
def on_export_excel(message: types.Message):
    if user_level(message.from_user.id) < 3:
        bot.reply_to(message, "Доступ к экспорту только для администраторов.")
        return
    export_file = "issues_export.xlsx"
    try:
        export_to_excel(export_file, status=None, by_user_id=None, limit=None)
        if os.path.exists(export_file):
            with open(export_file, "rb") as f:
                bot.send_document(message.chat.id, f, caption="Экспорт заявок в Excel")
        else:
            bot.reply_to(message, "Файл экспорта не найден.")
    except Exception as e:
        bot.reply_to(message, f"Не удалось сделать экспорт: {e}")
    finally:
        try:
            os.remove(export_file)
        except Exception:
            pass

# =====================
# 🔁 CALLBACKS: REPORT / FIX
# =====================
@bot.callback_query_handler(func=lambda c: c.data.startswith("report|"))
def report_callbacks(cq: types.CallbackQuery):
    user_id = cq.from_user.id
    s = ensure_session(user_id)
    parts = cq.data.split("|")  # [report, action, value]
    action = parts[1]
    value = parts[2] if len(parts) > 2 else None

    # Назад
    if action == "back":
        where = value
        if where == "1":
            s["step"] = "report_layer1"
            s["data"].update({"area": None, "subarea": None, "equipment": None, "component": None, "_machine_raw": None})
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Выберите область:", reply_markup=menu_layer1())
        elif where == "2_ceh":
            s["step"] = "report_layer2_ceh"
            s["data"].update({"subarea": None, "equipment": None, "component": None})
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Цех → выберите подразделение:", reply_markup=menu_layer2_for_ceh())
        elif where == "2_transport":
            s["step"] = "report_layer2_transport"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Транспорт → выберите тип:", reply_markup=menu_layer2_transport())
        elif where == "3_pack":
            s["step"] = "report_layer3_pack"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Фасовка → выберите линию:", reply_markup=menu_layer3_packing())
        elif where == "3_prod":
            s["step"] = "report_layer3_prod"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Производство → выберите станок:", reply_markup=menu_layer3_production())
        elif where == "4_group":
            machine = s["data"].get("_machine_raw") or s["data"].get("equipment")
            safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{machine} → выберите узел:", reply_markup=menu_layer4_prod_components(machine))
        bot.answer_callback_query(cq.id)
        return

    # Область
    if action == "area":
        s["data"].update({"area": value, "subarea": None, "equipment": None, "component": None, "_machine_raw": None})
        if value == "Цех":
            s["step"] = "report_layer2_ceh"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Цех → выберите подразделение:", reply_markup=menu_layer2_for_ceh())
        elif value == "Транспорт":
            s["step"] = "report_layer2_transport"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Транспорт → выберите тип:", reply_markup=menu_layer2_transport())
        bot.answer_callback_query(cq.id)
        return

    # Транспорт → сразу описание
    if action == "transport":
        s["data"]["subarea"] = value
        s["step"] = "report_description"
        safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{value} → опишите проблему:")
        bot.send_message(cq.message.chat.id, "Введите описание поломки:")
        bot.answer_callback_query(cq.id)
        return

    # Цех → подразделение
    if action == "subarea":
        s["data"]["subarea"] = value
        s["data"].update({"equipment": None, "component": None, "_machine_raw": None})
        if value == "Производство":
            s["step"] = "report_layer3_prod"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Производство → выберите станок:", reply_markup=menu_layer3_production())
        elif value == "Фасовка":
            s["step"] = "report_layer3_pack"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Фасовка → выберите линию:", reply_markup=menu_layer3_packing())
        elif value == "Техническое оборудование":
            s["step"] = "report_layer3_tech"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, "Техническое оборудование → выберите:", reply_markup=menu_layer3_tech())
        bot.answer_callback_query(cq.id)
        return

    # Тех.оборудование → описание
    if action == "tech":
        s["data"]["equipment"] = value
        s["step"] = "report_description"
        safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{value} → опишите проблему:")
        bot.send_message(cq.message.chat.id, "Введите описание поломки:")
        bot.answer_callback_query(cq.id)
        return

    # Выбор оборудования/линии
    if action == "equipment":
        chosen = value
        s["data"]["equipment"] = chosen
        s["data"]["_machine_raw"] = chosen
        s["data"]["component"] = None
        if s["data"].get("subarea") == "Фасовка":
            if chosen == "2.5":
                s["step"] = "report_description"
                safe_edit_text(cq.message.chat.id, cq.message.message_id, f"Фасовка {chosen} → опишите проблему:")
                bot.send_message(cq.message.chat.id, "Введите описание поломки:")
            else:
                s["step"] = "report_layer4_pack_component"
                safe_edit_text(cq.message.chat.id, cq.message.message_id, f"Фасовка {chosen} → выберите узел:", reply_markup=menu_layer4_pack_components())
        elif s["data"].get("subarea") == "Производство":
            s["step"] = "report_layer4_prod_component"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{chosen} → выберите узел:", reply_markup=menu_layer4_prod_components(chosen))
        bot.answer_callback_query(cq.id)
        return

    # Фасовка: узел
    if action == "packcomp":
        comp = value
        equip = s["data"].get("equipment")
        s["data"]["component"] = comp
        s["data"]["equipment"] = f"{equip} > {comp}"
        s["step"] = "report_description"
        safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{equip} / {comp} → опишите проблему:")
        bot.send_message(cq.message.chat.id, "Введите описание поломки:")
        bot.answer_callback_query(cq.id)
        return

    # Производство: узел
    if action == "prodcomp":
        comp = value
        machine = s["data"].get("_machine_raw") or s["data"].get("equipment")
        if machine == "Станок №8" and comp == "группорезка":
            s["step"] = "report_layer5_groupcut"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{machine} → группорезка → выберите подузел:", reply_markup=menu_layer5_groupcut_sub())
        else:
            s["data"]["component"] = comp
            s["data"]["equipment"] = f"{machine} > {comp}"
            s["step"] = "report_description"
            safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{machine} / {comp} → опишите проблему:")
            bot.send_message(cq.message.chat.id, "Введите описание поломки:")
        bot.answer_callback_query(cq.id)
        return

    # Производство: подузлы для группорезки
    if action == "prodsubcomp":
        sub = value
        machine = s["data"].get("_machine_raw") or s["data"].get("equipment")
        s["data"]["component"] = f"группорезка > {sub}"
        s["data"]["equipment"] = f"{machine} > группорезка > {sub}"
        s["step"] = "report_description"
        safe_edit_text(cq.message.chat.id, cq.message.message_id, f"{machine} / группорезка / {sub} → опишите проблему:")
        bot.send_message(cq.message.chat.id, "Введите описание поломки:")
        bot.answer_callback_query(cq.id)
        return

@bot.callback_query_handler(func=lambda c: c.data.startswith("fix|"))
def fix_callbacks(cq: types.CallbackQuery):
    if user_level(cq.from_user.id) < 2:
        bot.answer_callback_query(cq.id, "Недостаточно прав для закрытия заявок", show_alert=True)
        return
    parts = cq.data.split("|")
    action = parts[1]
    if action == "refresh":
        bot.edit_message_reply_markup(cq.message.chat.id, cq.message.message_id, reply_markup=open_issues_inline())
        bot.answer_callback_query(cq.id, "Обновлено")
        return
    if action == "pick":
        issue_id = int(parts[2])
        ok = issue_close(issue_id, cq.from_user.id, cq.from_user.username or cq.from_user.first_name or "")
        if ok:
            bot.edit_message_text(f"Заявка #{issue_id} закрыта ✅", cq.message.chat.id, cq.message.message_id)
        else:
            bot.answer_callback_query(cq.id, "Не удалось закрыть (возможно, уже закрыта)", show_alert=True)
        bot.send_message(cq.message.chat.id, "Готово. Что дальше?", reply_markup=main_menu_for(cq.from_user.id))
        return

# =====================
# 📨 РОУТЕР ТЕКСТОВ (описание поломки)
# =====================
@bot.message_handler(func=lambda m: True)
def text_router(message: types.Message):
    user_id = message.from_user.id
    s = ensure_session(user_id)

    if s.get("step") == "report_description":
        description = message.text.strip()
        data = s["data"]
        area = data.get("area")
        subarea = data.get("subarea")
        equipment = data.get("equipment")
        issue_id = issue_create(
            user_id=user_id,
            user_name=message.from_user.username or message.from_user.first_name or "",
            area=area,
            subarea=subarea,
            equipment=equipment,
            description=description,
        )
        place = " / ".join([x for x in [area, subarea, equipment] if x])
        bot.reply_to(
            message,
            f"✅ Заявка создана: <b>#{issue_id}</b>\n"
            f"📍 {place or '—'}\n"
            f"📝 {description}",
            reply_markup=main_menu_for(message.from_user.id),
        )
        reset_session(user_id)
        return

    # Любой иной текст — подсказываем меню
    if message.text not in [
        "📣 Сообщить о проблеме",
        "✅ Сообщить о решении",
        "📜 История (мои)",
        "📚 История (все)",
        "📤 Экспорт Excel",
        "👤 Профиль",
    ]:
        bot.send_message(message.chat.id, "Выберите действие из меню ниже:", reply_markup=main_menu_for(message.from_user.id))

# =====================
# 🔗 DEEPLINK / QR helper
# =====================
def encode_equipment_to_payload(equipment_id: str) -> str:
    b = equipment_id.encode("utf-8")
    s = base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")
    return s

def decode_equipment_from_payload(payload: str) -> Optional[str]:
    if not payload:
        return None
    pad = "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(payload + pad).decode("utf-8")
    except Exception:
        return None

# =====================
# 🚀 ЗАПУСК
# =====================
if __name__ == "__main__":
    db_init()
    db_init_users_and_migration()
    print("🤖 Бот запущен. Меню готово.")
    bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
