# -*- coding: utf-8 -*-
import sys
import io
import time
import base64
import threading
import keyboard
import ctypes
import google.generativeai as genai
from PIL import ImageGrab

# --- CONFIGURATION ---
GEMINI_API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
GEMINI_PRO_MODEL = "gemini-3.1-pro-preview"


genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Answer with only the final result. Do not include explanations, words, punctuation, or formatting."
)

# --- STATE ---
code_buffer = ""
code_index = 0
typing_mode = False
edit_mode = False
busy = False
typing_hook = None
state_lock = threading.Lock()
loading_event = threading.Event()


def set_numlock(state: bool):
    """Turns NumLock ON or OFF as an indicator."""
    # VK_NUMLOCK = 0x90
    current_state = ctypes.windll.user32.GetKeyState(0x90) & 1
    if bool(current_state) != state:
        keyboard.send('num lock')

def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)


def loading_heartbeat():
    """Blinks NumLock while loading_event is set."""
    while loading_event.is_set():
        set_numlock(True)
        time.sleep(0.3)
        set_numlock(False)
        time.sleep(0.3)


def get_screenshot_image():
    try:
        # Capture all monitors
        img = ImageGrab.grab(all_screens=True)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        log(f"Screenshot error: {e}", "WARN")
        return None


def strip_markdown(code):
    code = code.replace('\r', '').strip()
    if code.startswith("```"):
        lines = code.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


def adjust_for_autoindent(code):
    """
    Transform code so it types correctly in a VS Code-like editor with auto-indent.
    Tracks where the editor cursor will be (in spaces) when we start typing each line.
    - After a ':' line, editor adds 4 spaces on Enter.
    - If next line needs LESS indent than cursor pos: prepend \\x08 backspaces.
    - If next line needs MORE indent than cursor pos: prepend extra spaces.
    """
    lines = code.split('\n')
    result = []
    editor_indent = 0  # spaces the editor cursor is at when starting a new line

    for line in lines:
        if not line.strip():
            result.append('')
            # Empty line: editor keeps same cursor indent, we don't change editor_indent
            continue

        original_indent = len(line) - len(line.lstrip(' '))
        content = line.lstrip(' ')

        if original_indent < editor_indent:
            # We need to outdent. VS Code and similar editors delete one full 
            # indent level (typically 4 spaces) per backspace on an empty auto-indented line.
            outdent_levels = (editor_indent - original_indent) // 4
            result.append('\x08' * outdent_levels + content)
        else:
            # We need to indent more
            extra_spaces = original_indent - editor_indent
            result.append(' ' * extra_spaces + content)

        # After typing this line + Enter, editor will move cursor to:
        if content.rstrip().endswith(':'):
            editor_indent = original_indent +4  # auto-indent after colon
        else:
            editor_indent = original_indent      # keep same level
    
    return '\n'.join(result)


def generate_code(image_bytes, model_name=GEMINI_MODEL):
    model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=SYSTEM_INSTRUCTION
    )
    img_part = {
        "inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(image_bytes).decode()
        }
    }
    response = model.generate_content(
        ["Return exactly the answer and nothing else.", img_part]
    )
    raw = response.text
    code = strip_markdown(raw)
    return adjust_for_autoindent(code)


def type_one_char(ch):
    """Type a single character using the keyboard library (synchronous)."""
    if ch == '\n':
        keyboard.press_and_release('enter')
    elif ch == '\t':
        keyboard.press_and_release('tab')
    elif ch == '\x08':  # backspace (\b)
        keyboard.press_and_release('backspace')
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
    global typing_hook, typing_mode, edit_mode
    typing_mode = False
    edit_mode = False
    if typing_hook is not None:
        keyboard.unhook(typing_hook)
        typing_hook = None
    if reason:
        log(reason)


import queue

typing_queue = queue.Queue()

def typing_worker():
    while True:
        ch = typing_queue.get()
        if ch is None:
            break
        type_one_char(ch)
        typing_queue.task_done()

