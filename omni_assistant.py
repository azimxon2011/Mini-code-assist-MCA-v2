# -*- coding: utf-8 -*-
import sys
import os
import io
import time
import base64
import threading
import hashlib
import keyboard
import ctypes
import win32api
import win32con
import win32gui
import warnings
import queue
import re 
import json
import math
import requests
import socket
import subprocess
try:
    import winreg
except ImportError:
    winreg = None
try:
    import cv2
except ImportError:
    cv2 = None
from PIL import ImageGrab
import tkinter as tk
from tkinter import ttk, messagebox

# Suppress deprecation warnings from libraries
warnings.filterwarnings("ignore")

def gemini_generate_content(api_key, model_name, system_instruction, prompt, image_b64=None):
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        parts = []
        if prompt:
            parts.append({"text": prompt})
        if image_b64:
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": image_b64
                }
            })
        payload = {
            "contents": [{"parts": parts}]
        }
        if system_instruction:
            payload["system_instruction"] = {
                "parts": [{"text": system_instruction}]
            }
        headers = {"Content-Type": "application/json"}
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            text_parts = [p.get("text", "") for p in candidates[0]["content"].get("parts", [])]
            return "".join(text_parts)
    except Exception as e:
        print(f"[Gemini REST API Error] {e}")
        raise
    return ""


IS_WINDOWS = sys.platform == "win32"

# Unique identifier for this PC
def get_pc_identifier():
    try:
        name = socket.gethostname()
        return name.strip() if name else "PC_UNKNOWN"
    except Exception:
        return "PC_UNKNOWN"

PC_NAME = get_pc_identifier()

# Ensure single-instance running (prevents multiple launches or duplicate processes)
SINGLE_INSTANCE_MUTEX = None
if IS_WINDOWS:
    try:
        ERROR_ALREADY_EXISTS = 183
        mutex_name = f"Global\\OmniAssistant_{PC_NAME}"
        SINGLE_INSTANCE_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == ERROR_ALREADY_EXISTS:
            # Process is already running, exit immediately without duplicate
            sys.exit(0)
    except Exception:
        pass

# Hide Windows console window if started with python.exe instead of pythonw.exe
if IS_WINDOWS:
    try:
        hwnd_console = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd_console:
            ctypes.windll.user32.ShowWindow(hwnd_console, 0)  # SW_HIDE
    except Exception:
        pass

# DPI Awareness for accurate coordinate calculation and mouse positioning
if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# --- CONFIG ---
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

TG_BOT_TOKEN = "8606905686:AAGpMwPpjsJcJEhwH56LdEXvKGw1c4kbv18"
TG_CHAT_ID = "-5535080344"

