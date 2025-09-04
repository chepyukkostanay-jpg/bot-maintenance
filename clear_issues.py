import sqlite3, os

# указываем путь именно к той базе, которую использует бот
DB_PATH = r"C:\Users\chepy\PycharmProjects\PythonProject\.venv\issues.db"

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Удаляем все заявки
c.execute("DELETE FROM issues")

# Сбрасываем автоинкремент ID (чтобы новые заявки начинались с 1)
c.execute("DELETE FROM sqlite_sequence WHERE name='issues'")

conn.commit()
conn.close()

print("✅ Все заявки удалены, профили сохранены.")
