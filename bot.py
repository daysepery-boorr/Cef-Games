import urllib.request
import urllib.parse
import json
import time
import os
import sys

TOKEN = os.environ.get("BOT_TOKEN", "")
if not TOKEN:
    print("[!] BOT_TOKEN не задан")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TOKEN}"

# ---- Память диалога (по chat_id) ----
HISTORY = {}
MAX_HISTORY = 8  # последние 8 сообщений

SYSTEM_PROMPT = (
    "Ты полезный ассистент. Отвечай на русском языке, кратко и по делу. "
    "На простые вопросы (математика, факты, объяснения) отвечай сразу, без воды."
)

def api_call(method, timeout=60, **params):
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[!] {method}: {e}")
        return None

def send_message(chat_id, text, parse_mode="HTML"):
    # Telegram лимит 4096 символов
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        api_call("sendMessage", chat_id=chat_id, text=chunk, parse_mode=parse_mode)

def send_photo(chat_id, photo_url, caption=""):
    return api_call("sendPhoto", timeout=120, chat_id=chat_id, photo=photo_url, caption=caption)

def send_video(chat_id, video_url):
    return api_call("sendVideo", timeout=300, chat_id=chat_id, video=video_url)

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    result = api_call("getUpdates", timeout=40, **params)
    if result and result.get("ok"):
        return result.get("result", [])
    return []

# ---- LLM через Pollinations (бесплатно, без ключа) ----
def ask_llm(chat_id, user_text):
    history = HISTORY.get(chat_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    url = "https://text.pollinations.ai/openai"
    payload = json.dumps({
        "model": "openai",
        "messages": messages
    }).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
            answer = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"Не смог ответить: {e}"

    # сохраняем в историю
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": answer})
    HISTORY[chat_id] = history[-MAX_HISTORY:]

    return answer

def image_url(prompt):
    q = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&model=flux&nologo=true"

def video_url(prompt):
    q = urllib.parse.quote(prompt)
    return f"https://video.pollinations.ai/prompt/{q}?model=seedance"

def search_ddg(query):
    url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        text = data.get("AbstractText") or ""
        if not text:
            return None  # ничего не нашёл, вернём None — обработаем LLM'ом
        return text
    except Exception as e:
        return f"Ошибка поиска: {e}"

def handle(update):
    if "message" not in update:
        return
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if not text:
        return

    if text.startswith("/start"):
        HISTORY.pop(chat_id, None)
        send_message(chat_id,
            "Привет! Я твой бот.\n\n"
            "💬 Просто напиши вопрос — отвечу.\n"
            "🔍 /search &lt;запрос&gt; — поиск в интернете\n"
            "🎨 /image &lt;описание&gt; — картинка (англ. лучше)\n"
            "🎬 /video &lt;описание&gt; — видео (1-4 мин)\n"
            "🧹 /reset — очистить память диалога\n\n"
            "Например: <i>сколько будет 2 умножить на 2?</i>"
        )
        return

    if text.startswith("/reset"):
        HISTORY.pop(chat_id, None)
        send_message(chat_id, "🧹 Память очищена.")
        return

    if text.startswith("/image"):
        prompt = text.replace("/image", "", 1).strip()
        if not prompt:
            send_message(chat_id, "Напиши описание: /image cat in space")
            return
        send_message(chat_id, "🎨 Генерирую...")
        result = send_photo(chat_id, image_url(prompt))
        if not result or not result.get("ok"):
            send_message(chat_id, "❌ Не получилось. Попробуй ещё раз.")
        return

    if text.startswith("/video"):
        prompt = text.replace("/video", "", 1).strip()
        if not prompt:
            send_message(chat_id, "Опиши видео: /video a man dancing")
            return
        send_message(chat_id, "🎬 Генерирую видео. Займёт 1-4 минуты. Не закрывай чат...")
        try:
            result = send_video(chat_id, video_url(prompt))
            if not result or not result.get("ok"):
                send_message(chat_id, "❌ Сервер перегружен, попробуй через минуту.")
        except Exception as e:
            send_message(chat_id, f"❌ Ошибка: {e}")
        return

    if text.startswith("/search"):
        query = text.replace("/search", "", 1).strip()
        if not query:
            send_message(chat_id, "Что искать: /search weather Kyiv")
            return
        send_message(chat_id, "🔍 Ищу...")
        result = search_ddg(query)
        if result is None:
            result = ask_llm(chat_id, query)
        send_message(chat_id, result)
        return

    # ---- Обычный текст → LLM ----
    send_message(chat_id, "💭 Думаю...")
    answer = ask_llm(chat_id, text)
    send_message(chat_id, answer)

def main():
    print("[+] Bot started")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                try:
                    handle(u)
                except Exception as e:
                    print(f"[!] handle error: {e}")
        except Exception as e:
            print(f"[!] loop error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