DEFAULT_CONFIG = {
    "api_key_1": "AIzaSyAIBABOOlZS8aopLEgsPW9_9lWDivSTB_c",
    "api_key_2": "AIzaSyBp_mHdzSh7FutWrNIJ-dJv7hTAFIoIrro",
    "api_key_3": "AIzaSyBQtqv87aXYIQlJ55r_lwfJX5hfmvEckis",
    "active_key_index": 1,
    "model_flash": "gemini-3.1-flash-lite",
    "model_pro": "gemini-3-flash-preview",
    "model_solver": "gemini-3.1-flash-lite",
    "tg_bot_token": TG_BOT_TOKEN,
    "tg_chat_id": TG_CHAT_ID,
    "pc_name": PC_NAME,
    "target_logins": [],
    "log_watch_path": os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_activity.log")
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if "api_key" in cfg and "api_key_1" not in cfg:
                    cfg["api_key_1"] = cfg["api_key"]
                    cfg["api_key_2"] = cfg["api_key"]
                    cfg["api_key_3"] = cfg["api_key"]
                    cfg["active_key_index"] = 1
                if cfg.get("model_flash") == "gemini-3-flash-preview":
                    cfg["model_flash"] = "gemini-3.1-flash-lite"
                if cfg.get("model_pro") == "gemini-3.1-pro-preview":
                    cfg["model_pro"] = "gemini-3-flash-preview"
                if cfg.get("model_solver") == "gemini-3-flash-preview":
                    cfg["model_solver"] = "gemini-3.1-flash-lite"
                if "pc_name" not in cfg:
                    cfg["pc_name"] = PC_NAME
                if "target_logins" not in cfg:
                    cfg["target_logins"] = []
                if "log_watch_path" not in cfg:
                    cfg["log_watch_path"] = DEFAULT_CONFIG["log_watch_path"]
                return cfg
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass

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

# --- STATE ---
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # "SOLVER", "TYPER_PYTHON" or "TYPER_CPP"
        self.busy = False
        self.lock = threading.Lock()
        
        self.pc_name = config_data.get("pc_name", PC_NAME)
        self.target_logins = set(config_data.get("target_logins", []))
        self.log_watch_path = config_data.get("log_watch_path", DEFAULT_CONFIG["log_watch_path"])
        self.is_running = True
        self.frozen = False
        
        old_key = config_data.get("api_key", "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s")
        self.api_key_1 = config_data.get("api_key_1", old_key)
        self.api_key_2 = config_data.get("api_key_2", old_key)
        self.api_key_3 = config_data.get("api_key_3", old_key)
        self.active_key_index = config_data.get("active_key_index", 1)
        self.api_key = getattr(self, f"api_key_{self.active_key_index}", self.api_key_1)
        
        self.model_flash = config_data.get("model_flash", DEFAULT_CONFIG["model_flash"])
        self.model_pro = config_data.get("model_pro", DEFAULT_CONFIG["model_pro"])
        self.model_solver = config_data.get("model_solver", DEFAULT_CONFIG["model_solver"])
        self.tg_bot_token = config_data.get("tg_bot_token", DEFAULT_CONFIG["tg_bot_token"])
        self.tg_chat_id = str(config_data.get("tg_chat_id", DEFAULT_CONFIG["tg_chat_id"]))
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
        
        # Cursor caches
        self.h_normal_cache = None
        self.h_wait_cache = None
        self.h_block_cache = None
        
        # Global
        self.loading_event = threading.Event()
        self.menu_open = False
        self.cancel_event = threading.Event()
        self.rshift_timer = None
        self.request_id = 0
        self.error_cursor_active = False
        self.error_cursor_id = 0

state = AssistantState()

def switch_api_key(index):
    with state.lock:
        if index not in (1, 2, 3):
            return
        state.active_key_index = index
        state.api_key = getattr(state, f"api_key_{index}")
        
        cfg = load_config()
        cfg["active_key_index"] = index
        cfg["api_key_1"] = state.api_key_1
        cfg["api_key_2"] = state.api_key_2
        cfg["api_key_3"] = state.api_key_3
        save_config(cfg)

# --- TELEGRAM BOT UTILS ---
def tg_send_message(text, parse_mode="HTML"):
    def _worker():
        token = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode}, timeout=6)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def tg_send_photo(photo_bytes, caption="", parse_mode="HTML"):
    def _worker():
        token = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            files = {"photo": ("screenshot.png", io.BytesIO(photo_bytes), "image/png")}
            data = {"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode}
            requests.post(url, data=data, files=files, timeout=12)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

# --- CURSOR CACHING & CONTROLS ---
def init_cursors():
    if not IS_WINDOWS:
        return
    with state.lock:
        if state.h_normal_cache is None:
            try:
                ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 0)
            except Exception:
                pass
            
            try:
                OCR_NORMAL = 32512
                OCR_APPSTARTING = 32650
                OCR_NO = 32648
                state.h_normal_cache = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, OCR_NORMAL))
                state.h_wait_cache = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, OCR_APPSTARTING))
                state.h_block_cache = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, OCR_NO))
            except Exception:
                pass

def set_system_cursor(cursor_type):
    if not IS_WINDOWS:
        return
    init_cursors()
    try:
        h_copy = None
        if cursor_type == "NORMAL" and state.h_normal_cache:
            h_copy = ctypes.windll.user32.CopyIcon(state.h_normal_cache)
        elif cursor_type == "WAIT" and state.h_wait_cache:
            h_copy = ctypes.windll.user32.CopyIcon(state.h_wait_cache)
        elif cursor_type == "BLOCK" and state.h_block_cache:
            h_copy = ctypes.windll.user32.CopyIcon(state.h_block_cache)
        
        if h_copy:
            ctypes.windll.user32.SetSystemCursor(h_copy, 32512)
    except Exception:
        pass

def show_error_cursor():
    with state.lock:
        state.error_cursor_id += 1
        my_id = state.error_cursor_id
        state.error_cursor_active = True

    def worker():
        try:
            set_system_cursor("BLOCK")
            time.sleep(1.5)
            with state.lock:
                if state.error_cursor_id == my_id:
                    state.error_cursor_active = False
                    set_system_cursor("NORMAL")
        except Exception:
            pass
            
    threading.Thread(target=worker, daemon=True).start()

def loading_heartbeat():
    is_solver = (state.mode == "SOLVER")
    
    if is_solver:
        with state.lock:
            state.error_cursor_id += 1
            state.error_cursor_active = False
        set_system_cursor("WAIT")
        
    while state.loading_event.is_set():
        time.sleep(0.08)

    if is_solver:
        if not getattr(state, "error_cursor_active", False):
            set_system_cursor("NORMAL")

