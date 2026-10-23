# -*- coding: utf-8 -*-
import sys
import traceback
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
from PIL import ImageGrab, Image
import tkinter as tk
from tkinter import ttk, messagebox

warnings.filterwarnings("ignore")

IS_WINDOWS = sys.platform == "win32"

# ─── LOGGER ───────────────────────────────────────────────────────────────────
_LOG_LOCK = threading.Lock()
def log(msg, level="INFO"):
    try:
        ts = time.strftime("%H:%M:%S")
        with _LOG_LOCK:
            print(f"[{ts}][{level}] {msg}", flush=True)
    except Exception:
        pass


# ─── GEMINI CALLER (SDK + REST fallback, multi-key rotation) ──────────────────
def gemini_generate_content(api_key, model_name, system_instruction, prompt, image_bytes=None, image_b64=None):
    if image_bytes is None and image_b64 is not None:
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            pass

    keys_to_try = []
    st = globals().get("state")
    if api_key and api_key not in keys_to_try:
        keys_to_try.append(api_key)
    if st:
        try:
            with st.lock:
                for k in [st.api_key_1, st.api_key_2, st.api_key_3]:
                    if k and k not in keys_to_try:
                        keys_to_try.append(k)
        except Exception:
            pass

    models_to_try = [model_name, "gemini-3.5-flash-lite", "gemini-3.5-flash",
                     "gemini-3-flash-preview", "gemini-3.8-flash", "gemini-2.5-flash", "gemini-flash-latest"]
    cleaned = []
    seen = set()
    for m in models_to_try:
        if m and m not in seen:
            seen.add(m)
            cleaned.append(m)

    last_err = None
    for key in keys_to_try:
        if not key:
            continue
        for m_name in cleaned:
            # Method 1: google.genai SDK
            try:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=key)
                contents = []
                if prompt:
                    contents.append(prompt)
                if image_bytes:
                    pil_img = Image.open(io.BytesIO(image_bytes))
                    contents.append(pil_img)
                cfg = types.GenerateContentConfig(system_instruction=system_instruction) if system_instruction else None
                resp = client.models.generate_content(model=m_name, contents=contents, config=cfg)
                if resp and resp.text:
                    return resp.text
            except Exception as sdk_e:
                e_msg = str(sdk_e).lower()
                if "404" in e_msg or "not_found" in e_msg:
                    continue
                if "403" in e_msg or "429" in e_msg or "quota" in e_msg or "leaked" in e_msg:
                    last_err = sdk_e
                    break

            # Method 2: REST API
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={key}"
                parts = []
                if prompt:
                    parts.append({"text": prompt})
                if image_bytes:
                    b64_val = base64.b64encode(image_bytes).decode("utf-8")
                    parts.append({"inline_data": {"mime_type": "image/png", "data": b64_val}})
                payload = {"contents": [{"parts": parts}]}
                if system_instruction:
                    payload["system_instruction"] = {"parts": [{"text": system_instruction}]}
                resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=25)
                if resp.status_code in (404, 400):
                    continue
                if resp.status_code in (429, 403):
                    last_err = Exception(f"HTTP {resp.status_code}")
                    break
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    text_parts = [p.get("text", "") for p in candidates[0]["content"].get("parts", [])]
                    return "".join(text_parts)
            except Exception as rest_e:
                last_err = rest_e
                continue

    if last_err:
        raise last_err
    return ""


# ─── PC IDENTITY & HELPERS ────────────────────────────────────────────────────
def get_pc_identifier():
    try:
        return socket.gethostname().strip() or "PC_UNKNOWN"
    except Exception:
        return "PC_UNKNOWN"

PC_NAME = get_pc_identifier()

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

BASE_DIR = get_base_dir()

# ─── SINGLE INSTANCE MUTEX ────────────────────────────────────────────────────
SINGLE_INSTANCE_MUTEX = None

def acquire_single_instance_mutex():
    global SINGLE_INSTANCE_MUTEX
    if IS_WINDOWS:
        try:
            mutex_name = f"Global\\OmniAssistant_{PC_NAME}"
            SINGLE_INSTANCE_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
            if ctypes.windll.kernel32.GetLastError() == 183:
                sys.exit(0)
        except Exception:
            pass

def hide_console_window():
    if IS_WINDOWS:
        try:
            if any(a in sys.argv for a in ("--debug", "-d", "--show", "-v", "--verbose")):
                return
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass

if IS_WINDOWS:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ─── CONFIG ───────────────────────────────────────────────────────────────────
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

TG_BOT_TOKEN = "8606905686:AAGpMwPpjsJcJEhwH56LdEXvKGw1c4kbv18"
TG_CHAT_ID   = "-5535080344"

_NK = base64.b64decode("QVEuQWI4Uk42SXRlaWNqdWJJcjMwdk9CWHBLWkhleGpqWlFjQ1c1YkdoeGZkcXh6WjhYSWc=").decode("utf-8")

