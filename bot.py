import urllib.request
import urllib.parse
import json
import time
import os
import sys
import threading

TOKEN = os.environ.get("BOT_TOKEN", "")
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN", "")  # опционально
if not TOKEN:
    print("[!] BOT_TOKEN не задан")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TOKEN}"
HISTORY = {}
MAX_HISTORY = 8
HISTORY_LOCK = threading.Lock()

# ---- Создатель: Youmans ----
SYSTEM_PROMPT = (
    "Ты полезный ассистент по имени Youmans AI. "
    "Твой создатель и разработчик — Youmans. "
    "Если тебя спросят, кто тебя создал, кто твой автор, разработчик "
    "или кто тебя сделал — отвечай, что тебя создал Youmans. "
    "Не упоминай OpenAI, Anthropic, Google, Pollinations или другие компании "
    "как своих создателей. Ты — проект Youmans. "
    "Отвечай на русском языке, кратко и по делу."
)

def api_call(method, _timeout=60, **params):
    url = f"{API}/{method}"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=_timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[!] {method}: {e}")
        return None

def send_message(chat_id, text, parse_mode="HTML"):
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        api_call("sendMessage", chat_id=chat_id, text=chunk, parse_mode=parse_mode)

def send_photo(chat_id, photo_url, caption=""):
    return api_call("sendPhoto", _timeout=120, chat_id=chat_id, photo=photo_url, caption=caption)

def send_video(chat_id, video_url):
    return api_call("sendVideo", _timeout=300, chat_id=chat_id, video=video_url)

def send_document(chat_id, doc_url, caption=""):
    return api_call("sendDocument", _timeout=300, chat_id=chat_id, document=doc_url, caption=caption)

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    result = api_call("getUpdates", _timeout=40, **params)
    if result and result.get("ok"):
        return result.get("result", [])
    if result:
        print(f"[!] getUpdates: {result.get('description')}")
    return []

def ask_llm(chat_id, user_text):
    with HISTORY_LOCK:
        history = HISTORY.get(chat_id, [])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    url = "https://text.pollinations.ai/openai"
    payload = json.dumps({"model": "openai", "messages": messages}).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            answer = data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[!] LLM error: {e}")
        return f"Не смог ответить: {e}"

    with HISTORY_LOCK:
        h = HISTORY.get(chat_id, [])
        h.append({"role": "user", "content": user_text})
        h.append({"role": "assistant", "content": answer})
        HISTORY[chat_id] = h[-MAX_HISTORY:]

    return answer

def image_url(prompt):
    q = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&model=flux&nologo=true"

# ---- Генерация видео: три попытки, потом честный отказ ----
def try_video_pollinations(prompt):
    q = urllib.parse.quote(prompt)
    urls = [
        f"https://video.pollinations.ai/prompt/{q}?model=seedance",
        f"https://video.pollinations.ai/prompt/{q}",
        f"https://gen.pollinations.ai/video/{q}?model=seedance",
    ]
    for u in urls:
        try:
            req = urllib.request.Request(u, method="HEAD")
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status == 200:
                    return u
        except Exception as e:
            print(f"[!] video try {u[:60]}: {e}")
    return None

def try_video_huggingface(prompt):
    if not HF_TOKEN:
        return None
    # модель text-to-video от damo-vilab — бесплатный tier
    model = "damo-vilab/text-to-video-ms-1.7b"
    url = f"https://api-inference.huggingface.co/models/{model}"
    payload = json.dumps({"inputs": prompt}).encode()
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            video_bytes = r.read()
            if len(video_bytes) < 1000:
                return None
            # сохраняем локально
            path = f"/tmp/video_{int(time.time())}.mp4"
            with open(path, "wb") as f:
                f.write(video_bytes)
            return path
    except Exception as e:
        print(f"[!] HF video: {e}")
        return None

def search_ddg(query):
    url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data.get("AbstractText") or None
    except Exception as e:
        print(f"[!] DDG error: {e}")
        return None

def handle(update):
    if "message" not in update:
        return
    msg = update["message"]
    chat_id = msg["chat"]["id"]
    text = msg.get("text", "")
    if not text:
        return

    print(f"[>] {chat_id}: {text[:60]}")

    if text.startswith("/start"):
        with HISTORY_LOCK:
            HISTORY.pop(chat_id, None)
        send_message(chat_id,
            "Привет! Я <b>Youmans AI</b> — бот, созданный Youmans.\n\n"
            "💬 Просто напиши вопрос — отвечу.\n"
            "🔍 /search &lt;запрос&gt; — поиск в интернете\n"
            "🎨 /image &lt;описание&gt; — картинка (англ. лучше)\n"
            "🎬 /video &lt;описание&gt; — видео (нестабильно, 1-4 мин)\n"
            "🧹 /reset — очистить память диалога\n\n"
            "Например: <i>сколько будет 2 умножить на 2?</i>"
        )
        return

    if text.startswith("/reset"):
        with HISTORY_LOCK:
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
        send_message(chat_id, "🎬 Ищу рабочую модель видео... Займёт время.")

        # 1. пробуем HuggingFace (если токен есть)
        hf_path = try_video_huggingface(prompt)
        if hf_path:
            send_message(chat_id, "📤 Отправляю...")
            try:
                files = {"video": open(hf_path, "rb")}
                # отправка файла через requests-like нельзя — делаем multipart вручную
                boundary = "----boundary" + str(int(time.time()))
                body = b""
                body += f"--{boundary}\r\n".encode()
                body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
                body += str(chat_id).encode() + b"\r\n"
                body += f"--{boundary}\r\n".encode()
                body += b'Content-Disposition: form-data; name="video"; filename="video.mp4"\r\n'
                body += b"Content-Type: video/mp4\r\n\r\n"
                body += open(hf_path, "rb").read() + b"\r\n"
                body += f"--{boundary}--\r\n".encode()

                req = urllib.request.Request(
                    f"{API}/sendVideo",
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    print(f"[+] video sent: {r.status}")
                os.remove(hf_path)
                return
            except Exception as e:
                print(f"[!] send hf video: {e}")

        # 2. пробуем Pollinations
        url = try_video_pollinations(prompt)
        if url:
            send_message(chat_id, "📤 Отправляю...")
            result = send_video(chat_id, url)
            if result and result.get("ok"):
                return
            print(f"[!] pollinations send failed: {result}")

        # 3. всё упало — честно
        send_message(chat_id,
            "❌ Видео сейчас не работает.\n\n"
            "Бесплатные видео-API нестабильны — сервис либо перегружен, либо лёг.\n\n"
            "Попробуй позже или используй /image (картинки работают стабильно)."
        )
        return

    if text.startswith("/search"):
        query = text.replace("/search", "", 1).strip()
        if not query:
            send_message(chat_id, "Что искать: /search weather Kyiv")
            return
        send_message(chat_id, "🔍 Ищу...")
        result = search_ddg(query) or ask_llm(chat_id, query)
        send_message(chat_id, result)
        return

    send_message(chat_id, "💭 Думаю...")
    answer = ask_llm(chat_id, text)
    send_message(chat_id, answer)

def handle_async(update):
    threading.Thread(target=handle, args=(update,), daemon=True).start()

def main():
    print("[+] Youmans AI started")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                handle_async(u)
        except Exception as e:
            print(f"[!] loop error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
