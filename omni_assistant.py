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
import math
import json
import os

# --- CONFIG ---
CONFIG_FILE = "config.json"

DEFAULT_CONFIG = {
    "api_key": "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s",
    "model_flash": "gemini-3-flash-preview",
    "model_pro": "gemini-3.1-pro-preview",
    "model_solver": "gemini-3-flash-preview",
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except:
            return DEFAULT_CONFIG
    return DEFAULT_CONFIG

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=4)

config_data = load_config()

# --- SYSTEM PROMPTS ---
TYPER_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write READABLE Python code.\n"
    "2. Do not overengineer(solve like a student in programming olympiad).\n"
    "3. Use 2-SPACE INDENTATION strictly. Every nested block must be indented exactly 2 spaces more than its parent.\n"
    "4. `else`, `elif` must align with their matching `if`.\n"
    "5. No OOP (no classes). No imports. No `def`s.\n"
    "6. Short variable names (1-5 characters) related to the problem.\n"
    "7. Return ONLY the raw code. No markdown, no explanations, no comments."
)

SOLVER_INSTRUCTION = (
    "You are an Elite Solver. Analyze the image and solve the question.\n"
    "1. Reason step-by-step internally to find the correct option (A, B, C, or D).\n"
    "2. Locate the EXACT center coordinates [X, Y] of the letter or its bubble.\n"
    "3. Output ONLY the result in this format: LETTER [X, Y]\n"
    "Example: C [500, 750]\n"
    "IMPORTANT: X and Y must be normalized (0-1000) relative to the image provided. 0,0 is top-left.\n"
    "If an answer is already marked, verify its correctness. Output ONLY the answer string."
)

TYPER_CPP_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write VERY CLEAR, UNDERSTANDABLE C++ code.\n"
    "2. Code should look like it was written by a normal human student, not a system engineer. Avoid overly complex syntax, macros, or obscure STL features unless necessary. Keep it simple and readable.\n"
    "3. Use 2-SPACE INDENTATION strictly.\n"
    "4. Use `#include <bits/stdc++.h>`.\n"
    "5. Short but meaningful variable names related to the problem.\n"
    "6. Return ONLY the raw code. No markdown, no explanations, no comments(also never leave comments in code)."
)

# indicator states for Solver: (NumLock, CapsLock, ScrollLock)
INDICATOR_MAP = {
    "A": (True,  False, False),
    "B": (False, True,  False),
    "C": (False, False, True),
    "D": (True,  True,  True),
    "?": (False, False, False),
}

MODE_INDICATOR_MAP = {
    "SOLVER": (False, True, False),
    "TYPER_PYTHON": (True, False, False),
    "TYPER_CPP": (False, False, True),
}

# --- STATE ---
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # "SOLVER", "TYPER_PYTHON" or "TYPER_CPP"
        self.busy = False
        self.lock = threading.Lock()
        
        # Configurable Settings
        self.api_key = config_data.get("api_key", DEFAULT_CONFIG["api_key"])
        self.model_flash = config_data.get("model_flash", DEFAULT_CONFIG["model_flash"])
        self.model_pro = config_data.get("model_pro", DEFAULT_CONFIG["model_pro"])
        self.model_solver = config_data.get("model_solver", DEFAULT_CONFIG["model_solver"])
        self.system_instr_typer = TYPER_INSTRUCTION
        self.system_instr_typer_cpp = TYPER_CPP_INSTRUCTION
        self.system_instr_solver = SOLVER_INSTRUCTION
        
        # Typer Specific
        self.code_buffer = ""
        self.code_index = 0
        self.typing_active = False
        self.edit_mode = False
        self.typing_hook = None
        
        # Solver Specific
        self.last_img_hash = None
        self.last_letter = "?"
        self.last_solver_indicator_until = 0.0
        
        # Global
        self.loading_event = threading.Event()
        self.menu_open = False
        self.cancel_event = threading.Event()
        self.rshift_timer = None
        self.request_id = 0