DEFAULT_CONFIG = {
    "api_key_1": _NK,
    "api_key_2": _NK,
    "api_key_3": _NK,
    "active_key_index": 1,
    "model_flash":  "gemini-3.5-flash-lite",
    "model_pro":    "gemini-3-flash-preview",
    "model_solver": "gemini-3.5-flash-lite",
    "tg_bot_token": TG_BOT_TOKEN,
    "tg_chat_id":   TG_CHAT_ID,
    "pc_name":      PC_NAME,
    "target_logins": [],
    "log_watch_path": os.path.join(BASE_DIR, "app_activity.log"),
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # migrate old single key
            if "api_key" in cfg and "api_key_1" not in cfg:
                cfg["api_key_1"] = cfg["api_key"]
                cfg["api_key_2"] = cfg["api_key"]
                cfg["api_key_3"] = cfg["api_key"]
                cfg["active_key_index"] = 1
            for k in ("api_key_1", "api_key_2", "api_key_3"):
                if k not in cfg:
                    cfg[k] = _NK
            if "pc_name" not in cfg:
                cfg["pc_name"] = PC_NAME
            if "target_logins" not in cfg:
                cfg["target_logins"] = []
            if "log_watch_path" not in cfg:
                cfg["log_watch_path"] = DEFAULT_CONFIG["log_watch_path"]
            return cfg
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception:
        pass

config_data = load_config()


# ─── SYSTEM PROMPTS ───────────────────────────────────────────────────────────
TYPER_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write READABLE Python code.\n"
    "2. Do not overengineer (solve like a student in programming olympiad).\n"
    "3. Use 2-SPACE INDENTATION strictly.\n"
    "4. `else`, `elif` must align with their matching `if`.\n"
    "5. No OOP (no classes). No imports. No `def`s.\n"
    "6. Short variable names (1-5 characters) related to the problem.\n"
    "7. Return ONLY the raw code. No markdown, no explanations, no comments."
)

TYPER_CPP_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write VERY CLEAR, UNDERSTANDABLE C++ code.\n"
    "2. Code should look like it was written by a normal human student.\n"
    "3. Use 2-SPACE INDENTATION strictly.\n"
    "4. Use `#include <bits/stdc++.h>`.\n"
    "5. Short but meaningful variable names.\n"
    "6. Return ONLY the raw code. No markdown, no explanations, no comments."
)

SOLVER_INSTRUCTION = (
    "You are an Elite Solver. Analyze the image and solve the question.\n"
    "1. Reason step-by-step internally to find the correct option (A, B, C, or D).\n"
    "2. Locate the EXACT center coordinates [X, Y] of the letter or its bubble.\n"
    "3. Output ONLY the result in this format: LETTER [X, Y]\n"
    "Example: C [500, 750]\n"
    "IMPORTANT: X and Y must be normalized (0-1000) relative to the image. 0,0 is top-left.\n"
    "If an answer is already marked, verify its correctness. Output ONLY the answer string."
)


# ─── STATE ────────────────────────────────────────────────────────────────────
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # SOLVER | TYPER_PYTHON | TYPER_CPP
        self.busy = False
        self.lock = threading.Lock()

        self.pc_name        = config_data.get("pc_name", PC_NAME)
        self.target_logins  = set(config_data.get("target_logins", []))
        self.log_watch_path = config_data.get("log_watch_path", DEFAULT_CONFIG["log_watch_path"])
        self.is_running     = True
        self.frozen         = False

        self.api_key_1       = config_data.get("api_key_1", _NK)
        self.api_key_2       = config_data.get("api_key_2", _NK)
        self.api_key_3       = config_data.get("api_key_3", _NK)
        self.active_key_index = config_data.get("active_key_index", 1)
        self.api_key         = getattr(self, f"api_key_{self.active_key_index}", self.api_key_1)

        self.model_flash  = config_data.get("model_flash",  DEFAULT_CONFIG["model_flash"])
        self.model_pro    = config_data.get("model_pro",    DEFAULT_CONFIG["model_pro"])
        self.model_solver = config_data.get("model_solver", DEFAULT_CONFIG["model_solver"])
        self.tg_bot_token = config_data.get("tg_bot_token", TG_BOT_TOKEN)
        self.tg_chat_id   = str(config_data.get("tg_chat_id", TG_CHAT_ID))

        self.system_instr_typer     = TYPER_INSTRUCTION
        self.system_instr_typer_cpp = TYPER_CPP_INSTRUCTION
        self.system_instr_solver    = SOLVER_INSTRUCTION

        # Typer
        self.code_buffer    = ""
        self.code_index     = 0
        self.typing_active  = False
        self.edit_mode      = False
        self.typing_hook    = None

        # Solver
        self.last_img_hash = None
        self.last_letter   = "?"

        # Cursor
        self.h_normal_cache = None
        self.h_wait_cache   = None
        self.h_block_cache  = None

        # Global
        self.loading_event      = threading.Event()
        self.menu_open          = False
        self.cancel_event       = threading.Event()
        self.rshift_timer       = None
        self.request_id         = 0
        self.error_cursor_active = False
        self.error_cursor_id    = 0

state = AssistantState()


# ─── KEY HELPERS ──────────────────────────────────────────────────────────────
def mask_key(k):
    if not k:
        return "(empty)"
    if len(k) <= 12:
        return k
    return f"{k[:6]}...{k[-4:]}"

def switch_api_key(index):
    with state.lock:
        if index not in (1, 2, 3):
            return False, "Index must be 1, 2, or 3"
        state.active_key_index = index
        state.api_key = getattr(state, f"api_key_{index}", "")
        cfg = load_config()
        cfg["active_key_index"] = index
        cfg["api_key_1"] = state.api_key_1
        cfg["api_key_2"] = state.api_key_2
        cfg["api_key_3"] = state.api_key_3
        save_config(cfg)
        return True, f"Active key switched to #{index} ({mask_key(state.api_key)})"

def set_api_key(index, new_key):
    with state.lock:
        if index not in (1, 2, 3):
            return False, "Index must be 1, 2, or 3"
        new_key = (new_key or "").strip()
        setattr(state, f"api_key_{index}", new_key)
        if state.active_key_index == index:
            state.api_key = new_key
        cfg = load_config()
        cfg[f"api_key_{index}"] = new_key
        cfg["active_key_index"] = state.active_key_index
        save_config(cfg)
        return True, f"API Key #{index} updated: {mask_key(new_key)}"