def get_screenshot(active_only=True):
    try:
        if active_only:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd:
                rect = win32gui.GetWindowRect(hwnd)
                if rect[2] > rect[0] and rect[3] > rect[1]:
                    img = ImageGrab.grab(bbox=rect, all_screens=True)
                    return img, rect
        
        img = ImageGrab.grab(all_screens=True)
        return img, (0, 0, img.width, img.height)
    except Exception:
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
    code = code.replace('\x08', '')
    lines = code.split('\n')
    result = []
    editor_indent = 0
    for line in lines:
        if not line.strip():
            result.append('')
            continue
        target_indent = len(line) - len(line.lstrip(' '))
        content = line.lstrip(' ')
        if target_indent < editor_indent:
            bs_count = editor_indent - target_indent
            result.append('\x08' * bs_count + content)
        else:
            extra = target_indent - editor_indent
            result.append(' ' * extra + content)
        if content.rstrip().endswith(':') or content.rstrip().endswith('{'):
            editor_indent = target_indent + 2
        else:
            editor_indent = target_indent
    return '\n'.join(result)

# --- SETTINGS MENU (TKINTER) ---
def open_settings_menu():
    if state.menu_open:
        return
    state.menu_open = True
    
    root = tk.Tk()
    root.title("Settings")
    root.geometry("420x680")
    root.attributes('-topmost', True)
    
    main_frame = ttk.Frame(root, padding=10)
    main_frame.pack(fill=tk.BOTH, expand=True)

    def add_entry(label, var_name, is_text=False):
        ttk.Label(main_frame, text=label).pack(anchor=tk.W)
        if is_text:
            widget = tk.Text(main_frame, height=3, width=40, font=("Consolas", 9))
            widget.insert("1.0", getattr(state, var_name))
            widget.pack(fill=tk.X, pady=(0, 5))
            return widget
        else:
            widget = ttk.Entry(main_frame, width=40)
            widget.insert(0, getattr(state, var_name))
            widget.pack(fill=tk.X, pady=(0, 5))
            return widget

    key1_e = add_entry("API Key 1 (Free tier)", "api_key_1")
    key2_e = add_entry("API Key 2 (Free tier)", "api_key_2")
    key3_e = add_entry("API Key 3 (Free tier)", "api_key_3")
    
    ttk.Label(main_frame, text="Active API Key").pack(anchor=tk.W)
    active_key_var = tk.IntVar(value=state.active_key_index)
    key_sel_frame = ttk.Frame(main_frame)
    key_sel_frame.pack(fill=tk.X, pady=(0, 5))
    for i in (1, 2, 3):
        ttk.Radiobutton(key_sel_frame, text=f"Key {i}", variable=active_key_var, value=i).pack(side=tk.LEFT, padx=5)

    flash_e = add_entry("Flash Model", "model_flash")
    pro_e = add_entry("Pro Model", "model_pro")
    solve_model_e = add_entry("Solver Model", "model_solver")
    typer_i = add_entry("Typer Instr", "system_instr_typer", True)
    typer_cpp_i = add_entry("Typer C++ Instr", "system_instr_typer_cpp", True)
    solver_i = add_entry("Solver Instr", "system_instr_solver", True)

    def on_apply():
        with state.lock:
            state.api_key_1 = key1_e.get().strip()
            state.api_key_2 = key2_e.get().strip()
            state.api_key_3 = key3_e.get().strip()
            state.active_key_index = active_key_var.get()
            state.api_key = getattr(state, f"api_key_{state.active_key_index}")
            
            state.model_flash = flash_e.get().strip()
            state.model_pro = pro_e.get().strip()
            state.model_solver = solve_model_e.get().strip()
            state.system_instr_typer = typer_i.get("1.0", tk.END).strip()
            state.system_instr_typer_cpp = typer_cpp_i.get("1.0", tk.END).strip()
            state.system_instr_solver = solver_i.get("1.0", tk.END).strip()
            
            save_config({
                "api_key_1": state.api_key_1,
                "api_key_2": state.api_key_2,
                "api_key_3": state.api_key_3,
                "active_key_index": state.active_key_index,
                "model_flash": state.model_flash,
                "model_pro": state.model_pro,
                "model_solver": state.model_solver
            })
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
    if getattr(state, "frozen", False):
        stop_typing_mode()
        return True
    is_down = event.event_type == keyboard.KEY_DOWN
    
    if event.name == 'f8':
        if is_down:
            state.edit_mode = not state.edit_mode
        return False
    if state.edit_mode:
        return True
    
    if event.name == 'f9':
        if is_down:
            stop_typing_mode()
            toggle_mode()
        return False

    if event.name == 'i' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
        if is_down:
            threading.Thread(target=open_settings_menu, daemon=True).start()
        return False

    if event.name == 'esc':
        if is_down:
            stop_typing_mode()
        return False

    if event.name == 'backspace':
        if is_down:
            with state.lock:
                if state.code_index > 0:
                    state.code_index -= 1
            typing_queue.put('\x08')
        return False
    
    if is_down:
        with state.lock:
            if not state.typing_active or state.code_index >= len(state.code_buffer):
                if state.code_index >= len(state.code_buffer) and state.typing_active:
                    stop_typing_mode()
                return False
            ch = state.code_buffer[state.code_index]
            state.code_index += 1
        typing_queue.put(ch)
    return False

