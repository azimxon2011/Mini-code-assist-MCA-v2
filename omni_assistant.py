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
import tkinter as tk
from tkinter import ttk, messagebox
import re 

# --- STATE ---
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # "SOLVER" or "TYPER"
        self.busy = False
        self.lock = threading.Lock()
        
        # Configurable Settings
        self.api_key = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
        self.model_flash = "gemini-3-flash-preview"
        self.model_pro = "gemini-3.1-pro-preview"
        self.model_solver = "gemini-3-flash-preview"
        self.system_instr_typer = (
            "You are an Elite Competitive Programmer.\n"
            "Your rules:\n"
            "1. Write ULTRA-COMPACT(but readable) and efficient Python code. Solve the problem EXACTLY.\n"
            "2. NO Object-Oriented Programming (no classes). NO unnecessary imports.\n"
            "3. Use SINGLE-LETTER or 2-3 LETTER variable names (a, b, n, s, res). Minimalist code only.\n"
            "4. Do NOT leave blank lines. Keep the code vertically and horizontally tight.\n"
            "5. Preferred pattern for input: `a, b = map(int, input().split())` or similar clean idioms.\n"
            "6. Return ONLY the code. No markdown code blocks (no ```python), no explanations, no comments.\n"
            "7. DEDUCTION RULE: If you need to outdent (e.g., after a loop ends), "
            "output exactly ONE '\\x08' character. DO NOT USE THIS RULE unless breaking out of a block."
        )
        self.system_instr_solver = (
            "You are an Elite Solver. Solve the Math/Physics/English question in the image. "
            "Reason internally but Output ONLY the uppercase letter (A, B, C, or D). "
            "No text, no explanation. If there is answer marked already recheck it and output right answer."
        )
        
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
        self.menu_open = False

state = AssistantState()

# Initialize GenAI with default key
genai.configure(api_key=state.api_key)

# --- VIRTUAL KEYS ---
VK_NUM = 0x90
VK_CAPS = 0x14
VK_SCROLL = 0x91

# --- SYSTEM PROMPTS ---
TYPER_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Your rules:\n"
    "1. Write ULTRA-COMPACT and efficient Python code. Solve the problem EXACTLY.\n"
    "2. NO Object-Oriented Programming (no classes). NO unnecessary imports.\n"
    "3. Use SINGLE-LETTER or 2-3 LETTER variable names (a, b, n, s, res). Minimalist code only.\n"
    "4. Do NOT leave blank lines. Keep the code vertically and horizontally tight.\n"
    "5. Preferred pattern for input: `a, b = map(int, input().split())` or similar clean idioms.\n"
    "6. Return ONLY the code. No markdown code blocks (no ```python), no explanations, no comments.\n"
    "7. DEDUCTION RULE: If you need to outdent (e.g., after a loop ends), "
    "output exactly ONE '\\x08' character. DO NOT USE THIS RULE unless breaking out of a block."
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
            set_leds(num=True)
            time.sleep(0.3)
            set_leds(num=False)
            time.sleep(0.3)
        else:
            set_leds(num=True, scroll=True)
            time.sleep(0.3)
            set_leds(num=False, scroll=False)
            time.sleep(0.3)
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
    match = re.search(r"```(?:python)?\n?(.*?)\n?```", code, re.DOTALL | re.IGNORECASE)
    if match:
        code = match.group(1).strip()
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