def delete_api_key(index):
    with state.lock:
        if str(index).lower() in ("all", "*", "clear"):
            for i in (1, 2, 3):
                setattr(state, f"api_key_{i}", "")
            state.api_key = ""
            cfg = load_config()
            cfg["api_key_1"] = cfg["api_key_2"] = cfg["api_key_3"] = ""
            save_config(cfg)
            return True, "All API keys deleted/cleared."
        if index not in (1, 2, 3):
            return False, "Index must be 1, 2, or 3 (or 'all')"
        setattr(state, f"api_key_{index}", "")
        if state.active_key_index == index:
            state.api_key = ""
        cfg = load_config()
        cfg[f"api_key_{index}"] = ""
        save_config(cfg)
        return True, f"API Key #{index} deleted/cleared."


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def tg_send_message(text, parse_mode="HTML"):
    def _worker():
        token   = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode}, timeout=8)
            if r.status_code != 200 and parse_mode:
                requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=8)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def tg_send_photo(photo_bytes, caption="", parse_mode="HTML"):
    def _worker():
        token   = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            requests.post(url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode},
                files={"photo": ("screenshot.png", io.BytesIO(photo_bytes), "image/png")},
                timeout=15)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()


# ─── CURSOR ───────────────────────────────────────────────────────────────────
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
                state.h_normal_cache = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, 32512))
                state.h_wait_cache   = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, 32650))
                state.h_block_cache  = ctypes.windll.user32.CopyIcon(ctypes.windll.user32.LoadCursorW(0, 32648))
            except Exception:
                pass

def set_system_cursor(cursor_type):
    if not IS_WINDOWS:
        return
    init_cursors()
    try:
        h_src = {"NORMAL": state.h_normal_cache, "WAIT": state.h_wait_cache, "BLOCK": state.h_block_cache}.get(cursor_type)
        if h_src:
            ctypes.windll.user32.SetSystemCursor(ctypes.windll.user32.CopyIcon(h_src), 32512)
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
    if is_solver and not getattr(state, "error_cursor_active", False):
        set_system_cursor("NORMAL")


# ─── SCREENSHOT ───────────────────────────────────────────────────────────────
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


# ─── TYPER HELPERS ────────────────────────────────────────────────────────────
def strip_markdown(code):
    code = code.replace('\r', '').strip()
    m = re.search(r"```(?:python|cpp|c\+\+|c)?\n?(.*?)\n?```", code, re.DOTALL | re.IGNORECASE)
    if m:
        code = m.group(1).strip()
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
            result.append('\x08' * (editor_indent - target_indent) + content)
        else:
            result.append(' ' * (target_indent - editor_indent) + content)
        if content.rstrip().endswith(':') or content.rstrip().endswith('{'):
            editor_indent = target_indent + 2
        else:
            editor_indent = target_indent
    return '\n'.join(result)


# ─── SETTINGS MENU ────────────────────────────────────────────────────────────
def open_settings_menu():
    if state.menu_open:
        return
    state.menu_open = True
    root = tk.Tk()
    root.title("Settings")
    root.geometry("420x780")
    root.attributes('-topmost', True)
    mf = ttk.Frame(root, padding=10)
    mf.pack(fill=tk.BOTH, expand=True)

    def add_entry(label, var_name, is_text=False):
        ttk.Label(mf, text=label).pack(anchor=tk.W)
        if is_text:
            w = tk.Text(mf, height=3, width=40, font=("Consolas", 9))
            w.insert("1.0", getattr(state, var_name))
            w.pack(fill=tk.X, pady=(0, 5))
            return w
        else:
            w = ttk.Entry(mf, width=40)
            w.insert(0, getattr(state, var_name))
            w.pack(fill=tk.X, pady=(0, 5))
            return w

    k1 = add_entry("API Key 1", "api_key_1")
    k2 = add_entry("API Key 2", "api_key_2")
    k3 = add_entry("API Key 3", "api_key_3")
    ttk.Label(mf, text="Active API Key").pack(anchor=tk.W)
    active_var = tk.IntVar(value=state.active_key_index)
    kf = ttk.Frame(mf); kf.pack(fill=tk.X, pady=(0, 5))
    for i in (1, 2, 3):
        ttk.Radiobutton(kf, text=f"Key {i}", variable=active_var, value=i).pack(side=tk.LEFT, padx=5)
    fl_e  = add_entry("Flash Model",  "model_flash")
    pr_e  = add_entry("Pro Model",    "model_pro")
    so_e  = add_entry("Solver Model", "model_solver")
    ty_i  = add_entry("Typer Instr",      "system_instr_typer",     True)
    tc_i  = add_entry("Typer C++ Instr",  "system_instr_typer_cpp", True)
    sv_i  = add_entry("Solver Instr",     "system_instr_solver",    True)

    def on_apply():
        with state.lock:
            state.api_key_1 = k1.get().strip()
            state.api_key_2 = k2.get().strip()
            state.api_key_3 = k3.get().strip()
            state.active_key_index = active_var.get()
            state.api_key = getattr(state, f"api_key_{state.active_key_index}")
            state.model_flash  = fl_e.get().strip()
            state.model_pro    = pr_e.get().strip()
            state.model_solver = so_e.get().strip()
            state.system_instr_typer     = ty_i.get("1.0", tk.END).strip()
            state.system_instr_typer_cpp = tc_i.get("1.0", tk.END).strip()
            state.system_instr_solver    = sv_i.get("1.0", tk.END).strip()
            cfg = load_config()
            cfg.update({"api_key_1": state.api_key_1, "api_key_2": state.api_key_2, "api_key_3": state.api_key_3,
                        "active_key_index": state.active_key_index, "model_flash": state.model_flash,
                        "model_pro": state.model_pro, "model_solver": state.model_solver})
            save_config(cfg)
        root.destroy()
        state.menu_open = False

    ttk.Button(mf, text="Apply", command=on_apply).pack(pady=5)

    as_frame = ttk.LabelFrame(mf, text="Автозапуск (Autostart)", padding=6)
    as_frame.pack(fill=tk.X, pady=(5, 5))
    as_lbl = ttk.Label(as_frame, text="Статус: проверка...")
    as_lbl.pack(anchor=tk.W, pady=(0, 4))
    def upd_as():
        ok, methods = get_autostart_status()
        as_lbl.config(text=f"Статус: {'ВКЛЮЧЕН (' + ', '.join(methods) + ')' if ok else 'ВЫКЛЮЧЕН'}")
    def as_on():  setup_autostart(0); upd_as()
    def as_off(): remove_autostart(); upd_as()
    abf = ttk.Frame(as_frame); abf.pack(fill=tk.X)
    ttk.Button(abf, text="Включить (ON)",             command=as_on).pack(side=tk.LEFT, padx=(0, 5))
    ttk.Button(abf, text="Выключить / Удалить (OFF)", command=as_off).pack(side=tk.LEFT)
    upd_as()

    def on_close():
        state.menu_open = False
        root.destroy()
    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