def start_typing_mode():
    if state.typing_hook is None:
        state.typing_hook = keyboard.hook(on_typing_key, suppress=True)

def stop_typing_mode():
    state.typing_active = False
    if state.typing_hook:
        keyboard.unhook(state.typing_hook)
        state.typing_hook = None

# --- ACTION HANDLERS ---
def handle_typer_action(model_name=None):
    if getattr(state, "frozen", False):
        return
    with state.lock:
        if state.busy:
            return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id = state.request_id
        if model_name is None:
            is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')
            model_name = state.model_pro if is_ctrl else state.model_flash
        is_cpp = (state.mode == "TYPER_CPP")
        lang_str = "C++" if is_cpp else "Python"
        env_str = "GCC" if is_cpp else "PyPy"
        system_instr = state.system_instr_typer_cpp if is_cpp else state.system_instr_typer
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        img, _ = get_screenshot(active_only=False)
        if img:
            img_data = img_to_bytes(img)
            b64_img = base64.b64encode(img_data).decode("utf-8")
            res_text = gemini_generate_content(
                api_key=state.api_key,
                model_name=model_name,
                system_instruction=system_instr,
                prompt=f"Solve this Robocontest problem and return the {lang_str} code. (Environment: {env_str})",
                image_b64=b64_img
            )
            if state.cancel_event.is_set() or state.request_id != my_req_id:
                return
            code = adjust_for_autoindent(strip_markdown(res_text))
             
            with state.lock:
                state.code_buffer = code
                state.code_index = 0
                state.typing_active = True
            start_typing_mode()
            tg_send_message(f"💻 Typer ({lang_str}) Ready! Code generated ({len(code)} chars).")
    except Exception:
        show_error_cursor()
        tg_send_message("❌ Typer Error occurred.")
    finally:
        with state.lock:
            if state.request_id == my_req_id:
                state.loading_event.clear()
                state.busy = False

def handle_solver_action():
    if getattr(state, "frozen", False):
        return
    with state.lock:
        if state.busy:
            return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id = state.request_id
        model_name = state.model_solver
    
    try:
        img, rect = get_screenshot(active_only=True)
        if not img:
            return
        w, h = img.size
        
        img_bytes = img_to_bytes(img)
        curr_hash = hashlib.sha256(img_bytes).hexdigest()
        if curr_hash == state.last_img_hash:
            return

        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        b64_img = base64.b64encode(img_bytes).decode("utf-8")
        res_text = gemini_generate_content(
            api_key=state.api_key,
            model_name=model_name,
            system_instruction=state.system_instr_solver,
            prompt="Solve the question in this image. Provide the letter and coordinates.",
            image_b64=b64_img
        )
        if state.cancel_event.is_set() or state.request_id != my_req_id:
            return
        
        text = res_text.strip().upper()
        match = re.search(r"([A-D])\s*\[(\d+),\s*(\d+)\]", text)
        
        if match:
            ans = match.group(1)
            nx = int(match.group(2))
            ny = int(match.group(3))
            
            tx = rect[0] + int(nx * w / 1000)
            ty = rect[1] + int(ny * h / 1000)
            
            if state.cancel_event.is_set():
                return

            start_pull = time.time()
            click_done = False
            
            while time.time() - start_pull < 2.0:
                if state.cancel_event.is_set():
                    return
                
                cur_x, cur_y = win32api.GetCursorPos()
                
                factor = 0.15 
                mx = cur_x + int((tx - cur_x) * factor)
                my = cur_y + int((ty - cur_y) * factor)
                
                if abs(tx - cur_x) <= 2: mx = tx
                if abs(ty - cur_y) <= 2: my = ty
                
                win32api.SetCursorPos((mx, my))
                
                dist = math.hypot(tx - cur_x, ty - cur_y)
                if dist < 5 and not click_done:
                    win32api.SetCursorPos((tx, ty))
                    time.sleep(0.01)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
                    time.sleep(0.02)
                    win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
                    click_done = True
                
                time.sleep(0.01)
        else:
            ans = text[:1]
            if ans not in "ABCD":
                ans = "?"

        state.last_img_hash = curr_hash
        state.last_letter = ans
        tg_send_message(f"🎯 Solver Answer: {ans}")
        
    except Exception:
        show_error_cursor()
        tg_send_message("❌ Solver Error occurred.")
    finally:
        with state.lock:
            if state.request_id == my_req_id:
                state.loading_event.clear()
                state.busy = False

