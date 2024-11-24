import sqlite3
import os

from modules.domain import Domain


saved_path: str = os.path.join(os.getcwd(), "appdata", "userdata.db")
def ensure_directory_exists(path: str):
    directory = os.path.dirname(path)
    if not os.path.exists(directory):
        os.makedirs(directory)

def init_db():
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS domains (
            domain TEXT UNIQUE,
            uuid TEXT PRIMARY KEY,
            tld TEXT,
            type BOOLEAN
        )
        ''')
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS datas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT,
            key TEXT,
            value TEXT
        )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tlds (
                tld TEXT UNIQUE,
                idna TEXT,
                whois TEXT
            )
            ''')

        conn.commit()


def update_data(uuid: str, data: dict):
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        for key, value in data.items():
            cursor.execute('''
            INSERT OR REPLACE INTO datas (uuid, key, value)
            VALUES (?, ?, ?)
            ''', (uuid, key, value))
        conn.commit()


def update_domain(domain: Domain):
    name: str = domain.name
    uuid: str = domain.uuid
    tld: str = domain.tld
    type: bool = domain.active
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        INSERT OR REPLACE INTO domains (domain, uuid, tld, type)
        VALUES (?, ?, ?, ?)
        ''', (name, uuid, tld, type))
        conn.commit()

def get_data(uuid: str, key: str):
    """
    通过 uuid 与 key 获取数据
    不存在的数据返回 None
    :param uuid:
    :param key:
    :return:
    """
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT value FROM datas WHERE uuid = ? AND key = ?
        ''', (uuid, key))
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            return None

def get_domain(uuid: str) -> Domain:
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT domain, uuid, tld, type FROM domains WHERE uuid = ?
        ''', (uuid,))
        result = cursor.fetchone()
        if result:
            domain = Domain(result[0])
            domain.uuid = result[1]
            domain.tld = result[2]
            domain.active = result[3]
            return domain
        else:
            return None

def get_active_domains() -> list[Domain]:
    with sqlite3.connect(saved_path) as conn:
        cursor = conn.cursor()
        cursor.execute('''
        SELECT domain, uuid, tld, type FROM domains WHERE type = 1
        ''')
        result = cursor.fetchall()
        domains = []
        for domain in result:
            d = Domain(domain[0])
            d.uuid = domain[1]
            d.tld = domain[2]
            d.active = domain[3]
            domains.append(d)
        return domains

ensure_directory_exists(saved_path)
init_db()