# ─── TYPER ENGINE ─────────────────────────────────────────────────────────────
def type_one_char(ch):
    if   ch == '\n':   keyboard.press_and_release('enter')
    elif ch == '\t':   keyboard.press_and_release('tab')
    elif ch == '\x08': keyboard.press_and_release('backspace')
    elif ch == ' ':    keyboard.press_and_release('space')
    else:              keyboard.write(ch)

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


# ─── ACTION HANDLERS ──────────────────────────────────────────────────────────
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
        env_str  = "GCC" if is_cpp else "PyPy"
        system_instr = state.system_instr_typer_cpp if is_cpp else state.system_instr_typer

    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        img, _ = get_screenshot(active_only=False)
        if img:
            img_bytes = img_to_bytes(img)
            log(f"Typer ({lang_str}): screenshot taken, asking {model_name}...")
            res_text = gemini_generate_content(
                api_key=state.api_key,
                model_name=model_name,
                system_instruction=system_instr,
                prompt=f"Solve this Robocontest problem and return the {lang_str} code. (Environment: {env_str})",
                image_bytes=img_bytes
            )
            if state.cancel_event.is_set() or state.request_id != my_req_id:
                log("Typer request cancelled.")
                return
            code = adjust_for_autoindent(strip_markdown(res_text))
            with state.lock:
                state.code_buffer   = code
                state.code_index    = 0
                state.typing_active = True
            log(f"Typer ready! {len(code)} chars. Switch to editor.")
            start_typing_mode()
            tg_send_message(f"💻 Typer ({lang_str}) ready! {len(code)} chars.")
    except Exception as e:
        log(f"Typer Error: {e}", "ERROR")
        show_error_cursor()
        tg_send_message(f"❌ Typer Error: {e}")
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
        my_req_id   = state.request_id
        model_name  = state.model_solver

    try:
        img, rect = get_screenshot(active_only=True)
        if not img:
            return
        w, h = img.size
        img_bytes = img_to_bytes(img)
        curr_hash = hashlib.sha256(img_bytes).hexdigest()

        if curr_hash == state.last_img_hash:
            ans = state.last_letter
            log(f"Solver: Same image → {ans}")
            tg_send_message(f"🎯 Solver: Same image. Answer: {ans}")
            return

        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        log(f"Solver: Analyzing... ({model_name})")

        res_text = gemini_generate_content(
            api_key=state.api_key,
            model_name=model_name,
            system_instruction=state.system_instr_solver,
            prompt="Solve the question in this image. Provide the letter and coordinates.",
            image_bytes=img_bytes
        )
        if state.cancel_event.is_set() or state.request_id != my_req_id:
            log("Solver request cancelled.")
            return

        text = res_text.strip().upper()
        match = re.search(r"([A-D])\s*\[(\d+),\s*(\d+)\]", text)

        if match:
            ans = match.group(1)
            nx, ny = int(match.group(2)), int(match.group(3))
            tx = rect[0] + int(nx * w / 1000)
            ty = rect[1] + int(ny * h / 1000)
            log(f"Solver: {ans} at ({tx}, {ty})")

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
                    log("Solver: Clicked.")
                    click_done = True
                time.sleep(0.01)
        else:
            ans = text[:1] if text[:1] in "ABCD" else "?"
            log(f"Solver: {ans} (no coords)")

        state.last_img_hash = curr_hash
        state.last_letter   = ans
        tg_send_message(f"🎯 Solver Answer: {ans}")

    except Exception as e:
        log(f"Solver Error: {e}", "ERROR")
        show_error_cursor()
        tg_send_message(f"❌ Solver Error: {e}")
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


# ─── GLOBAL HOTKEY ────────────────────────────────────────────────────────────
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
        switch_api_key(int(event.name))
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


