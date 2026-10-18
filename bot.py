import urllib.request
import urllib.parse
import json
import time
import os
import sys
import threading
import subprocess
import tempfile
import base64

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

def send_video_file(chat_id, file_path):
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

# ---------- LLM: три источника ----------
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
    except Exception as e:
        print(f"[!] Pollinations LLM: {e}")
        return None

def ask_llm_inferenceport(chat_id, user_text):
    # InferencePort прокси (без ключа)
    url = "http://localhost:8080/v1/chat/completions"  # если развернёшь прокси локально
    # Но для Railway проще использовать публичный эндпоинт? В README его нет.
    # Поэтому пока пропускаем.
    return None

def ask_llm_huggingface(chat_id, user_text):
    if not HF_TOKEN:
        return None
    model = "Qwen/Qwen2.5-7B-Instruct"
    url = f"https://api-inference.huggingface.co/models/{model}"
    prompt = f"<|im_start|>system\n{SYSTEM_PROMPT}<|im_end|>\n<|im_start|>user\n{user_text}<|im_end|>\n<|im_start|>assistant\n"
    payload = json.dumps({
        "inputs": prompt,
        "parameters": {"max_new_tokens": 500, "return_full_text": False}
    }).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode())
            if isinstance(data, list) and data:
                return data[0].get("generated_text", "").strip()
    except Exception as e:
        print(f"[!] HF LLM: {e}")
    return None

def ask_llm(chat_id, user_text):
    answer = ask_llm_pollinations(chat_id, user_text)
    if not answer:
        answer = ask_llm_huggingface(chat_id, user_text)
    if not answer:
        answer = "Извини, сейчас не могу ответить. Попробуй позже."

    if answer != "Извини, сейчас не могу ответить. Попробуй позже.":
        with HISTORY_LOCK:
            h = HISTORY.get(chat_id, [])
            h.append({"role": "user", "content": user_text})
            h.append({"role": "assistant", "content": answer})
            HISTORY[chat_id] = h[-MAX_HISTORY:]
    return answer

# ---------- Изображения ----------
def image_url(prompt):
    q = urllib.parse.quote(prompt)
    return f"https://image.pollinations.ai/prompt/{q}?width=1024&height=1024&model=flux&nologo=true"

# ---------- Видео: Agnes AI (встроенный ключ) ----------
def generate_video_agnes(prompt, duration=5):
    """Вызывает agnes-video-generator через subprocess."""
    try:
        # Библиотека ставится через pip, но у неё есть CLI-скрипт generate_video.py
        # Ищем его в PATH или в папке пакета.
        script = "generate_video.py"
        # Проверяем, есть ли скрипт
        import shutil
        if not shutil.which(script):
            # Пробуем найти в site-packages
            import site
            for p in site.getsitepackages():
                candidate = os.path.join(p, "agnes_video_generator", "generate_video.py")
                if os.path.exists(candidate):
                    script = candidate
                    break
        if not os.path.exists(script) and not shutil.which(script):
            print("[!] generate_video.py не найден. Установи agnes-video-generator")
            return None

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
        tmp.close()
        output_path = tmp.name

        cmd = [sys.executable, script, prompt, "--duration", str(duration), "--output", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode != 0:
            print(f"[!] Agnes AI error: {result.stderr}")
            return None
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
        return None
    except Exception as e:
        print(f"[!] Agnes AI exception: {e}")
        return None

# ---------- Видео: InferencePort (прокси, без ключа) ----------
def generate_video_inferenceport(prompt):
    """
    Прямой вызов InferencePort AI (без прокси).
    В README указан upstream: https://sharktide-lightning.hf.space
    Но структура API может быть /gen/*
    Пробуем синхронный эндпоинт.
    """
    url = "https://sharktide-lightning.hf.space/gen/video"
    payload = json.dumps({"prompt": prompt, "model": "wan-fast"}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            # Может вернуть b64_json или URL
            data = json.loads(r.read().decode())
            if "video" in data:
                video_bytes = base64.b64decode(data["video"])
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                tmp.write(video_bytes)
                tmp.close()
                return tmp.name
            elif "url" in data:
                return data["url"]
    except Exception as e:
        print(f"[!] InferencePort video: {e}")
    return None

def search_ddg(query):
    url = f"https://api.duckduckgo.com/?q={urllib.parse.quote(query)}&format=json&no_html=1"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data.get("AbstractText") or None
    except Exception as e:
        print(f"[!] DDG: {e}")
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

        # 1. Agnes AI
        file_path = generate_video_agnes(prompt, duration=5)
        if file_path:
            send_message(chat_id, "📤 Отправляю...")
            result = send_video_file(chat_id, file_path)
            if result and result.get("ok"):
                os.unlink(file_path)
                return
            else:
                os.unlink(file_path)

        # 2. InferencePort
        result_url = generate_video_inferenceport(prompt)
        if result_url:
            if result_url.startswith("http"):
                send_message(chat_id, "📤 Отправляю...")
                # отправка по URL
                res = api_call("sendVideo", _timeout=300, chat_id=chat_id, video=result_url)
                if res and res.get("ok"):
                    return
            elif os.path.exists(result_url):
                send_message(chat_id, "📤 Отправляю...")
                result = send_video_file(chat_id, result_url)
                if result and result.get("ok"):
                    os.unlink(result_url)
                    return

        # 3. Всё упало
        send_message(chat_id,
            "❌ Не удалось сгенерировать видео.\n\n"
            "Бесплатные видео-API сейчас нестабильны. Попробуй позже или используй /image."
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
