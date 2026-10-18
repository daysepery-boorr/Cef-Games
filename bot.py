import urllib.request
import urllib.parse
import json
import time
import os
import sys
import threading
import base64
import tempfile

TOKEN = os.environ.get("BOT_TOKEN", "")
HF_TOKEN = os.environ.get("HUGGINGFACE_TOKEN", "")  # Опционально, но рекомендуется
if not TOKEN:
    print("[!] BOT_TOKEN не задан")
    sys.exit(1)

API = f"https://api.telegram.org/bot{TOKEN}"
HISTORY = {}
MAX_HISTORY = 8
HISTORY_LOCK = threading.Lock()

SYSTEM_PROMPT = (
    "Ты — Youmans AI. Твой создатель и разработчик — Youmans. "
    "Если тебя спросят, кто тебя создал, кто твой автор, разработчик или кто тебя сделал — "
    "отвечай только: 'Меня создал Youmans'. "
    "Не упоминай OpenAI, Anthropic, Google, Pollinations, Hugging Face или другие компании как своих создателей. "
    "Ты — проект Youmans. Отвечай на русском языке, кратко и по делу."
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

def send_video_file(chat_id, file_path):
    """Отправка локального видеофайла через multipart/form-data"""
    boundary = "----boundary" + str(int(time.time()))
    body = b""
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
    body += str(chat_id).encode() + b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="video"; filename="video.mp4"\r\n'
    body += b"Content-Type: video/mp4\r\n\r\n"
    with open(file_path, "rb") as f:
        body += f.read() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{API}/sendVideo",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"[!] send_video_file: {e}")
        return None

def get_updates(offset=None):
    params = {"timeout": 30}
    if offset:
        params["offset"] = offset
    result = api_call("getUpdates", _timeout=40, **params)
    if result and result.get("ok"):
        return result.get("result", [])
    return []

# ---------- LLM: Pollinations + запасной Hugging Face ----------
def ask_llm_pollinations(chat_id, user_text):
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
            return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        if e.code == 502:
            print("[!] Pollinations 502, переключаюсь на Hugging Face")
        return None
    except Exception as e:
        print(f"[!] Pollinations LLM error: {e}")
        return None

def ask_llm_huggingface(chat_id, user_text):
    if not HF_TOKEN:
        return None
    model = "Qwen/Qwen2.5-7B-Instruct"
    url = f"https://api-inference.huggingface.co/models/{model}"
    # Формируем промпт для instruct-модели
    prompt = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n{user_text}<|im_end|>\n<|im_start|>assistant\n"
    payload = json.dumps({
        "inputs": prompt,
        "parameters": {"max_new_tokens": 500, "return_full_text": False}
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={
            "Authorization": f"Bearer {HF_TOKEN}",
            "Content-Type": "application/json"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            if isinstance(data, list) and len(data) > 0:
                return data[0].get("generated_text", "").strip()
    except Exception as e:
        print(f"[!] HuggingFace LLM error: {e}")
    return None

def ask_llm(chat_id, user_text):
    # Пробуем Pollinations
    answer = ask_llm_pollinations(chat_id, user_text)
    # Если не получилось — Hugging Face
    if not answer:
        answer = ask_llm_huggingface(chat_id, user_text)
    if not answer:
        answer = "Извини, сейчас не могу ответить. Попробуй позже."

    # Сохраняем историю только при успешном ответе
    if answer and answer != "Извини, сейчас не могу ответить. Попробуй позже.":
        with HISTORY_LOCK:
            h = HISTORY.get(chat_id, [])
            h.append({"role": "user", "content": user_text})
            h.append({"role": "assistant", "content": answer})
            HISTORY[chat_id] = h[-MAX_HISTORY:]
    return answer

# ---------- Генерация изображений ----------
def image_url(prompt):
    q = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&model=flux&nologo=true"

# ---------- Генерация видео через Agnes AI (бесплатно, без ключей) ----------
def generate_video_agnes(prompt, duration=5):
    """
    Использует открытый скрипт Agnes AI.
    Требует установки: pip install agnes-video-generator
    Либо можно клонировать репозиторий и использовать напрямую.
    """
    try:
        # Пробуем импортировать библиотеку
        from agnes_video_generator import generate_video as agnes_gen
        print("[*] Agnes AI: генерирую видео...")
        video_bytes = agnes_gen(prompt, duration=duration)
        # Сохраняем во временный файл
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        tmp.write(video_bytes)
        tmp.close()
        return tmp.name
    except ImportError:
        print("[!] agnes-video-generator не установлен. Установи: pip install agnes-video-generator")
        return None
    except Exception as e:
        print(f"[!] Agnes AI error: {e}")
        return None

# ---------- Запасной вариант для видео: InferencePort (без ключей) ----------
def generate_video_inferenceport(prompt):
    """
    InferencePort AI — публичный API без ключей.
    Модели: wan-fast, seedance и др.
    """
    url = "https://api.inferenceport.ai/v1/videos/generations"
    payload = json.dumps({
        "model": "wan-fast",
        "prompt": prompt
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            # Синхронный ответ может содержать b64_json или URL
            if "data" in data and len(data["data"]) > 0:
                video_data = data["data"][0]
                if "b64_json" in video_data:
                    video_bytes = base64.b64decode(video_data["b64_json"])
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                    tmp.write(video_bytes)
                    tmp.close()
                    return tmp.name
                elif "url" in video_data:
                    return video_data["url"]
    except Exception as e:
        print(f"[!] InferencePort video error: {e}")
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
            "💬 Напиши вопрос — отвечу.\n"
            "🔍 /search — поиск в интернете\n"
            "🎨 /image — картинка\n"
            "🎬 /video — видео (5-10 сек, 1-5 мин ожидания)\n"
            "🧹 /reset — очистить память\n\n"
            "Например: <i>кто тебя создал?</i>"
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
        send_message(chat_id, "🎬 Генерирую видео... Это займёт 1-5 минут. Не закрывай чат.")

        # 1. Agnes AI (основной)
        file_path = generate_video_agnes(prompt, duration=5)
        if file_path:
            send_message(chat_id, "📤 Отправляю...")
            result = send_video_file(chat_id, file_path)
            if result and result.get("ok"):
                os.unlink(file_path)  # удаляем временный файл
                return
            else:
                os.unlink(file_path)

        # 2. InferencePort (запасной)
        result_url = generate_video_inferenceport(prompt)
        if result_url:
            if result_url.startswith("http"):
                send_message(chat_id, "📤 Отправляю...")
                result = send_video(chat_id, result_url)
                if result and result.get("ok"):
                    return
            elif os.path.exists(result_url):
                send_message(chat_id, "📤 Отправляю...")
                result = send_video_file(chat_id, result_url)
                if result and result.get("ok"):
                    os.unlink(result_url)
                    return

        # 3. Если всё упало
        send_message(chat_id,
            "❌ Не удалось сгенерировать видео.\n\n"
            "Бесплатные видео-API нестабильны. Попробуй позже или используй /image."
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