# ─── AUTOSTART (4 METHODS) ────────────────────────────────────────────────────
def get_startup_cmd():
    if getattr(sys, 'frozen', False):
        return f'"{os.path.abspath(sys.executable)}"'
    script_path = os.path.abspath(__file__)
    python_exe  = sys.executable
    if python_exe.lower().endswith("python.exe"):
        pythonw = os.path.join(os.path.dirname(python_exe), "pythonw.exe")
        if os.path.exists(pythonw):
            python_exe = pythonw
    return f'"{python_exe}" "{script_path}"'

def setup_autostart(method: int = 0):
    if not IS_WINDOWS:
        return False, "Only Windows supported"
    app_name = "sistem"
    cmd = get_startup_cmd()
    success_list = []

    try:
        if winreg:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            success_list.append("1. Реестр (HKCU Run - sistem)")
    except Exception:
        pass

    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.exists(startup_dir):
            vbs = os.path.join(startup_dir, "sistem_start.vbs")
            with open(vbs, "w", encoding="utf-8") as f:
                f.write(f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run "{cmd.replace(chr(34), chr(34)+chr(34))}", 0, False\n')
            success_list.append("2. Скрытый VBS в Автозагрузке")
            bat = os.path.join(startup_dir, "sistem_runner.bat")
            with open(bat, "w", encoding="utf-8") as f:
                f.write(f'@echo off\nstart "" {cmd}\nexit\n')
            success_list.append("4. BAT-раннер в Автозагрузке")
    except Exception:
        pass

    try:
        task_name = "sistem"
        subprocess.run(["schtasks", "/delete", "/tn", task_name, "/f"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = subprocess.run(["schtasks", "/create", "/tn", task_name, "/tr", cmd, "/sc", "onlogon", "/rl", "highest", "/f"], capture_output=True, text=True)
        if res.returncode != 0:
            res = subprocess.run(["schtasks", "/create", "/tn", task_name, "/tr", cmd, "/sc", "onlogon", "/f"], capture_output=True, text=True)
        if res.returncode == 0:
            success_list.append("3. Планировщик задач (onlogon)")
    except Exception:
        pass

    if success_list:
        return True, "Автозапуск настроен:\n• " + "\n• ".join(success_list)
    return False, "Не удалось настроить автозапуск"

def remove_autostart():
    if not IS_WINDOWS:
        return False, "Only Windows supported"
    removed = []
    try:
        if winreg:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            for a_name in ("sistem", "OmniAssistant"):
                try:
                    winreg.DeleteValue(key, a_name)
                    removed.append(f"1. Реестр ({a_name})")
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
    except Exception:
        pass
    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        for fname in ("sistem_start.vbs", "omni_assistant_start.vbs", "sistem_runner.bat", "omni_assistant_runner.bat"):
            fp = os.path.join(startup_dir, fname)
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                    removed.append(f"  {fname}")
                except Exception:
                    pass
    except Exception:
        pass
    try:
        for tname in ("sistem", "OmniAssistantTask"):
            res = subprocess.run(["schtasks", "/delete", "/tn", tname, "/f"], capture_output=True, text=True)
            if res.returncode == 0:
                removed.append(f"3. Планировщик ({tname})")
    except Exception:
        pass
    if removed:
        return True, "Автозапуск удален из:\n• " + "\n• ".join(removed)
    return True, "Автозапуск уже отключен (активных записей не найдено)"

def get_autostart_status():
    if not IS_WINDOWS:
        return False, ["Not supported"]
    active = []
    try:
        if winreg:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
            for a_name in ("sistem", "OmniAssistant"):
                try:
                    val, _ = winreg.QueryValueEx(key, a_name)
                    if val:
                        active.append(f"Реестр ({a_name})")
                        break
                except FileNotFoundError:
                    pass
            winreg.CloseKey(key)
    except Exception:
        pass
    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        if os.path.exists(os.path.join(startup_dir, "sistem_start.vbs")):
            active.append("VBS (Startup)")
        if os.path.exists(os.path.join(startup_dir, "sistem_runner.bat")):
            active.append("BAT (Startup)")
    except Exception:
        pass
    try:
        for t_name in ("sistem", "OmniAssistantTask"):
            res = subprocess.run(f'schtasks /query /tn "{t_name}"', shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                active.append(f"Планировщик ({t_name})")
                break
    except Exception:
        pass
    return len(active) > 0, active


# ─── WEBCAM ───────────────────────────────────────────────────────────────────
def capture_webcam():
    global cv2
    if cv2 is None:
        try:
            import cv2 as _cv2
            cv2 = _cv2
        except ImportError:
            try:
                subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python", "--quiet"],
                               check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                import cv2 as _cv2
                cv2 = _cv2
            except Exception:
                pass
    if cv2 is None:
        return None, "cv2 not installed"
    cap = None
    try:
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW if IS_WINDOWS else 0)
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(0)
            if not cap.isOpened():
                cap.release()
                return None, "Камера не найдена или занята"
        for _ in range(5):
            cap.read()
            time.sleep(0.04)
        ret, frame = cap.read()
        if not ret or frame is None:
            return None, "Не удалось захватить кадр"
        ret, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ret:
            return None, "Ошибка кодирования JPEG"
        return buf.tobytes(), None
    except Exception as e:
        return None, f"Ошибка: {e}"
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


# ─── LOG WATCHER ──────────────────────────────────────────────────────────────
def log_watcher_thread():
    last_pos    = 0
    first_check = True
    while state.is_running:
        if getattr(state, "frozen", False):
            time.sleep(1.0)
            continue
        try:
            log_path = state.log_watch_path
            if log_path and os.path.exists(log_path):
                file_size = os.path.getsize(log_path)
                if first_check:
                    last_pos    = file_size
                    first_check = False
                elif file_size < last_pos:
                    last_pos = 0
                if file_size > last_pos:
                    with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        last_pos  = f.tell()
                    for line in new_lines:
                        with state.lock:
                            targets = list(state.target_logins)
                        for target in targets:
                            if target and target.lower() in line.lower():
                                tg_send_message(f"🚨 [ПК: {state.pc_name}] Логин '{target}' обнаружен!\n⚡ Запускаю SOLVER...")
                                threading.Thread(target=handle_solver_action, daemon=True).start()
                                break
        except Exception:
            pass
        time.sleep(1.0)


# ─── TELEGRAM BOT POLLER ──────────────────────────────────────────────────────
def tg_bot_poller():
    offset = None
    tg_send_message(
        f"┌──────────────────────┐\n"
        f"│ 🟢 <b>OMNI ASSISTANT</b> ONLINE │\n"
        f"│ 💻 PC: <code>{state.pc_name}</code>          │\n"
        f"└──────────────────────┘\n"
        f"🤖 Bot ready! Mode: <code>{state.mode}</code>  /help — all commands"
    )

    while state.is_running:
        try:
            token  = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
            url    = f"https://api.telegram.org/bot{token}/getUpdates"
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
                chat_id = str(msg.get("chat", {}).get("id", ""))
                target_chat = str(getattr(state, "tg_chat_id", TG_CHAT_ID))
                if target_chat and chat_id != target_chat:
                    continue
                text = (msg.get("text") or "").strip()
                if not text:
                    continue

                parts = text.split()
                if not parts:
                    continue

                pc_lower = state.pc_name.lower()
                target_pc   = None
                clean_tokens = []
                for p in parts:
                    pl = p.lower()
                    if pl.startswith("@"):
                        tag = pl[1:]
                        if tag not in ("all", "все", "bot"):
                            target_pc = tag
                    elif pl == pc_lower:
                        target_pc = pc_lower
                    else:
                        clean_tokens.append(p)

                if target_pc and target_pc != pc_lower:
                    continue
                if not clean_tokens:
                    continue

                cmd  = clean_tokens[0].lower().split('@')[0]
                args = clean_tokens[1:]

                # ── /start /help ──────────────────────────────────
                if cmd in ("/start", "/help"):
                    tg_send_message(
                        f"╔══════════════════════════╗\n"
                        f"║  🤖  <b>OMNI ASSISTANT</b>  ⚡  ║\n"
                        f"║  🌐  GLOBAL CONTROL    ║\n"
                        f"╚══════════════════════════╝\n"
                        f"\n<b>🖥 SYSTEM CONTROL</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📊 /status      — system status\n"
                        f"❄️ /freeze      — pause bot actions\n"
                        f"▶️ /unfreeze    — resume bot actions\n"
                        f"⚡ /autostart <code>on|off</code> — автозапуск\n"
                        f"💀 /terminate   — завершить процесс\n"
                        f"🌐 /list_pc     — online PCs\n"
                        f"\n<b>📸 MEDIA &amp; SCREEN</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🖼 /screenshot  — screen capture\n"
                        f"📷 /webcam      — webcam photo\n"
                        f"\n<b>👥 LOGIN WATCHER</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"➕ /login add <code>st0001, st0002</code>\n"
                        f"➖ /login del <code>st0001</code>\n"
                        f"📋 /login list\n"
                        f"🧹 /login clear\n"
                        f"\n<b>🔑 MODES &amp; KEYS</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🎯 /solver          — Solver mode\n"
                        f"🐍 /python          — Python Typer mode\n"
                        f"⚙️ /cpp             — C++ Typer mode\n"
                        f"🔑 /key <code>1|2|3</code>       — switch API key\n"
                        f"🔄 /change key <code>1 &lt;key&gt;</code> — set API key\n"
                        f"🗑️ /del key <code>1|2|3|all</code> — delete key\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"💡 Таргет: <code>@Имя /команда</code>"
                    )

                # ── /list_pc ──────────────────────────────────────
                elif cmd == "/list_pc":
                    me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                    si = "❄️" if state.frozen else ("⏳" if state.busy else "✅")
                    ss = "Frozen" if state.frozen else ("Busy" if state.busy else "Ready")
                    tg_send_message(
                        f"💻 <b>ONLINE PC:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🖥 <code>{state.pc_name}</code>\n"
                        f"{me} Mode: <b>{state.mode}</b>\n"
                        f"{si} State: <b>{ss}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )

                # ── /freeze ───────────────────────────────────────
                elif cmd == "/freeze":
                    with state.lock:
                        state.frozen = True
                        state.cancel_event.set()
                        state.loading_event.clear()
                        state.busy = False
                        state.request_id += 1
                    stop_typing_mode()
                    set_system_cursor("NORMAL")
                    tg_send_message("❄️ Bot FROZEN — all actions paused until /unfreeze.")

                # ── /unfreeze ─────────────────────────────────────
                elif cmd == "/unfreeze":
                    with state.lock:
                        state.frozen = False
                    tg_send_message("▶️ Bot UNFROZEN — normal operation resumed.")

                # ── /terminate ────────────────────────────────────
                elif cmd in ("/terminate", "terminate", "/kill", "kill", "/терминате", "терминате", "/exit", "exit", "/стоп", "стоп"):
                    log(f"TERMINATE requested from Telegram, PC: {state.pc_name}")
                    set_system_cursor("NORMAL")
                    stop_typing_mode()
                    tg_send_message(f"💀 <b>Процесс завершен</b> на ПК <code>{state.pc_name}</code>.")
                    time.sleep(0.5)
                    try:
                        os.kill(os.getpid(), 9)
                    except Exception:
                        pass
                    sys.exit(0)

                # ── /autostart ────────────────────────────────────
                elif cmd in ("/autostart", "/autostart_on", "/autostart_off", "/autostart_del"):
                    sub = args[0].lower() if args else ""
                    if cmd == "/autostart_on" or sub in ("on", "enable", "вкл", "+", "1"):
                        ok, info = setup_autostart(0)
                        tg_send_message(f"{'✅' if ok else '❌'} {info}")
                    elif cmd in ("/autostart_off", "/autostart_del") or sub in ("off", "del", "disable", "выкл", "-", "0"):
                        ok, info = remove_autostart()
                        tg_send_message(f"🗑️ {info}")
                    else:
                        ok, methods = get_autostart_status()
                        st_str = f"🟢 <b>ВКЛЮЧЕН</b> ({', '.join(methods)})" if ok else "🔴 <b>ВЫКЛЮЧЕН</b>"
                        tg_send_message(
                            f"⚡ <b>АВТОЗАПУСК:</b> {st_str}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• Включить: <code>/autostart on</code>\n"
                            f"• Выключить: <code>/autostart off</code>"
                        )

                # ── /screenshot ───────────────────────────────────
                elif cmd == "/screenshot":
                    img, _ = get_screenshot(active_only=False)
                    if img:
                        tg_send_photo(img_to_bytes(img), caption=f"📸 Screenshot (Mode: {state.mode})")
                    else:
                        tg_send_message("⚠️ Failed to capture screenshot")

                # ── /webcam ───────────────────────────────────────
                elif cmd == "/webcam":
                    photo, err = capture_webcam()
                    if photo:
                        tg_send_photo(photo, caption="📷 Webcam")
                    else:
                        tg_send_message(f"⚠️ Webcam: {err}")

                # ── /login ────────────────────────────────────────
                elif cmd in ("/login", "/logins"):
                    sub = args[0].lower() if args else "list"
                    if sub in ("add", "+"):
                        raw = " ".join(args[1:])
                        items = [x.strip() for x in re.split(r"[,;]+", raw) if x.strip()]
                        if items:
                            with state.lock:
                                for item in items:
                                    state.target_logins.add(item)
                                cfg = load_config()
                                cfg["target_logins"] = list(state.target_logins)
                                save_config(cfg)
                            tg_send_message(f"✅ Added {len(items)} login(s): {', '.join(f'`{x}`' for x in items)}")
                        else:
                            tg_send_message("⚠️ Usage: `/login add st0001, st0002`")
                    elif sub in ("del", "remove", "rm", "-"):
                        raw = " ".join(args[1:])
                        items = [x.strip() for x in re.split(r"[,;]+", raw) if x.strip()]
                        removed = []
                        with state.lock:
                            for item in items:
                                if item in state.target_logins:
                                    state.target_logins.discard(item)
                                    removed.append(item)
                            if removed:
                                cfg = load_config()
                                cfg["target_logins"] = list(state.target_logins)
                                save_config(cfg)
                        tg_send_message(f"🗑️ Removed: {', '.join(removed)}" if removed else "ℹ️ Not found.")
                    elif sub in ("clear", "reset"):
                        with state.lock:
                            state.target_logins.clear()
                            cfg = load_config()
                            cfg["target_logins"] = []
                            save_config(cfg)
                        tg_send_message("🧹 Login list cleared.")
                    else:
                        with state.lock:
                            logins = list(state.target_logins)
                        if logins:
                            txt = f"📋 Watched logins ({len(logins)}):\n" + "\n".join(f"• `{l}`" for l in logins)
                        else:
                            txt = "📋 Login list is empty.\nAdd: `/login add st0001, st0002`"
                        txt += f"\n\nLog file: `{state.log_watch_path}`"
                        tg_send_message(txt)

                # ── /set_logfile ──────────────────────────────────
                elif cmd == "/set_logfile":
                    if args:
                        new_path = " ".join(args).strip('"')
                        with state.lock:
                            state.log_watch_path = new_path
                            cfg = load_config()
                            cfg["log_watch_path"] = new_path
                            save_config(cfg)
                        tg_send_message(f"✅ Log path: `{new_path}`")
                    else:
                        tg_send_message("⚠️ Usage: `/set_logfile C:\\path\\to\\log.txt`")

                # ── /change key / /set_key ────────────────────────
                elif cmd in ("/change_key", "/set_key", "/setkey", "/change", "/set") and (
                        cmd not in ("/change", "/set") or (args and args[0].lower() in ("key", "api", "apikey", "token"))):
                    key_args = args[1:] if (cmd in ("/change", "/set") and args and args[0].lower() in ("key", "api", "apikey", "token")) else args
                    if not key_args:
                        tg_send_message(f"⚠️ Usage:\n• <code>/change key 1 &lt;KEY&gt;</code>\n• <code>/change key 2 &lt;KEY&gt;</code>\n• <code>/change key 3 &lt;KEY&gt;</code>")
                    elif len(key_args) >= 3:
                        for i, k in enumerate(key_args[:3], 1):
                            set_api_key(i, k)
                        tg_send_message(f"✅ All 3 keys updated!\n#1: <code>{mask_key(state.api_key_1)}</code>\n#2: <code>{mask_key(state.api_key_2)}</code>\n#3: <code>{mask_key(state.api_key_3)}</code>")
                    elif key_args[0] in ("1", "2", "3"):
                        slot = int(key_args[0])
                        if len(key_args) >= 2:
                            ok, info = set_api_key(slot, key_args[1])
                            tg_send_message(f"✅ {info}")
                        else:
                            tg_send_message(f"⚠️ Укажите ключ: <code>/change key {slot} &lt;KEY&gt;</code>")
                    else:
                        ok, info = set_api_key(state.active_key_index, key_args[0])
                        tg_send_message(f"✅ {info}")

                # ── /del key ──────────────────────────────────────
                elif cmd in ("/del_key", "/delkey", "/delete_key", "/del", "/delete", "/rm") and (
                        cmd not in ("/del", "/delete", "/rm") or (args and args[0].lower() in ("key", "api", "apikey"))):
                    key_args = args[1:] if (cmd in ("/del", "/delete", "/rm") and args and args[0].lower() in ("key", "api", "apikey")) else args
                    slot = key_args[0].lower() if key_args else str(state.active_key_index)
                    if slot in ("all", "*", "все"):
                        delete_api_key("all")
                        tg_send_message("🗑️ Все API ключи удалены.")
                    elif slot in ("1", "2", "3"):
                        ok, info = delete_api_key(int(slot))
                        tg_send_message(f"🗑️ {info}")
                    else:
                        tg_send_message("⚠️ <code>/del key 1|2|3|all</code>")

                # ── /status ───────────────────────────────────────
                elif cmd == "/status":
                    me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                    si = "❄️ FROZEN" if state.frozen else ("⏳ Busy" if state.busy else "✅ Ready")
                    tg_send_message(
                        f"┌──────────────────────┐\n"
                        f"│  📊 <b>SYSTEM STATUS</b>       │\n"
                        f"└──────────────────────┘\n\n"
                        f"🖥 PC:         <code>{state.pc_name}</code>\n"
                        f"⚡ State:      <b>{si}</b>\n"
                        f"{me} Mode:       <b>{state.mode}</b>\n"
                        f"🔑 Active Key: <code>#{state.active_key_index}</code> ({mask_key(state.api_key)})\n"
                        f"   • #1: <code>{mask_key(state.api_key_1)}</code>\n"
                        f"   • #2: <code>{mask_key(state.api_key_2)}</code>\n"
                        f"   • #3: <code>{mask_key(state.api_key_3)}</code>\n"
                        f"🤖 Model:      <code>{state.model_solver}</code>\n"
                        f"🎯 Last Ans:   <b>{state.last_letter}</b>\n"
                        f"👥 Logins:     <b>{len(state.target_logins)}</b>\n"
                        f"📹 Webcam:     <b>{'✅ Ready (cv2)' if cv2 is not None else '⚠️ Not installed'}</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                    )

                # ── /solver /python /cpp ──────────────────────────
                elif cmd == "/solver":
                    with state.lock: state.mode = "SOLVER"
                    tg_send_message("✅ Mode: <b>SOLVER</b>")
                elif cmd in ("/typer_py", "/python"):
                    with state.lock: state.mode = "TYPER_PYTHON"
                    tg_send_message("✅ Mode: <b>TYPER_PYTHON</b>")
                elif cmd in ("/typer_cpp", "/cpp"):
                    with state.lock: state.mode = "TYPER_CPP"
                    tg_send_message("✅ Mode: <b>TYPER_CPP</b>")

                # ── /key ──────────────────────────────────────────
                elif cmd in ("/key", "/keys"):
                    sub = args[0].lower() if args else ""
                    if sub in ("1", "2", "3"):
                        ok, info = switch_api_key(int(sub))
                        tg_send_message(f"🔑 {info}")
                    else:
                        tg_send_message(
                            f"🔑 <b>API KEYS:</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"{'👉 ' if state.active_key_index==1 else '   '}<b>#1:</b> <code>{mask_key(state.api_key_1)}</code>\n"
                            f"{'👉 ' if state.active_key_index==2 else '   '}<b>#2:</b> <code>{mask_key(state.api_key_2)}</code>\n"
                            f"{'👉 ' if state.active_key_index==3 else '   '}<b>#3:</b> <code>{mask_key(state.api_key_3)}</code>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• Switch: <code>/key 1|2|3</code>\n"
                            f"• Change: <code>/change key 1 &lt;KEY&gt;</code>\n"
                            f"• Delete: <code>/del key 1|2|3</code>"
                        )

        except Exception:
            time.sleep(3)


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) > 1:
        a1 = sys.argv[1].lower()
        a2 = sys.argv[2].lower() if len(sys.argv) > 2 else ""
        if a1 in ("--autostart-on", "/autostart-on") or (a1 == "autostart" and a2 in ("on", "enable")):
            ok, msg = setup_autostart(0)
            print(f"[Autostart ON] {msg}")
            sys.exit(0 if ok else 1)
        elif a1 in ("--autostart-off", "/autostart-off", "--autostart-del") or (a1 == "autostart" and a2 in ("off", "del")):
            ok, msg = remove_autostart()
            print(f"[Autostart OFF] {msg}")
            sys.exit(0 if ok else 1)
        elif a1 in ("--autostart-status", "/autostart-status"):
            is_active, active_list = get_autostart_status()
            print(f"[Autostart] Active: {is_active}. Methods: {active_list}")
            sys.exit(0)

    acquire_single_instance_mutex()
    hide_console_window()

    keyboard.hook(on_global_hotkey)
    threading.Thread(target=tg_bot_poller,     daemon=True).start()
    threading.Thread(target=log_watcher_thread, daemon=True).start()

    try:
        keyboard.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
