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

def api_call(method, **params):
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[!] {method}: {e}")
        return None

def send_message(chat_id, text, parse_mode="HTML"):
    return api_call("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode)

def send_photo(chat_id, photo_url, caption=""):
    return api_call("sendPhoto", chat_id=chat_id, photo=photo_url, caption=caption)

def send_video(chat_id, video_url):
    return api_call("sendVideo", chat_id=chat_id, video=video_url)

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    result = api_call("getUpdates", **params)
    if result and result.get("ok"):
        return result.get("result", [])
    return []

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
            return "Ничего не нашёл. Попробуй переформулировать."
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
        send_message(chat_id,
            "Привет! Я твой бот.\n\n"
            "🔍 /search &lt;запрос&gt; — поиск в интернете\n"
            "🎨 /image &lt;описание&gt; — картинка\n"
            "🎬 /video &lt;описание&gt; — видео\n\n"
            "Или просто напиши вопрос."
        )
    elif text.startswith("/image"):
        prompt = text.replace("/image", "", 1).strip()
        if not prompt:
            send_message(chat_id, "Напиши описание: /image кот в космосе")
            return
        send_message(chat_id, "🎨 Генерирую...")
        send_photo(chat_id, image_url(prompt))
    elif text.startswith("/video"):
        prompt = text.replace("/video", "", 1).strip()
        if not prompt:
            send_message(chat_id, "Опиши видео: /video летающий кит")
            return
        send_message(chat_id, "🎬 Генерирую... может занять время.")
        send_video(chat_id, video_url(prompt))
    elif text.startswith("/search"):
        query = text.replace("/search", "", 1).strip()
        if not query:
            send_message(chat_id, "Что искать: /search погода Киев")
            return
        send_message(chat_id, "🔍 Ищу...")
        send_message(chat_id, search_ddg(query))
    else:
        send_message(chat_id, "🤔 Ищу...")
        send_message(chat_id, search_ddg(text))

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
