# -*- coding: utf-8 -*-
import sys
import io
import time
import base64
import threading
import hashlib
import keyboard
import ctypes
import win32api
import win32con
import google.generativeai as genai
from PIL import ImageGrab

# --- CONFIGURATION ---
GEMINI_API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
MODEL_FLASH = "gemini-3.1-flash-lite-preview"
MODEL_PRO = "gemini-3.1-pro-preview"
MODEL_SOLVER = "gemini-3-flash-preview"

# Virtual Key Codes
VK_NUM = 0x90
VK_CAPS = 0x14
VK_SCROLL = 0x91

genai.configure(api_key=GEMINI_API_KEY)

# --- SYSTEM PROMPTS ---
TYPER_INSTRUCTION = (
    "Answer with only the final result. Do not include explanations, words, punctuation, or formatting."
)

SOLVER_INSTRUCTION = (
    "You are an Elite Solver. Solve the Math/Physics/English question in the image. "
    "Reason internally but Output ONLY the uppercase letter (A, B, C, or D). "
    "No text, no explanation. If there is answer marked already recheck it and output right answer."
)

# indicator states for Solver: (NumLock, CapsLock, ScrollLock)
INDICATOR_MAP = {
    "A": (True,  False, False),
    "B": (False, True,  False),
    "C": (False, False, True),
    "D": (True,  True,  True),
    "?": (False, False, False),
}

# --- STATE ---
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # "SOLVER" or "TYPER"
        self.busy = False
        self.lock = threading.Lock()
        
        # Typer Specific
        self.code_buffer = ""
        self.code_index = 0
        self.typing_active = False
        self.edit_mode = False
        self.typing_hook = None
        
        # Solver Specific
        self.last_img_hash = None
        self.last_letter = "?"
        
        # Global
        self.loading_event = threading.Event()

state = AssistantState()

# --- UTILITIES ---
def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def get_toggle_state(vk_code: int) -> bool:
    return (win32api.GetKeyState(vk_code) & 1) != 0

def press_key(vk_code: int):
    win32api.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.01)
    win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)

def set_leds(num=None, caps=None, scroll=None):
    """Sets keyboard LEDs to requested states (None means keep current)."""
    if num is not None and get_toggle_state(VK_NUM) != num:
        press_key(VK_NUM)
    if caps is not None and get_toggle_state(VK_CAPS) != caps:
        press_key(VK_CAPS)
    if scroll is not None and get_toggle_state(VK_SCROLL) != scroll:
        press_key(VK_SCROLL)

def loading_heartbeat():
    """Blinks indicators while loading_event is set."""
    while state.loading_event.is_set():
        if state.mode == "SOLVER":
            # Solver indicator: NumLock
            set_leds(num=True)
            time.sleep(0.3)
            set_leds(num=False)
            time.sleep(0.3)
        else:
            # Typer indicator: NumLock + ScrollLock
            set_leds(num=True, scroll=True)
            time.sleep(0.3)
            set_leds(num=False, scroll=False)
            time.sleep(0.3)
    # Ensure off when done
    set_leds(num=False, scroll=False)

def get_screenshot():
    try:
        img = ImageGrab.grab(all_screens=True)
        return img
    except Exception as e:
        log(f"Screenshot error: {e}", "WARN")
        return None

def img_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

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
    lines = code.split('\n')
    result = []
    editor_indent = 0
    for line in lines:
        if not line.strip():
            result.append('')
            continue
        original_indent = len(line) - len(line.lstrip(' '))
        content = line.lstrip(' ')
        if original_indent < editor_indent:
            outdent_levels = (editor_indent - original_indent) // 4
            result.append('\x08' * outdent_levels + content)
        else:
            extra_spaces = original_indent - editor_indent
            result.append(' ' * extra_spaces + content)
        if content.rstrip().endswith(':'):
            editor_indent = original_indent + 4
        else:
            editor_indent = original_indent
    return '\n'.join(result)

# --- TYPER LOGIC ---
def type_one_char(ch):
    if ch == '\n': keyboard.press_and_release('enter')
    elif ch == '\t': keyboard.press_and_release('tab')
    elif ch == '\x08': keyboard.press_and_release('backspace')
    elif ch == ' ': keyboard.press_and_release('space')
    else: keyboard.write(ch)

import queue
typing_queue = queue.Queue()

def typing_worker():
    while True:
        ch = typing_queue.get()
        if ch is None: break
        type_one_char(ch)
        typing_queue.task_done()

threading.Thread(target=typing_worker, daemon=True).start()

