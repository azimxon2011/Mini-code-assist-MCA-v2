# -*- coding: utf-8 -*-
import sys
import io
import time
import base64
import threading
import keyboard
import google.generativeai as genai
from PIL import ImageGrab

# --- CONFIGURATION ---
GEMINI_API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
GEMINI_MODEL = "gemini-2.5-flash"

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Ты — ученик 8-го класса на олимпиаде по программированию. Тебе лень, ты пишешь просто и быстро.\n"
    "Твои правила:\n"
    "1. Пиши простой код, как восьмиклассник. Не парься над оптимизацией.\n"
    "2. Используй простые имена переменных: a, b, x, y, s, res, cnt.\n"
    "3. Не добавляй лишних импортов.\n"
    "4. Возвращай ТОЛЬКО код. Без markdown, без лишних пояснений.\n"
)

# --- STATE ---
code_buffer = ""
code_index = 0
typing_mode = False
busy = False
typing_hook = None
state_lock = threading.Lock()


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def get_clipboard_image():
    try:
        img = ImageGrab.grabclipboard()
        if img is None:
            return None
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        log(f"Clipboard error: {e}", "WARN")
        return None


def strip_markdown(code):
    code = code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


def generate_code(image_bytes):
    model = genai.GenerativeModel(
        model_name=GEMINI_MODEL,
        system_instruction=SYSTEM_INSTRUCTION
    )
    img_part = {
        "inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(image_bytes).decode()
        }
    }
    response = model.generate_content(
        ["Напиши Python код по этому изображению. ТОЛЬКО код, без объяснений.", img_part]
    )
    return strip_markdown(response.text)


def type_one_char(ch):
    """Type a single character using the keyboard library (synchronous)."""
    if ch == '\n':
        keyboard.press_and_release('enter')
    elif ch == '\t':
        keyboard.press_and_release('tab')
    elif ch == ' ':
        keyboard.press_and_release('space')
    else:
        keyboard.write(ch)


def start_typing_mode():
    global typing_hook
    if typing_hook is None:
        typing_hook = keyboard.hook(on_typing_key, suppress=True)
    log(f"Typing mode ON — switch to your editor and press any key. ({len(code_buffer)} chars)")


def stop_typing_mode(reason=""):
    global typing_hook, typing_mode
    typing_mode = False
    if typing_hook is not None:
        keyboard.unhook(typing_hook)
        typing_hook = None
    if reason:
        log(reason)


def on_typing_key(event):
    """Intercepts ALL keys when typing_mode is active."""
    global code_index

    if event.event_type != keyboard.KEY_DOWN:
        return False  # suppress key-up events too

    # ESC cancels
    if event.name == 'esc':
        stop_typing_mode("Typing cancelled.")
        return False

    # RightShift re-captures new image
    if event.name in ('right shift', 'rightshift') or event.scan_code == 54:
        stop_typing_mode("RightShift pressed — restarting capture.")
        threading.Thread(target=capture_and_generate, daemon=True).start()
        return False

    with state_lock:
        if not typing_mode or not code_buffer:
            return False
        if code_index >= len(code_buffer):
            stop_typing_mode("All code typed!")
            return False
        ch = code_buffer[code_index]
        code_index += 1

    # Type synchronously (no thread) — avoids race conditions
    type_one_char(ch)
    return False  # suppress original keypress


def capture_and_generate():
    global code_buffer, code_index, typing_mode, busy

    with state_lock:
        if busy:
            log("Already busy, please wait...", "WARN")
            return
        busy = True

    try:
        log("Reading clipboard image...")
        image_bytes = get_clipboard_image()
        if image_bytes is None:
            log("No image in clipboard! Copy a screenshot first.", "WARN")
            return

        log(f"Image captured ({len(image_bytes)} bytes). Asking Gemini...")
        code = generate_code(image_bytes)

        if not code:
            log("Gemini returned nothing.", "WARN")
            return

        with state_lock:
            code_buffer = code
            code_index = 0
            typing_mode = True

        log(f"Ready! {len(code)} chars. Switch to editor, press ANY key.")
        log(f"  Preview: {code[:50].replace(chr(10), '↵')}...")
        start_typing_mode()

    except Exception as e:
        log(f"Error: {e}", "ERROR")
    finally:
        with state_lock:
            busy = False


def on_press_global(event):
    """Passive listener — triggers capture on RightShift."""
    if event.event_type != keyboard.KEY_DOWN:
        return
    if event.name in ('right shift', 'rightshift') or event.scan_code == 54:
        if not typing_mode:
            threading.Thread(target=capture_and_generate, daemon=True).start()


def main():
    log("=== Hacker Typing Assistant ===")
    log("RightShift = capture screenshot + generate code")
    log("Any key    = type next code char (in typing mode)")
    log("ESC        = cancel typing mode")
    log("Ctrl+C     = exit")
    log("")
    log("[DEBUG] Press a few keys to confirm keyboard detection...")

    # Passive global hook — only listens, does NOT suppress
    keyboard.hook(on_press_global)

    # Debug: show first 3 key names
    count = [0]
    def debug_hook(e):
        if e.event_type == keyboard.KEY_DOWN and count[0] < 3:
            log(f"[DEBUG] key='{e.name}' scan={e.scan_code}")
            count[0] += 1
    keyboard.hook(debug_hook)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        stop_typing_mode()
        log("Exiting.")


if __name__ == "__main__":
    main()