def toggle_mode():
    with state.lock:
        if state.mode == "SOLVER":
            state.mode = "TYPER_PYTHON"
        elif state.mode == "TYPER_PYTHON":
            state.mode = "TYPER_CPP"
        else:
            state.mode = "SOLVER"

def on_global_hotkey(event):
    if getattr(state, "frozen", False):
        return
    is_down = event.event_type == keyboard.KEY_DOWN

    is_rshift = (event.name in ('right shift', 'rightshift') or event.scan_code == 54)
    if is_rshift:
        if is_down:
            if state.rshift_timer is None:
                def trigger():
                    with state.lock:
                        if state.busy:
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

    if not is_down:
        return

    if (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')) and event.name in ('1', '2', '3'):
        key_num = int(event.name)
        switch_api_key(key_num)
        return

    if event.name == 'esc' and state.loading_event.is_set():
        state.cancel_event.set()
        state.loading_event.clear()
        with state.lock:
            state.busy = False
            state.request_id += 1
        return
    
    if event.name == 'f9':
        toggle_mode()
        return

    if event.name == 'i' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
        threading.Thread(target=open_settings_menu, daemon=True).start()
        return

# --- AUTOSTART MANAGER (4 METHODS) ---
def get_startup_cmd():
    # Returns the recommended command string to launch omni_assistant
    script_path = os.path.abspath(__file__)
    python_exe = sys.executable
    if python_exe.lower().endswith("python.exe"):
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
    return f'"{python_exe}" "{script_path}"'

def setup_autostart(method: int = 0):
    """
    Configures all 4 autostart mechanisms simultaneously:
    1. Registry HKCU\\...\\Run
    2. VBS Runner in Startup
    3. Task Scheduler (schtasks onlogon)
    4. BAT Runner in Startup
    Single-instance mutex prevents duplicate processes from launching simultaneously.
    """
    if not IS_WINDOWS:
        return False, "Поддерживается только в Windows"
    
    app_name = "OmniAssistant"
    cmd = get_startup_cmd()
    success_list = []
    
    # 1. Registry Run
    try:
        if winreg:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            success_list.append("1. Реестр (HKCU Run)")
    except Exception:
        pass

    # 2 & 4. Startup folder (VBS + BAT)
    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.exists(startup_dir):
            # 2. VBS (silent)
            vbs_file = os.path.join(startup_dir, "omni_assistant_start.vbs")
            vbs_content = f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run "{cmd}", 0, False\n'
            with open(vbs_file, "w", encoding="utf-8") as f:
                f.write(vbs_content)
            success_list.append("2. Скрытый VBS в Автозагрузке")

            # 4. BAT
            bat_file = os.path.join(startup_dir, "omni_assistant_runner.bat")
            bat_content = f'@echo off\nstart "" {cmd}\nexit\n'
            with open(bat_file, "w", encoding="utf-8") as f:
                f.write(bat_content)
            success_list.append("4. BAT-раннер в Автозагрузке")
    except Exception:
        pass

    # 3. Task Scheduler
    try:
        task_name = "OmniAssistantTask"
        subprocess.run(f'schtasks /delete /tn "{task_name}" /f', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.run(
            f'schtasks /create /tn "{task_name}" /tr "{cmd}" /sc onlogon /rl highest /f',
            shell=True, capture_output=True, text=True
        )
        if res.returncode == 0:
            success_list.append("3. Планировщик задач (onlogon)")
    except Exception:
        pass

    if success_list:
        summary = "Автозапуск настроен всеми методами (без дублей процессов благодаря Mutex):\n• " + "\n• ".join(success_list)
        return True, summary
    else:
        return False, "Не удалось настроить автозапуск ни одним из способов"

# --- WEBCAM CAPTURE ---
def capture_webcam():
    if cv2 is None:
        return None, "OpenCV (cv2) не установлен"
    cap = None
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if IS_WINDOWS else 0)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                return None, "Не удалось открыть веб-камеру"
        
        # Warm up camera sensor
        for _ in range(5):
            cap.read()
        ret, frame = cap.read()
        if not ret or frame is None:
            return None, "Не удалось захватить кадр с камеры"
        
        ret, buffer = cv2.imencode('.jpg', frame)
        if not ret:
            return None, "Ошибка кодирования изображения"
        return buffer.tobytes(), None
    except Exception as e:
        return None, f"Ошибка веб-камеры: {e}"
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass

# --- LOG WATCHER (AUTO-SOLVER TRIGGER) ---
def log_watcher_thread():
    last_pos = 0
    while state.is_running:
        if getattr(state, "frozen", False):
            time.sleep(1.0)
            continue
        try:
            log_path = state.log_watch_path
            if log_path and os.path.exists(log_path):
                file_size = os.path.getsize(log_path)
                if file_size < last_pos:
                    last_pos = 0  # File was rotated/truncated
                
                if file_size > last_pos:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        last_pos = f.tell()
                    
                    for line in new_lines:
                        # Check logins in new lines
                        with state.lock:
                            targets = list(state.target_logins)
                        for target in targets:
                            if target and target.lower() in line.lower():
                                tg_send_message(f"🚨 [ПК: {state.pc_name}] Обнаружен вход целевого логина: '{target}'!\n⚡ Автоматически запускаю SOLVER...")
                                # Trigger solver
                                threading.Thread(target=handle_solver_action, daemon=True).start()
                                break
        except Exception:
            pass
        time.sleep(1.0)

# --- TELEGRAM BOT POLLER & DISPATCHER ---
def tg_bot_poller():
    offset = None
    token = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
    target_chat_id = str(getattr(state, "tg_chat_id", TG_CHAT_ID))
    
    # Startup announcement
    tg_send_message(
        f"┌──────────────────────┐\n"
        f"│ 🟢 <b>OMNI ASSISTANT</b> — ONLINE │\n"
        f"│ 💻 PC: <code>{state.pc_name}</code>          │\n"
        f"└──────────────────────┘\n"
        f"🤖 Bot online & ready!\n"
        f"Mode: <code>{state.mode}</code>  ↓  /help — all commands"
    )
    
    while state.is_running:
        try:
            token = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {"timeout": 20}
            if offset:
                params["offset"] = offset
            
            resp = requests.get(url, params=params, timeout=25)
            if resp.status_code != 200:
                time.sleep(2)
                continue
            
            data = resp.json()
            if not data.get("ok"):
                time.sleep(2)
                continue
            
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue
                
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id"))
                
                target_chat_id = str(getattr(state, "tg_chat_id", TG_CHAT_ID))
                if target_chat_id and chat_id != target_chat_id:
                    continue
                
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                
                parts = text.split()
                cmd = parts[0].lower().split('@')[0]
                args = parts[1:]
                
                # All controls are shared/global for all PCs; ignore any accidental @tags
                filtered_args = [a for a in args if not a.startswith("@")]
                
                # General Commands
                if cmd in ("/start", "/help"):
                    help_txt = (
                        f"╔══════════════════════════╗\n"
                        f"║  🤖  <b>OMNI ASSISTANT</b>  ⚡  ║\n"
                        f"║  🌐  <b>GLOBAL CONTROL</b>    ║\n"
                        f"╚══════════════════════════╝\n"
                        f"\n"
                        f"<b>🖥 SYSTEM CONTROL</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 /status      — system status\n"
                        f"❄️ /freeze      — pause bot actions (idle mode)\n"
                        f"▶️ /unfreeze    — resume bot actions\n"
                        f"⚡ /autostart   — setup autostart (4 methods)\n"
                        f"🌐 /list_pc     — list online PCs\n"
                        f"\n"
                        f"<b>📸 MEDIA &amp; SCREEN</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🖼 /screenshot  — screen capture\n"
                        f"📷 /webcam      — webcam photo\n"
                        f"\n"
                        f"<b>👥 LOGIN WATCHER</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"➕ /login add <code>st0001, st0002</code>\n"
                        f"➖ /login del <code>st0001</code>\n"
                        f"📋 /login list\n"
                        f"🧹 /login clear\n"
                        f"\n"
                        f"<b>🔑 MODES &amp; KEYS</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 /solver      — Solver mode\n"
                        f"🐍 /python      — Python Typer mode\n"
                        f"⚙️ /cpp         — C++ Typer mode\n"
                        f"🔑 /key <code>1|2|3</code>   — switch active API key\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💡 All commands execute <b>globally</b> across all connected PCs"
                    )
                    tg_send_message(help_txt)
                    continue

                if cmd == "/list_pc":
                    mode_emoji = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                    status_icon = "❄️" if getattr(state, "frozen", False) else ("⏳" if state.busy else "✅")
                    state_str = "Frozen" if getattr(state, "frozen", False) else ("Busy" if state.busy else "Ready")
                    tg_send_message(
                        f"💻 <b>ONLINE PC:</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🖥 <code>{state.pc_name}</code>\n"
                        f"{mode_emoji} Mode: <b>{state.mode}</b>\n"
                        f"{status_icon} State: <b>{state_str}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    continue

                # Commands execution (Global for all PCs)
                if cmd == "/freeze":
                    with state.lock:
                        state.frozen = True
                        state.cancel_event.set()
                        state.loading_event.clear()
                        state.busy = False
                        state.request_id += 1
                    stop_typing_mode()
                    set_system_cursor("NORMAL")
                    tg_send_message(f"❄️ [PC: {state.pc_name}] Bot is FROZEN (Idle mode). All actions paused until /unfreeze.")

                elif cmd == "/unfreeze":
                    with state.lock:
                        state.frozen = False
                    tg_send_message(f"▶️ [PC: {state.pc_name}] Bot is UNFROZEN. Normal operation resumed.")

                elif cmd == "/autostart":
                    ok, info = setup_autostart(0)
                    tg_send_message(f"{'✅' if ok else '❌'} [PC: {state.pc_name}] {info}")

                elif cmd == "/screenshot":
                    img, _ = get_screenshot(active_only=False)
                    if img:
                        b = img_to_bytes(img)
                        tg_send_photo(b, caption=f"📸 Screenshot — PC: {state.pc_name} (Mode: {state.mode})")
                    else:
                        tg_send_message(f"⚠️ [PC: {state.pc_name}] Failed to capture screenshot")

                elif cmd == "/webcam":
                    photo_bytes, err = capture_webcam()
                    if photo_bytes:
                        tg_send_photo(photo_bytes, caption=f"📷 Webcam — PC: {state.pc_name}")
                    else:
                        tg_send_message(f"⚠️ [PC: {state.pc_name}] Webcam: {err}")

                elif cmd in ("/login", "/logins"):
                    # Check subactions: /login add l1, l2 or /login del l1 or /login list or /login clear
                    subaction = filtered_args[0].lower() if filtered_args else "list"
                    
                    if subaction in ("add", "+"):
                        raw_logins_str = " ".join(filtered_args[1:])
                        
                        if not raw_logins_str.strip():
                            tg_send_message("⚠️ Usage:\n`/login add st0001, st0002, st0003`")
                            continue
                        
                        # Split by comma or semicolon
                        items = [x.strip() for x in re.split(r"[,;]+", raw_logins_str) if x.strip()]
                        if not items:
                            tg_send_message("⚠️ No logins found to add.")
                            continue
                        
                        with state.lock:
                            for item in items:
                                state.target_logins.add(item)
                            cfg = load_config()
                            cfg["target_logins"] = list(state.target_logins)
                            save_config(cfg)
                        
                        added_str = ", ".join([f"`{x}`" for x in items])
                        tg_send_message(f"✅ [PC: {state.pc_name}] Added logins ({len(items)}): {added_str}\nSolver will trigger automatically when detected!")

                    elif subaction in ("del", "remove", "rm", "-"):
                        raw_logins_str = " ".join(filtered_args[1:])
                        if not raw_logins_str.strip():
                            tg_send_message("⚠️ Usage: `/login del st0001, st0002`")
                            continue
                        items = [x.strip() for x in re.split(r"[,;]+", raw_logins_str) if x.strip()]
                        removed = []
                        with state.lock:
                            for item in items:
                                if item in state.target_logins:
                                    state.target_logins.remove(item)
                                    removed.append(item)
                            if removed:
                                cfg = load_config()
                                cfg["target_logins"] = list(state.target_logins)
                                save_config(cfg)
                        if removed:
                            rem_str = ", ".join([f"`{x}`" for x in removed])
                            tg_send_message(f"🗑️ [PC: {state.pc_name}] Removed ({len(removed)}): {rem_str}")
                        else:
                            tg_send_message(f"ℹ️ [PC: {state.pc_name}] Specified logins not found in list.")

                    elif subaction in ("clear", "reset"):
                        with state.lock:
                            state.target_logins.clear()
                            cfg = load_config()
                            cfg["target_logins"] = []
                            save_config(cfg)
                        tg_send_message(f"🧹 [PC: {state.pc_name}] Login list cleared.")

                    else:
                        # Default is list view
                        with state.lock:
                            logins = list(state.target_logins)
                        if logins:
                            txt = f"📋 [PC: {state.pc_name}] Watched logins ({len(logins)}):\n" + "\n".join([f"• `{l}`" for l in logins])
                        else:
                            txt = f"📋 [PC: {state.pc_name}] Login list is empty.\nAdd: `/login add st0001, st0002`"
                        txt += f"\n\nLog file: `{state.log_watch_path}`"
                        tg_send_message(txt)

                elif cmd == "/del_login":
                    if not filtered_args:
                        tg_send_message("⚠️ Usage: `/del_login <login>`")
                        continue
                    items = [x.strip() for x in re.split(r"[,;]+", " ".join(filtered_args)) if x.strip()]
                    removed = []
                    with state.lock:
                        for item in items:
                            if item in state.target_logins:
                                state.target_logins.remove(item)
                                removed.append(item)
                        if removed:
                            cfg = load_config()
                            cfg["target_logins"] = list(state.target_logins)
                            save_config(cfg)
                    if removed:
                        tg_send_message(f"🗑️ [PC: {state.pc_name}] Removed: {', '.join([f'`{x}`' for x in removed])}")
                    else:
                        tg_send_message(f"ℹ️ [PC: {state.pc_name}] Logins not found.")

                elif cmd == "/clear_logins":
                    with state.lock:
                        state.target_logins.clear()
                        cfg = load_config()
                        cfg["target_logins"] = []
                        save_config(cfg)
                    tg_send_message(f"🧹 [PC: {state.pc_name}] Watched logins list cleared.")

                elif cmd == "/set_logfile":
                    if not filtered_args:
                        tg_send_message("⚠️ Usage: `/set_logfile C:\\path\\to\\log.txt`")
                        continue
                    new_path = " ".join(filtered_args).strip('"')
                    with state.lock:
                        state.log_watch_path = new_path
                        cfg = load_config()
                        cfg["log_watch_path"] = new_path
                        save_config(cfg)
                    tg_send_message(f"✅ [PC: {state.pc_name}] Log path updated: `{new_path}`")

                elif cmd == "/status":
                    active_key = state.api_key
                    masked_key = f"{active_key[:6]}...{active_key[-4:]}" if len(active_key) > 10 else active_key
                    mode_emoji = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                    state_icon = "❄️ FROZEN" if getattr(state, "frozen", False) else ("⏳ Busy" if state.busy else "✅ Ready")
                    status_txt = (
                        f"┌──────────────────────┐\n"
                        f"│  📊 <b>SYSTEM STATUS</b>       │\n"
                        f"└──────────────────────┘\n"
                        f"\n"
                        f"🖥 PC:          <code>{state.pc_name}</code>\n"
                        f"⚡ State:       <b>{state_icon}</b>\n"
                        f"{mode_emoji} Mode:        <b>{state.mode}</b>\n"
                        f"🔑 Key:         <code>#{state.active_key_index}</code> | {masked_key}\n"
                        f"🤖 Model:       <code>{state.model_solver}</code>\n"
                        f"🎯 Last Ans:    <b>{state.last_letter}</b>\n"
                        f"👥 Logins:      <b>{len(state.target_logins)}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )
                    tg_send_message(status_txt)

                elif cmd == "/solver":
                    with state.lock:
                        state.mode = "SOLVER"
                    tg_send_message(f"✅ [PC: {state.pc_name}] Mode switched to: SOLVER")

                elif cmd in ("/typer_py", "/python"):
                    with state.lock:
                        state.mode = "TYPER_PYTHON"
                    tg_send_message(f"✅ [PC: {state.pc_name}] Mode switched to: TYPER_PYTHON")

                elif cmd in ("/typer_cpp", "/cpp"):
                    with state.lock:
                        state.mode = "TYPER_CPP"
                    tg_send_message(f"✅ [PC: {state.pc_name}] Mode switched to: TYPER_CPP")

                elif cmd == "/key":
                    if filtered_args and filtered_args[0] in ("1", "2", "3"):
                        idx = int(filtered_args[0])
                        switch_api_key(idx)
                        tg_send_message(f"🔑 [PC: {state.pc_name}] API key switched to #{idx}")
                    else:
                        tg_send_message("⚠️ Usage: `/key 1`, `/key 2` or `/key 3`")

        except Exception:
            time.sleep(3)

def main():
    keyboard.hook(on_global_hotkey)
    # Start Telegram bot controller thread
    threading.Thread(target=tg_bot_poller, daemon=True).start()
    # Start Log watcher for auto-solver activation
    threading.Thread(target=log_watcher_thread, daemon=True).start()
    try:
        keyboard.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