state = AssistantState()

# Initialize GenAI with default key
genai.configure(api_key=state.api_key)

# DPI Awareness for accurate mouse positioning
# DPI Awareness
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

import win32gui

# --- VIRTUAL KEYS ---
VK_NUM = 0x90
VK_CAPS = 0x14
VK_SCROLL = 0x91
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

def get_indicator_state():
    now = time.time()
    if state.typing_active:
        return (True, False, True)
    if state.mode == "SOLVER" and now < state.last_solver_indicator_until:
        return INDICATOR_MAP.get(state.last_letter, INDICATOR_MAP["?"])
    return MODE_INDICATOR_MAP.get(state.mode, (False, False, False))

def refresh_indicators():
    if state.loading_event.is_set():
        return
    set_leds(*get_indicator_state())

def loading_heartbeat():
    """Blinks indicators or animates mouse while loading_event is set."""
    is_solver = (state.mode == "SOLVER")
    
    if is_solver:
        try:
            OCR_NORMAL = 32512
            OCR_APPSTARTING = 32650
            hWaitCursor = ctypes.windll.user32.LoadCursorW(0, OCR_APPSTARTING)
            hCursorCopy = ctypes.windll.user32.CopyIcon(hWaitCursor)
            ctypes.windll.user32.SetSystemCursor(hCursorCopy, OCR_NORMAL)
        except Exception as e:
            log(f"Cursor error: {e}", "WARN")
        
    while state.loading_event.is_set():
        if is_solver:
            time.sleep(0.1)
        else:
            # Быстрое мигание для Typer
            set_leds(True, False, True)
            time.sleep(0.5)
            set_leds(False, False, False)
            for _ in range(50):
                if not state.loading_event.is_set():
                    break
                time.sleep(0.1)

    if is_solver:
        try:
            SPI_SETCURSORS = 0x0057
            ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)
        except Exception:
            pass

    refresh_indicators()

def get_screenshot(active_only=True):
    try:
        if active_only:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                rect = win32gui.GetWindowRect(hwnd)
                # Ensure rect is valid
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    img = ImageGrab.grab(bbox=rect, all_screens=True)
                    return img, rect
        
        img = ImageGrab.grab(all_screens=True)
        return img, (0, 0, img.width, img.height)
    except Exception as e:
        log(f"Screenshot error: {e}", "WARN")
        return None, None

def img_to_bytes(img):
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def strip_markdown(code):
    code = code.replace('\r', '').strip()
    match = re.search(r"```(?:python|cpp|c\+\+|c)?\n?(.*?)\n?```", code, re.DOTALL | re.IGNORECASE)
    if match:
        code = match.group(1).strip()
    return code.strip()

def adjust_for_autoindent(code):
    """Convert code for Robocontest editor.
    
    Robocontest: 2-space indentation, 1 backspace = 1 space.
    After a line ending with ':', the editor auto-indents by +2 spaces.
    So we track editor_indent and emit backspaces for the difference.
    """
    code = code.replace('\x08', '')  # strip any hallucinated backspaces from model
    lines = code.split('\n')
    result = []
    editor_indent = 0  # where the editor cursor will be at start of next line
    for line in lines:
        if not line.strip():
            result.append('')
            continue
        target_indent = len(line) - len(line.lstrip(' '))
        content = line.lstrip(' ')
        if target_indent < editor_indent:
            # Editor auto-indented too far; press backspace for each extra space
            bs_count = editor_indent - target_indent
            result.append('\x08' * bs_count + content)
        else:
            # Editor is behind or equal; add the extra spaces manually
            extra = target_indent - editor_indent
            result.append(' ' * extra + content)
        # After typing this line, predict where the editor will put the cursor next
        if content.rstrip().endswith(':') or content.rstrip().endswith('{'):
            editor_indent = target_indent + 2  # Robocontest auto-indents +2 after colon or brace
        else:
            editor_indent = target_indent
    return '\n'.join(result)