def on_typing_key(event):
    is_down = event.event_type == keyboard.KEY_DOWN
    if event.name == 'f8':
        if is_down:
            state.edit_mode = not state.edit_mode
            log(f"Edit Mode: {'ON' if state.edit_mode else 'OFF'}")
        return False
    if state.edit_mode: return True
    if event.name == 'esc':
        if is_down: stop_typing_mode("Typing cancelled.")
        return False
    if event.name == 'backspace':
        if is_down:
            with state.lock:
                if state.code_index > 0: state.code_index -= 1
            typing_queue.put('\x08')
        return False
    
    # Check for mode switch or recapture while in typing mode
    if event.name == 'f9' and is_down:
        stop_typing_mode("Mode switch requested.")
        toggle_mode()
        return False

    if is_down:
        with state.lock:
            if not state.typing_active or state.code_index >= len(state.code_buffer):
                if state.code_index >= len(state.code_buffer):
                    stop_typing_mode("Done typing!")
                return False
            ch = state.code_buffer[state.code_index]
            state.code_index += 1
        typing_queue.put(ch)
    return False

def start_typing_mode():
    if state.typing_hook is None:
        state.typing_hook = keyboard.hook(on_typing_key, suppress=True)
    log("Typing ACTIVE - Press ANY key to type code.")

def stop_typing_mode(reason=""):
    state.typing_active = False
    if state.typing_hook:
        keyboard.unhook(state.typing_hook)
        state.typing_hook = None
    if reason: log(reason)

# --- ACTION HANDLERS ---
def handle_typer_action(model_name=MODEL_FLASH):
    with state.lock:
        if state.busy: return
        state.busy = True
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        img = get_screenshot()
        if img:
            img_data = img_to_bytes(img)
            log(f"Typing: Capture taken. Asking Gemini ({model_name})...")
            gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=TYPER_INSTRUCTION)
            response = gen_model.generate_content([
                "Return code/text for this image.", 
                {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_data).decode()}}
            ])
            code = adjust_for_autoindent(strip_markdown(response.text))
            
            with state.lock:
                state.code_buffer = code
                state.code_index = 0
                state.typing_active = True
            log(f"Ready! {len(code)} chars. Switch to editor.")
            start_typing_mode()
    except Exception as e:
        log(f"Typer Error: {e}", "ERROR")
    finally:
        state.loading_event.clear()
        with state.lock: state.busy = False

def handle_solver_action():
    with state.lock:
        if state.busy: return
        state.busy = True
    
    try:
        img = get_screenshot()
        if not img: return
        
        # Hash check to avoid duplicate work if held down
        img_bytes = img_to_bytes(img)
        curr_hash = hashlib.sha256(img_bytes).hexdigest()
        if curr_hash == state.last_img_hash:
            ans = state.last_letter
            log(f"Solver: Same image detected. Answer: {ans}")
            states = INDICATOR_MAP.get(ans, INDICATOR_MAP["?"])
            set_leds(*states)
            return

        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        log("Solver: Analyzing question...")
        gen_model = genai.GenerativeModel(model_name=MODEL_SOLVER, system_instruction=SOLVER_INSTRUCTION)
        response = gen_model.generate_content([
            "Solve this question (A/B/C/D).",
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_bytes).decode()}}
        ])
        ans = response.text.strip().upper()[:1]
        if ans not in "ABCD": ans = "?"
        
        log(f"Solver: Answer discovered -> {ans}")
        state.last_img_hash = curr_hash
        state.last_letter = ans
        
        states = INDICATOR_MAP.get(ans, INDICATOR_MAP["?"])
        set_leds(*states)
        
    except Exception as e:
        log(f"Solver Error: {e}", "ERROR")
    finally:
        state.loading_event.clear()
        with state.lock: state.busy = False

def toggle_mode():
    with state.lock:
        if state.mode == "SOLVER":
            state.mode = "TYPER"
        else:
            state.mode = "SOLVER"
        log(f"MODE SWITCHED: Now in {state.mode} mode.")
    # Blink both as feedback
    set_leds(True, True, True)
    time.sleep(0.2)
    set_leds(False, False, False)

def on_global_hotkey(event):
    if event.event_type != keyboard.KEY_DOWN: return
    
    # Mode switch
    if event.name == 'f9':
        toggle_mode()
        return

    # Trigger (Right Shift)
    is_rshift = (event.name in ('right shift', 'rightshift') or event.scan_code == 54)
    if is_rshift:
        is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl') or keyboard.is_pressed('right ctrl')
        if state.mode == "SOLVER":
            threading.Thread(target=handle_solver_action, daemon=True).start()
        else:
            if not state.typing_active:
                model = MODEL_PRO if is_ctrl else MODEL_FLASH
                threading.Thread(target=handle_typer_action, args=(model,), daemon=True).start()

def main():
    log("=== OMNI ASSISTANT MONOLITH ===")
    log("F9            : Switch Mode (Solver <-> Typer)")
    log("RightShift    : Capture & Execute Action")
    log("Default Mode  : SOLVER")
    log("-------------------------------")
    
    keyboard.hook(on_global_hotkey)
    
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log("Exiting...")

if __name__ == "__main__":
    main()