threading.Thread(target=typing_worker, daemon=True).start()

def on_typing_key(event):
    """Intercepts ALL keys when typing_mode is active."""
    global code_index, edit_mode

    # We must suppress both down and up events to stop them reaching the editor
    is_down = event.event_type == keyboard.KEY_DOWN

    # F8 toggles edit mode
    if event.name == 'f8':
        if is_down:
            edit_mode = not edit_mode
            status = "ON (You can type freely)" if edit_mode else "OFF (Code typing resumed)"
            log(f"Edit Mode: {status}")
        return False  # Always suppress the F8 key itself

    # If edit mode is active, pass ALL keys to the OS
    if edit_mode:
        return True

    # ESC cancels
    if event.name == 'esc':
        if is_down:
            stop_typing_mode("Typing cancelled.")
        return False

    # Backspace — undo last typed char
    if event.name == 'backspace':
        if is_down:
            with state_lock:
                if code_index > 0:
                    code_index -= 1
            typing_queue.put('\x08')
        return False

    # RightShift or Ctrl+RightShift re-captures
    is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl') or keyboard.is_pressed('right ctrl')
    if event.name in ('right shift', 'rightshift') or event.scan_code == 54:
        if is_down:
            model_to_use = GEMINI_PRO_MODEL if is_ctrl else GEMINI_MODEL
            stop_typing_mode(f"Hotkey pressed — restarting capture ({model_to_use}).")
            threading.Thread(target=capture_and_generate, args=(model_to_use,), daemon=True).start()
        return False

    # Only type on key down (ignore key up events, but still suppress them)
    if is_down:
        with state_lock:
            if not typing_mode or not code_buffer:
                return False
            if code_index >= len(code_buffer):
                stop_typing_mode("All code typed!")
                return False
            ch = code_buffer[code_index]
            code_index += 1

        # Put character in queue so hook returns instantly
        typing_queue.put(ch)

    return False  # suppress original keypress


def capture_and_generate(model_name=GEMINI_MODEL):
    global code_buffer, code_index, typing_mode, busy, edit_mode

    with state_lock:
        if busy:
            log("Already busy, please wait...", "WARN")
            return
        busy = True

    set_numlock(True)  # Turn ON NumLock indicator

    try:
        loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()

        log("Taking screenshot...")
        image_bytes = get_screenshot_image()
        if image_bytes is None:
            log("Failed to take screenshot!", "WARN")
            return

        log(f"Screenshot taken ({len(image_bytes)} bytes). Asking Gemini ({model_name})...")
        code = generate_code(image_bytes, model_name=model_name)

        if not code:
            log("Gemini returned nothing.", "WARN")
            return

        with state_lock:
            code_buffer = code
            code_index = 0
            typing_mode = True
            edit_mode = False

        log(f"Ready! {len(code)} chars. Switch to editor, press ANY key.")
        log("  Press F8 to toggle Edit Mode (pause typing)")
        log(f"  Preview: {code[:50].replace(chr(10), '↵')}...")
        start_typing_mode()

    except Exception as e:
        log(f"Error: {e}", "ERROR")
    finally:
        loading_event.clear()
        set_numlock(False)  # Ensure NumLock is OFF
        with state_lock:
            busy = False


def on_press_global(event):
    """Passive listener — triggers capture on RightShift."""
    if event.event_type != keyboard.KEY_DOWN:
        return
    is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl') or keyboard.is_pressed('right ctrl')
    if event.name in ('right shift', 'rightshift') or event.scan_code == 54:
        if not typing_mode:
            model_to_use = GEMINI_PRO_MODEL if is_ctrl else GEMINI_MODEL
            threading.Thread(target=capture_and_generate, args=(model_to_use,), daemon=True).start()


def main():
    log("=== Hacker Typing Assistant ===")
    log("RightShift      = capture screenshot + generate code (Flash)")
    log("Ctrl+RightShift = capture screenshot + generate code (Smart/Pro)")
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