# --- SETTINGS MENU (TKINTER) ---
def open_settings_menu():
    if state.menu_open: return
    state.menu_open = True
    
    root = tk.Tk()
    root.title("Settings")
    root.geometry("400x550")
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
    typer_cpp_i = add_entry("Typer C++ Instr", "system_instr_typer_cpp", True)
    solver_i = add_entry("Solver Instr", "system_instr_solver", True)

    def on_apply():
        with state.lock:
            state.api_key = key_e.get().strip()
            state.model_flash = flash_e.get().strip()
            state.model_pro = pro_e.get().strip()
            state.model_solver = solve_model_e.get().strip()
            state.system_instr_typer = typer_i.get("1.0", tk.END).strip()
            state.system_instr_typer_cpp = typer_cpp_i.get("1.0", tk.END).strip()
            state.system_instr_solver = solver_i.get("1.0", tk.END).strip()
            
            save_config({
                "api_key": state.api_key,
                "model_flash": state.model_flash,
                "model_pro": state.model_pro,
                "model_solver": state.model_solver
            })
            
            genai.configure(api_key=state.api_key)
        log("Applied and saved to config.json")
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
    refresh_indicators()

def stop_typing_mode(reason=""):
    state.typing_active = False
    if state.typing_hook:
        keyboard.unhook(state.typing_hook)
        state.typing_hook = None
    if reason: log(reason)
    refresh_indicators()

# --- ACTION HANDLERS ---
def handle_typer_action(model_name=None):
    with state.lock:
        if state.busy: return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id = state.request_id
        if model_name is None:
            # Determine based on Ctrl mod used at trigger time
            is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')
            model_name = state.model_pro if is_ctrl else state.model_flash
        is_cpp = (state.mode == "TYPER_CPP")
        lang_str = "C++" if is_cpp else "Python"
        env_str = "GCC" if is_cpp else "PyPy"
        system_instr = state.system_instr_typer_cpp if is_cpp else state.system_instr_typer
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        img, _ = get_screenshot(active_only=False) # Typer usually needs full screen or at least browser
        if img:
            img_data = img_to_bytes(img)
            log(f"Typing ({lang_str}): Capture taken. Asking Gemini ({model_name})...")
            gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instr)
            response = gen_model.generate_content([
                f"Solve this Robocontest problem and return the {lang_str} code. (Environment: {env_str})", 
                {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_data).decode()}}
            ])
            if state.cancel_event.is_set() or state.request_id != my_req_id:
                log("Typing request cancelled. Ignoring late Gemini response.")
                return
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
        with state.lock:
            if state.request_id == my_req_id:
                state.loading_event.clear()
                state.busy = False
                refresh_indicators()

def handle_solver_action():
    with state.lock:
        if state.busy: return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id = state.request_id
        model_name = state.model_solver
    
    try:
        # For Solver, we prioritize the ACTIVE window to avoid confusing the AI
        img, rect = get_screenshot(active_only=True)
        if not img: return
        w, h = img.size
        
        img_bytes = img_to_bytes(img)
        curr_hash = hashlib.sha256(img_bytes).hexdigest()
        if curr_hash == state.last_img_hash:
            ans = state.last_letter
            log(f"Solver: Same image. Answer: {ans}")
            state.last_solver_indicator_until = time.time() + 8
            refresh_indicators()
            return

        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        log(f"Solver: Analyzing active window... ({model_name})")
        gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=state.system_instr_solver)
        response = gen_model.generate_content([
            "Solve the question in this image. Provide the letter and coordinates.",
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_bytes).decode()}}
        ])
        if state.cancel_event.is_set() or state.request_id != my_req_id:
            log("Solver request cancelled.")
            return
        
        text = response.text.strip().upper()
        # Look for B [450, 600] or similar
        match = re.search(r"([A-D])\s*\[(\d+),\s*(\d+)\]", text)
        
        if match:
            ans = match.group(1)
            nx = int(match.group(2))
            ny = int(match.group(3))
            
            # Scale normalized (0-1000) to actual pixels of the CROP
            tx = rect[0] + int(nx * w / 1000)
            ty = rect[1] + int(ny * h / 1000)
            
            log(f"Solver: Answer -> {ans} at ({tx}, {ty}).")
            
            if state.cancel_event.is_set(): return

            # Smoothly move to target
            cur_x, cur_y = win32api.GetCursorPos()
            steps = 6
            for i in range(steps):
                if state.cancel_event.is_set(): return
                mx = cur_x + (tx - cur_x) * (i + 1) // steps
                my = cur_y + (ty - cur_y) * (i + 1) // steps
                win32api.SetCursorPos((mx, my))
                time.sleep(0.01)
            
            if state.cancel_event.is_set(): return
            win32api.SetCursorPos((tx, ty))
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.05)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            log("Solver: Clicked.")
        else:
            ans = text[:1]
            if ans not in "ABCD": ans = "?"
            log(f"Solver: Answer discovered -> {ans} (No coordinates found)")

        state.last_img_hash = curr_hash
        state.last_letter = ans
        state.last_solver_indicator_until = time.time() + 8
        refresh_indicators()
        
    except Exception as e:
        log(f"Solver Error: {e}", "ERROR")
    finally:
        with state.lock:
            if state.request_id == my_req_id:
                state.loading_event.clear()
                state.busy = False
                refresh_indicators()