# --- SETTINGS MENU (TKINTER) ---
def open_settings_menu():
    if state.menu_open: return
    state.menu_open = True
    
    root = tk.Tk()
    root.title("Settings")
    root.geometry("400x450")
    root.attributes('-topmost', True)
    
    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    def add_entry(label, var_name, is_text=False):
        ttk.Label(main_frame, text=label).pack(anchor=tk.W)
        if is_text:
            widget = tk.Text(main_frame, height=4, width=40, font=("Consolas", 9))
            widget.insert("1.0", getattr(state, var_name))
            widget.pack(fill=tk.X, pady=(0, 10))
            return widget
        else:
            widget = ttk.Entry(main_frame, width=40)
            widget.insert(0, getattr(state, var_name))
            widget.pack(fill=tk.X, pady=(0, 10))
            return widget

    key_e = add_entry("API Key", "api_key")
    flash_e = add_entry("Flash Model", "model_flash")
    pro_e = add_entry("Pro Model", "model_pro")
    solve_model_e = add_entry("Solver Model", "model_solver")
    typer_i = add_entry("Typer Instr", "system_instr_typer", True)
    solver_i = add_entry("Solver Instr", "system_instr_solver", True)

    def on_apply():
        with state.lock:
            state.api_key = key_e.get().strip()
            state.model_flash = flash_e.get().strip()
            state.model_pro = pro_e.get().strip()
            state.model_solver = solve_model_e.get().strip()
            state.system_instr_typer = typer_i.get("1.0", tk.END).strip()
            state.system_instr_solver = solver_i.get("1.0", tk.END).strip()
            genai.configure(api_key=state.api_key)
        log("Applied.")
        root.destroy()
        state.menu_open = False

    ttk.Button(main_frame, text="Apply", command=on_apply).pack(pady=5)

    def on_close():
        state.menu_open = False
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

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
    
    # Handled Keys first (Esc, F8, F9, Ctrl+I, Backspace)
    if event.name == 'f8':
        if is_down:
            state.edit_mode = not state.edit_mode
            log(f"Edit Mode: {'ON' if state.edit_mode else 'OFF'}")
        return False
    if state.edit_mode: return True
    
    if event.name == 'f9':
        if is_down:
            stop_typing_mode("Mode switch requested.")
            toggle_mode()
        return False

    if event.name == 'i' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
        if is_down:
            threading.Thread(target=open_settings_menu, daemon=True).start()
        return False

    if event.name == 'esc':
        if is_down: stop_typing_mode("Typing cancelled.")
        return False

    if event.name == 'backspace':
        if is_down:
            with state.lock:
                if state.code_index > 0: state.code_index -= 1
            typing_queue.put('\x08')
        return False
    
    # Typer Trigger
    if is_down:
        with state.lock:
            if not state.typing_active or state.code_index >= len(state.code_buffer):
                if state.code_index >= len(state.code_buffer) and state.typing_active:
                    stop_typing_mode("Done typing!")
                return False
            ch = state.code_buffer[state.code_index]
            state.code_index += 1
            # log(f"Trigger: '{event.name}' -> Char: '{ch}'")
        typing_queue.put(ch)
    return False # Strict suppression of ALL other keys
    

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
def handle_typer_action(model_name=None):
    with state.lock:
        if state.busy: return
        state.busy = True
        if model_name is None:
            # Determine based on Ctrl mod used at trigger time
            is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')
            model_name = state.model_pro if is_ctrl else state.model_flash
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        img = get_screenshot()
        if img:
            img_data = img_to_bytes(img)
            log(f"Typing: Capture taken. Asking Gemini ({model_name})...")
            gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=state.system_instr_typer)
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
        model_name = state.model_solver
    
    try:
        img = get_screenshot()
        if not img: return
        
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
        
        log(f"Solver: Analyzing... ({model_name})")
        gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=state.system_instr_solver)
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
    set_leds(True, True, True)
    time.sleep(0.2)
    set_leds(False, False, False)

def on_global_hotkey(event):
    if event.event_type != keyboard.KEY_DOWN: return
    
    # Mode switch
    if event.name == 'f9':
        toggle_mode()
        return

    # Settings Menu (Ctrl + i)
    if event.name == 'i' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
        threading.Thread(target=open_settings_menu, daemon=True).start()
        return

    # Trigger (Right Shift)
    is_rshift = (event.name in ('right shift', 'rightshift') or event.scan_code == 54)
    if is_rshift:
        if state.mode == "SOLVER":
            threading.Thread(target=handle_solver_action, daemon=True).start()
        else:
            if not state.typing_active:
                threading.Thread(target=handle_typer_action, daemon=True).start()

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