def toggle_mode():
    with state.lock:
        if state.mode == "SOLVER":
            state.mode = "TYPER_PYTHON"
        elif state.mode == "TYPER_PYTHON":
            state.mode = "TYPER_CPP"
        else:
            state.mode = "SOLVER"
        log(f"MODE SWITCHED: Now in {state.mode} mode.")
    set_leds(True, True, True)
    time.sleep(0.2)
    refresh_indicators()

def on_global_hotkey(event):
    is_down = event.event_type == keyboard.KEY_DOWN

    # Trigger (Right Shift) with 0.5-second hold
    is_rshift = (event.name in ('right shift', 'rightshift') or event.scan_code == 54)
    if is_rshift:
        if is_down:
            if state.rshift_timer is None:
                def trigger():
                    with state.lock:
                        if state.busy:
                            log("Restarting request (interrupted by user)...")
                            state.cancel_event.set()
                            state.busy = False
                            state.request_id += 1

                    if state.mode == "SOLVER":
                        threading.Thread(target=handle_solver_action, daemon=True).start()
                    else:
                        if not state.typing_active:
                            threading.Thread(target=handle_typer_action, daemon=True).start()
                state.rshift_timer = threading.Timer(0.5, trigger)
                state.rshift_timer.start()
        else:
            if state.rshift_timer is not None:
                state.rshift_timer.cancel()
                state.rshift_timer = None
        return

    if not is_down: return

    if event.name == 'esc' and state.loading_event.is_set():
        state.cancel_event.set()
        state.loading_event.clear()
        with state.lock:
            state.busy = False
            state.request_id += 1
        log("!!! CANCELLED !!! Ready.")
        refresh_indicators()
        return
    
    # Mode switch
    if event.name == 'f9':
        toggle_mode()
        return

    # Settings Menu (Ctrl + i)
    if event.name == 'i' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
        threading.Thread(target=open_settings_menu, daemon=True).start()
        return

def main():
    log("=== OMNI ASSISTANT MONOLITH ===")
    log("F9            : Switch Mode (Solver -> Typer_Python -> Typer_CPP)")
    log("RightShift    : Capture & Execute Action (Hold for 0.5 seconds)")
    log("Default Mode  : SOLVER")
    log("Indicators    : Loading=Num+Scroll blink | Idle Solver=Caps | Idle Py=Num | Idle C++=Scroll")
    log("Indicators    : Typing ready=Num+Scroll | Solver answer shown for 8s")
    log("-------------------------------")
    refresh_indicators()
    
    keyboard.hook(on_global_hotkey)
    
    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log("Exiting...")

if __name__ == "__main__":
    main()
