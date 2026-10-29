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
import random
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


# ─── GEMINI CALLER (5-KEY FAILOVER WITH 65s AUTO-RECOVERY TO #1) ─────────────
KEY_COOLDOWNS = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
COOLDOWN_SECONDS = 65.0

def mark_key_exhausted(slot_idx: int, reason: str = ""):
    if slot_idx in KEY_COOLDOWNS:
        now = time.time()
        KEY_COOLDOWNS[slot_idx] = now + COOLDOWN_SECONDS
        log(f"⚠️ API Key #{slot_idx} rate-limited / quota exhausted ({reason}). Cooldown 65s.", "WARN")
        st = globals().get("state")
        if st:
            try:
                with st.lock:
                    st.key_cooldowns[slot_idx] = now + COOLDOWN_SECONDS
            except Exception:
                pass

def get_prioritized_keys(explicit_key=None):
    st = globals().get("state")
    now = time.time()
    ready_slots = []
    cooldown_slots = []

    for slot in (1, 2, 3, 4, 5):
        k = getattr(st, f"api_key_{slot}", "") if st else ""
        if not k:
            cfg = globals().get("config_data") or {}
            k = cfg.get(f"api_key_{slot}", "")
        if not k:
            continue
        cd = KEY_COOLDOWNS.get(slot, 0.0)
        if now >= cd:
            ready_slots.append((slot, k))
        else:
            cooldown_slots.append((slot, k, cd - now))

    cooldown_slots.sort(key=lambda x: x[2])
    candidates = ready_slots + [(s, k) for s, k, _ in cooldown_slots]
    if explicit_key and not any(k == explicit_key for _, k in candidates):
        candidates.insert(0, (0, explicit_key))
    return candidates

def gemini_generate_content(api_key, model_name, system_instruction, prompt, image_bytes=None, image_b64=None):
    if image_bytes is None and image_b64 is not None:
        try:
            image_bytes = base64.b64decode(image_b64)
        except Exception:
            pass

    key_candidates = get_prioritized_keys(explicit_key=api_key)
    if not key_candidates:
        raise RuntimeError("No Gemini API keys configured!")

    models_to_try = [
        model_name,
        "gemini-3.5-flash",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash"
    ]
    cleaned = []
    seen = set()
    for m in models_to_try:
        if not m:
            continue
        m = m.strip()
        if m not in seen:
            seen.add(m)
            cleaned.append(m)
    if not cleaned:
        cleaned = ["gemini-3.5-flash", "gemini-2.5-flash"]

    last_err = None
    st = globals().get("state")

    for slot_idx, key in key_candidates:
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
                cfg = types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                    top_p=0.95
                ) if system_instruction else types.GenerateContentConfig(temperature=0.1, top_p=0.95)
                resp = client.models.generate_content(model=m_name, contents=contents, config=cfg)
                if resp and resp.text:
                    if st and slot_idx > 0 and getattr(st, "active_key_index", None) != slot_idx:
                        with st.lock:
                            st.active_key_index = slot_idx
                            st.api_key = key
                        log(f"🔄 Active API Key is #{slot_idx} ({mask_key(key)})", "OK")
                    return resp.text
            except Exception as sdk_e:
                e_msg = str(sdk_e).lower()
                if "404" in e_msg or "not_found" in e_msg:
                    continue
                if "429" in e_msg or "quota" in e_msg or "resource_exhausted" in e_msg:
                    mark_key_exhausted(slot_idx, "SDK 429 Quota")
                    last_err = sdk_e
                    break
                if "401" in e_msg or "403" in e_msg or "leaked" in e_msg:
                    mark_key_exhausted(slot_idx, "Auth/Invalid")
                    last_err = sdk_e
                    break

            # Method 2: High-speed REST API
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_name}:generateContent?key={key}"
                parts = []
                if prompt:
                    parts.append({"text": prompt})
                if image_bytes:
                    b64_val = base64.b64encode(image_bytes).decode("utf-8")
                    parts.append({"inline_data": {"mime_type": "image/png", "data": b64_val}})
                payload = {
                    "contents": [{"parts": parts}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "topP": 0.95
                    }
                }
                if system_instruction:
                    payload["system_instruction"] = {"parts": [{"text": system_instruction}]}
                resp = requests.post(
                    url,
                    headers={"Content-Type": "application/json", "x-goog-api-key": key},
                    json=payload,
                    timeout=45
                )
                if resp.status_code in (404, 400):
                    continue
                if resp.status_code == 429 or "quota" in resp.text.lower() or "resource_exhausted" in resp.text.lower():
                    mark_key_exhausted(slot_idx, f"REST 429 Quota ({resp.status_code})")
                    last_err = Exception(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    break
                if resp.status_code in (401, 403):
                    mark_key_exhausted(slot_idx, f"REST Auth {resp.status_code}")
                    last_err = Exception(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    break
                resp.raise_for_status()
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates and "content" in candidates[0]:
                    text_parts = [p.get("text", "") for p in candidates[0]["content"].get("parts", [])]
                    ans_text = "".join(text_parts)
                    if st and slot_idx > 0 and getattr(st, "active_key_index", None) != slot_idx:
                        with st.lock:
                            st.active_key_index = slot_idx
                            st.api_key = key
                        log(f"🔄 Active API Key is #{slot_idx} ({mask_key(key)})", "OK")
                    return ans_text
            except requests.exceptions.Timeout as timeout_e:
                log(f"⚠️ Request timed out on key #{slot_idx} ({m_name}). Switching key...", "WARN")
                last_err = timeout_e
                # Break to next key candidate immediately instead of waiting through 5 models
                break
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

# ─── SINGLE INSTANCE PROTECTION ───────────────────────────────────────────────
SINGLE_INSTANCE_SOCKET = None
SINGLE_INSTANCE_MUTEX = None
SINGLE_INSTANCE_LOCK_FILE = None

def acquire_single_instance_mutex():
    global SINGLE_INSTANCE_SOCKET, SINGLE_INSTANCE_MUTEX, SINGLE_INSTANCE_LOCK_FILE
    # Tier 0: Loopback TCP port lock (atomic, OS kernel enforced, 100% reliable across all processes)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        s.bind(("127.0.0.1", 49812))
        s.listen(1)
        SINGLE_INSTANCE_SOCKET = s
    except Exception as e:
        log(f"Single instance protection: Another instance already running on 127.0.0.1:49812 ({e}). Exiting.")
        sys.exit(0)

    if IS_WINDOWS:
        # Tier 1: Win32 Named Mutex in Local\ namespace
        try:
            safe_id = hashlib.md5(f"OmniAssistant_{PC_NAME}".encode("utf-8")).hexdigest()
            mutex_name = f"Local\\OmniAssistantLock_{safe_id}"
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
            last_err = ctypes.windll.kernel32.GetLastError()
            if last_err in (183, 5) or handle == 0:  # 183 = ERROR_ALREADY_EXISTS, 5 = ACCESS_DENIED
                log(f"Single instance protection: Mutex already exists (err={last_err}). Exiting.")
                sys.exit(0)
            SINGLE_INSTANCE_MUTEX = handle
        except SystemExit:
            raise
        except Exception as e:
            log(f"Mutex creation notice: {e}")

        # Tier 2: Exclusive lockfile in %TEMP% using msvcrt (kernel file locking)
        try:
            import tempfile
            import msvcrt
            safe_id = hashlib.md5(f"OmniAssistant_{PC_NAME}".encode("utf-8")).hexdigest()[:12]
            lock_path = os.path.join(tempfile.gettempdir(), f"omni_{safe_id}.lock")
            SINGLE_INSTANCE_LOCK_FILE = open(lock_path, "a+b")
            try:
                msvcrt.locking(SINGLE_INSTANCE_LOCK_FILE.fileno(), msvcrt.LK_NBLCK, 1)
            except (IOError, OSError):
                log("Single instance protection: Lockfile already held by another process. Exiting.")
                sys.exit(0)
        except SystemExit:
            raise
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

_NK1 = base64.b64decode("QVEuQWI4Uk42S0NxZTEtRXFvdGR6OEF0eFozS0xWamRuZUIzbkdUM3VNakxrOFlRUUhVWlE=").decode("utf-8")
_NK2 = base64.b64decode("QVEuQWI4Uk42TDJkcDJ6Z18tcXF0NW1GNTRwcHVOeEpibjJSN216M0VUYUNYd2dVWFIxZmc=").decode("utf-8")
_NK3 = base64.b64decode("QVEuQWI4Uk42SUNsTUtUT2FKM0V5UGNaSmo1aVhpUUxiTjlxWjdDZlBEbHZxVTVLOEVSYkE=").decode("utf-8")
_NK4 = base64.b64decode("QVEuQWI4Uk42SXRlaWNqdWJJcjMwdk9CWHBLWkhleGpqWlFjQ1c1YkdoeGZkcXh6Wjh4SWc=").decode("utf-8")
_NK5 = base64.b64decode("QVEuQWI4Uk42S0NxZTEtRXFvdGR6OEF0eFozS0xWamRuZUIzbkdUM3VNakxrOFlRUUhVWlE=").decode("utf-8")

DEFAULT_CONFIG = {
    "api_key_1": _NK1,
    "api_key_2": _NK2,
    "api_key_3": _NK3,
    "api_key_4": _NK4,
    "api_key_5": _NK5,
    "active_key_index": 1,
    "model_flash":  "gemini-3.5-flash",
    "model_pro":    "gemini-1.5-pro",
    "model_solver": "gemini-3.5-flash",
    "tg_bot_token": TG_BOT_TOKEN,
    "tg_chat_id":   TG_CHAT_ID,
    "pc_name":      PC_NAME,
    "target_logins": [],
    "log_watch_path": os.path.join(BASE_DIR, "app_activity.log"),
    "solver_password": "",
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
            _def_keys = [None, _NK1, _NK2, _NK3, _NK4, _NK5]
            for i in range(1, 6):
                k = f"api_key_{i}"
                if k not in cfg:
                    cfg[k] = _def_keys[i]
            if "pc_name" not in cfg or not (cfg.get("pc_name") or "").strip():
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
    "You are an Elite Academic and Competitive Test Solver with 100% precision in test questions, coding, math, and logic.\n"
    "RULES:\n"
    "1. Read the ENTIRE question text and ALL answer options CAREFULLY before deciding.\n"
    "2. For math/logic: compute step by step. For code: trace execution mentally.\n"
    "3. Compare ALL options (A, B, C, D). Eliminate wrong ones by proof, NOT by intuition.\n"
    "4. Watch out for trick questions, double negatives, and edge cases.\n"
    "5. Once certain, find the EXACT center pixel [X, Y] of the correct option's clickable radio button/checkbox.\n"
    "   - Coordinates are normalized 0-1000 (0,0=top-left, 1000,1000=bottom-right of the image).\n"
    "   - X is horizontal position, Y is vertical position.\n"
    "   - Target the CENTER of the circle/checkbox, NOT the text label.\n"
    "6. Output format (STRICTLY follow this — no other text after ANSWER line):\n"
    "Step: <one-sentence reasoning showing WHY this answer is correct>\n"
    "ANSWER: <LETTER> [<X>, <Y>]\n"
    "Example:\n"
    "Step: 2^10 = 1024, so option B is the only correct value.\n"
    "ANSWER: B [125, 680]"
)


# ─── STATE ────────────────────────────────────────────────────────────────────
class AssistantState:
    def __init__(self):
        self.mode = "SOLVER"  # SOLVER | TYPER_PYTHON | TYPER_CPP
        self.busy = False
        self.lock = threading.Lock()

        cfg_name = str(config_data.get("pc_name") or "").strip()
        self.pc_name        = cfg_name if (cfg_name and cfg_name.lower() not in ("auto", "default", "none")) else PC_NAME
        self.target_logins  = set(config_data.get("target_logins", []))
        self.log_watch_path = config_data.get("log_watch_path", DEFAULT_CONFIG["log_watch_path"])
        self.is_running     = True

        self.api_key_1       = config_data.get("api_key_1", _NK1)
        self.api_key_2       = config_data.get("api_key_2", _NK2)
        self.api_key_3       = config_data.get("api_key_3", _NK3)
        self.api_key_4       = config_data.get("api_key_4", _NK4)
        self.api_key_5       = config_data.get("api_key_5", _NK5)
        self.active_key_index = config_data.get("active_key_index", 1)
        self.key_cooldowns   = {1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
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

        # Password unlock (solver locked until correct password)
        self.solver_password     = config_data.get("solver_password", "")
        self.solver_unlocked     = False
        self.key_listener_active = False
        self.key_buffer          = ""
        self.auto_lock_timer     = None
        self.unlocked_at         = 0.0

state = AssistantState()


# ─── KEY HELPERS ──────────────────────────────────────────────────────────────
def mask_key(k):
    if not k:
        return "(empty)"
    if len(k) <= 12:
        return k
    return f"{k[:6]}...{k[-4:]}"

def get_key_status_badge(slot: int):
    now = time.time()
    cd = KEY_COOLDOWNS.get(slot, 0.0)
    if cd > now:
        return f"⏳ Cooldown ({int(cd - now)}s)"
    st = globals().get("state")
    if st and getattr(st, "active_key_index", 1) == slot:
        return "👉 Active"
    return "✅ Ready"

def switch_api_key(index):
    with state.lock:
        if index not in (1, 2, 3, 4, 5):
            return False, "Index must be 1, 2, 3, 4, or 5"
        state.active_key_index = index
        state.api_key = getattr(state, f"api_key_{index}", "")
        cfg = load_config()
        cfg["active_key_index"] = index
        for i in range(1, 6):
            cfg[f"api_key_{i}"] = getattr(state, f"api_key_{i}", "")
        save_config(cfg)
        return True, f"Active key switched to #{index} ({mask_key(state.api_key)})"

def set_api_key(index, new_key):
    with state.lock:
        if index not in (1, 2, 3, 4, 5):
            return False, "Index must be 1, 2, 3, 4, or 5"
        new_key = (new_key or "").strip()
        setattr(state, f"api_key_{index}", new_key)
        if state.active_key_index == index:
            state.api_key = new_key
        KEY_COOLDOWNS[index] = 0.0
        state.key_cooldowns[index] = 0.0
        cfg = load_config()
        cfg[f"api_key_{index}"] = new_key
        cfg["active_key_index"] = state.active_key_index
        save_config(cfg)
        return True, f"API Key #{index} updated: {mask_key(new_key)}"

def delete_api_key(index):
    with state.lock:
        if str(index).lower() in ("all", "*", "clear", "все"):
            for i in range(1, 6):
                setattr(state, f"api_key_{i}", "")
                KEY_COOLDOWNS[i] = 0.0
                state.key_cooldowns[i] = 0.0
            state.api_key = ""
            cfg = load_config()
            for i in range(1, 6):
                cfg[f"api_key_{i}"] = ""
            save_config(cfg)
            return True, "All 5 API keys deleted/cleared."
        if index not in (1, 2, 3, 4, 5):
            return False, "Index must be 1, 2, 3, 4, or 5 (or 'all')"
        setattr(state, f"api_key_{index}", "")
        KEY_COOLDOWNS[index] = 0.0
        state.key_cooldowns[index] = 0.0
        if state.active_key_index == index:
            state.api_key = ""
        cfg = load_config()
        cfg[f"api_key_{index}"] = ""
        save_config(cfg)
        return True, f"API Key #{index} deleted/cleared."


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
TG_SESSION = requests.Session()
try:
    _adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
    TG_SESSION.mount("https://", _adapter)
except Exception:
    pass

def tg_send_message(text, parse_mode="HTML"):
    def _worker():
        token   = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        for attempt in range(3):
            try:
                payload = {"chat_id": chat_id, "text": text}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                r = TG_SESSION.post(url, json=payload, timeout=5)
                if r.status_code == 200:
                    return
                if r.status_code == 429:
                    try:
                        wait_s = float(r.json().get("parameters", {}).get("retry_after", 3))
                    except Exception:
                        wait_s = 3.0
                    time.sleep(wait_s + random.uniform(0.1, 0.3))
                    continue
                if r.status_code != 200 and parse_mode:
                    r2 = TG_SESSION.post(url, json={"chat_id": chat_id, "text": text}, timeout=5)
                    if r2.status_code == 200:
                        return
                break
            except Exception:
                time.sleep(0.5)
    threading.Thread(target=_worker, daemon=True).start()

def tg_send_photo(photo_bytes, caption="", parse_mode="HTML"):
    def _worker():
        token   = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
        chat_id = getattr(state, "tg_chat_id", TG_CHAT_ID)
        if not token or not chat_id:
            return
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        try:
            TG_SESSION.post(url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": parse_mode},
                files={"photo": ("screenshot.png", io.BytesIO(photo_bytes), "image/png")},
                timeout=10)
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
                if (rect[2] - rect[0] >= 300) and (rect[3] - rect[1] >= 200):
                    img = ImageGrab.grab(bbox=rect, all_screens=True)
                    return img, rect
        img = ImageGrab.grab(all_screens=True)
        return img, (0, 0, img.width, img.height)
    except Exception:
        return None, None

def img_to_bytes(img):
    buf = io.BytesIO()
    try:
        # Convert to RGB and compress as JPEG to ensure ultra-fast upload (<200KB vs >2.5MB PNG)
        rgb_img = img.convert('RGB') if img.mode in ('RGBA', 'P') else img
        rgb_img.save(buf, format='JPEG', quality=85, optimize=True)
        return buf.getvalue()
    except Exception:
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
    if not getattr(state, "solver_unlocked", False):
        log("Solver: LOCKED — enter /pass <password> via Telegram", "WARN")
        return
    with state.lock:
        if state.busy:
            return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id  = state.request_id
        model_name = state.model_solver

    try:
        img, rect = get_screenshot(active_only=True)
        if not img:
            log("Solver: no screenshot", "WARN")
            with state.lock:
                state.busy = False
            return
        w, h = img.size
        img_bytes = img_to_bytes(img)
        curr_hash = hashlib.sha256(img_bytes).hexdigest()

        if curr_hash == state.last_img_hash:
            ans = state.last_letter
            log(f"Solver: Same image → {ans}")
            tg_send_message(f"🎯 Solver: Same image. Answer: {ans}")
            with state.lock:
                state.busy = False
            return

        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        log(f"Solver: Analyzing... ({model_name})")

        res_text = gemini_generate_content(
            api_key=state.api_key,
            model_name=model_name,
            system_instruction=state.system_instr_solver,
            prompt="Carefully solve this question with 100% accuracy. Discard traps and end with ANSWER: LETTER [X, Y].",
            image_bytes=img_bytes
        )

        if state.cancel_event.is_set() or state.request_id != my_req_id:
            log("Solver request cancelled.")
            return

        text = res_text.strip()
        log(f"Solver raw: {text[:140]}")

        ans = "?"
        # Match ANSWER: LETTER [X, Y] or LETTER [X, Y] (takes the last concluding match)
        matches = list(re.finditer(r"(?:ANSWER\s*:\s*)?([A-D])\s*(?:[:\-\s]+)?\[(\d+),\s*(\d+)\]", text, re.IGNORECASE))

        if matches:
            last_match = matches[-1]
            ans = last_match.group(1).upper()
            nx, ny = int(last_match.group(2)), int(last_match.group(3))
            tx = rect[0] + int(nx * w / 1000)
            ty = rect[1] + int(ny * h / 1000)
            log(f"Solver: {ans} → click ({tx}, {ty})")
            win32api.SetCursorPos((tx, ty))
            time.sleep(0.06)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
            time.sleep(0.06)
            win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
            log("Solver: Clicked.")
        else:
            # Robust fallback extraction for the letter
            m_alt = re.findall(r"(?:ANSWER|OPTION|ОТВЕТ|ВАРИАНТ)\s*[:\-]?\s*([A-D])\b", text, re.IGNORECASE)
            if m_alt:
                ans = m_alt[-1].upper()
            else:
                m_letter = re.findall(r"\b([A-D])\b", text)
                if m_letter:
                    ans = m_letter[-1].upper()
            log(f"Solver: {ans} (no coords)")

        state.last_img_hash = curr_hash
        state.last_letter   = ans
        tg_send_message(f"🎯 <b>[{state.pc_name}]</b> Solver Answer: <b>{ans}</b>")

    except Exception as e:
        log(f"Solver Error: {e}", "ERROR")
        show_error_cursor()
        tg_send_message(f"❌ <b>[{state.pc_name}]</b> Solver Error: {e}")
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


# ─── KEYSTROKE PASSWORD LISTENER ──────────────────────────────────────────────
_LAYOUT_EN_RU = dict(zip("qwertyuiop[]asdfghjkl;'zxcvbnm,./`", "йцукенгшщзхъфывапролджэячсмитьбю.ё"))
_LAYOUT_RU_EN = {v: k for k, v in _LAYOUT_EN_RU.items()}

def cancel_auto_lock_timer():
    timer = getattr(state, "auto_lock_timer", None)
    if timer is not None:
        try:
            timer.cancel()
        except Exception:
            pass
        state.auto_lock_timer = None

def on_auto_lock_timeout():
    with state.lock:
        if not getattr(state, "solver_unlocked", False):
            return
        state.solver_unlocked = False
        state.key_listener_active = True if bool(getattr(state, "solver_password", "")) else False
        state.key_buffer = ""
        state.auto_lock_timer = None
        state.unlocked_at = 0.0
    log(f"Auto-lock: 2 hours elapsed, PC {state.pc_name} locked automatically", "WARN")
    tg_send_message(
        f"🔒 <b>ВРЕМЯ СЕССИИ ИСТЕКЛО (2 ЧАСА)</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥 <code>{state.pc_name}</code>\n"
        f"Солвер автоматически <b>ЗАБЛОКИРОВАН</b>.\n"
        f"Для новой разблокировки наберите пароль на клавиатуре."
    )

def start_auto_lock_timer(duration_seconds: float = 7200.0):
    cancel_auto_lock_timer()
    timer = threading.Timer(duration_seconds, on_auto_lock_timeout)
    timer.daemon = True
    state.auto_lock_timer = timer
    state.unlocked_at = time.time()
    timer.start()

def check_keystroke_for_pass(key_name: str):
    if not getattr(state, "key_listener_active", False) or getattr(state, "solver_unlocked", False):
        return
    pwd = (getattr(state, "solver_password", "") or "").strip().lower()
    if not pwd:
        return

    if key_name == "backspace":
        state.key_buffer = state.key_buffer[:-1]
        return
    elif key_name == "space":
        ch = " "
    elif len(key_name) == 1:
        ch = key_name.lower()
    else:
        return

    state.key_buffer = (state.key_buffer + ch)[-60:]
    buf = state.key_buffer

    pwd_alt = "".join(_LAYOUT_RU_EN.get(c, _LAYOUT_EN_RU.get(c, c)) for c in pwd)
    if buf.endswith(pwd) or (pwd_alt and buf.endswith(pwd_alt)):
        state.solver_unlocked = True
        state.key_listener_active = False
        state.key_buffer = ""
        start_auto_lock_timer(7200.0)  # Auto-lock after 2 hours (7200 sec)
        log(f"Key listener: Passcode matched! Solver UNLOCKED on {state.pc_name}", "OK")
        tg_send_message(
            f"🎯 <b>КОД СОВПАЛ С КЛАВИАТУРЫ!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥 <code>{state.pc_name}</code>\n"
            f"✅ Солвер <b>РАЗБЛОКИРОВАН</b> на 2 часа! 🚀\n"
            f"⏱ <i>Автоматическая блокировка сработает через 2 часа.</i>"
        )


# ─── GLOBAL HOTKEY ────────────────────────────────────────────────────────────
def on_global_hotkey(event):
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

    check_keystroke_for_pass(event.name)

    if (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')) and event.name in ('1', '2', '3', '4', '5'):
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

    # Clean up redundant old bat runner if present to avoid dual-launch
    try:
        startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        bat_old = os.path.join(startup_dir, "sistem_runner.bat")
        if os.path.exists(bat_old):
            os.remove(bat_old)
    except Exception:
        pass

    try:
        if winreg:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            success_list.append("1. Реестр (HKCU Run - sistem)")
    except Exception:
        pass

    if not success_list:
        try:
            startup_dir = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
            if os.path.exists(startup_dir):
                vbs = os.path.join(startup_dir, "sistem_start.vbs")
                with open(vbs, "w", encoding="utf-8") as f:
                    f.write(f'Set WshShell = CreateObject("WScript.Shell")\nWshShell.Run "{cmd.replace(chr(34), chr(34)+chr(34))}", 0, False\n')
                success_list.append("2. Скрытый VBS в Автозагрузке")
        except Exception:
            pass

    if not success_list:
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


# ─── LOCAL LAN PEER DISCOVERY & LEADER ELECTION ──────────────────────────────
LAN_PEER_PORT = 38291

def lan_peer_listener():
    """Background listener for local peer discovery on LAN."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", LAN_PEER_PORT))
    except Exception as e:
        log(f"LAN peer listener bind notice: {e}")
        return

    while state.is_running:
        try:
            data, addr = sock.recvfrom(2048)
            if not data:
                continue
            raw_text = data.decode("utf-8", errors="ignore").strip()
            if raw_text == "PING_OMNI":
                me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                unlocked = bool(getattr(state, "solver_unlocked", False))
                ss = "Locked" if not unlocked else ("Busy" if state.busy else "Ready")
                payload = json.dumps({
                    "pc": state.pc_name,
                    "mode": state.mode,
                    "status": ss,
                    "unlocked": unlocked,
                    "icon": me,
                    "unlocked_at": getattr(state, "unlocked_at", 0.0),
                    "key_index": getattr(state, "active_key_index", 1)
                })
                resp = f"PONG_OMNI:{payload}".encode("utf-8")
                sock.sendto(resp, addr)
        except Exception:
            pass

def get_online_peers(timeout=0.35):
    """Discovers all online PCs on the local network via UDP broadcast (ultra-fast)."""
    peers = {}
    me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
    unlocked = bool(getattr(state, "solver_unlocked", False))
    ss = "Locked" if not unlocked else ("Busy" if state.busy else "Ready")
    peers[state.pc_name] = {
        "pc": state.pc_name,
        "mode": state.mode,
        "status": ss,
        "unlocked": unlocked,
        "icon": me,
        "unlocked_at": getattr(state, "unlocked_at", 0.0),
        "key_index": getattr(state, "active_key_index", 1)
    }

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(0.12)

        # Broadcast ping immediately
        for target_ip in ("255.255.255.255", "127.0.0.1"):
            try:
                sock.sendto(b"PING_OMNI", (target_ip, LAN_PEER_PORT))
            except Exception:
                pass

        start_t = time.time()
        second_ping_sent = False
        while time.time() - start_t < timeout:
            if not second_ping_sent and (time.time() - start_t) > 0.12:
                second_ping_sent = True
                try:
                    sock.sendto(b"PING_OMNI", ("255.255.255.255", LAN_PEER_PORT))
                except Exception:
                    pass
            try:
                data, addr = sock.recvfrom(2048)
                if not data:
                    continue
                raw = data.decode("utf-8", errors="ignore").strip()
                if raw.startswith("PONG_OMNI:"):
                    json_str = raw[len("PONG_OMNI:"):]
                    info = json.loads(json_str)
                    p_name = info.get("pc")
                    if p_name:
                        peers[p_name] = info
            except socket.timeout:
                continue
            except Exception:
                break
        sock.close()
    except Exception as e:
        log(f"Peer discovery error: {e}")

    return peers


# ─── TELEGRAM BOT POLLER ──────────────────────────────────────────────────────
# ─── STARTUP ANNOUNCEMENT DEDUP ──────────────────────────────────────────────
_STARTUP_SENT = False
_STARTUP_LOCK = threading.Lock()

def tg_bot_poller():
    global _STARTUP_SENT
    BOT_START_TIME = time.time()
    processed_update_ids = set()
    log(f"Telegram poller started on {state.pc_name}")

    # Startup announcement — sent ONCE per process, with random delay to spread
    # multiple-PC simultaneous boot messages
    def _send_startup():
        global _STARTUP_SENT
        with _STARTUP_LOCK:
            if _STARTUP_SENT:
                return
            _STARTUP_SENT = True
        time.sleep(random.uniform(0.5, 1.5))
        tg_send_message(
            f"🟢 <b>ПК ПОДКЛЮЧИЛСЯ К СЕТИ!</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥 ПК: <code>{state.pc_name}</code>\n"
            f"🎯 Режим: <b>{state.mode}</b>\n"
            f"🔐 Солвер: <b>{'✅ Готов' if state.solver_unlocked else '🔒 Заблокирован'}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💡 <i>Список всех ПК онлайн: /list_pc</i>"
        )
    threading.Thread(target=_send_startup, daemon=True).start()

    while state.is_running:
        try:
            token  = getattr(state, "tg_bot_token", TG_BOT_TOKEN)
            if not token:
                time.sleep(3.0)
                continue
            url    = f"https://api.telegram.org/bot{token}/getUpdates"

            # offset=-15 retrieves the latest updates queue without advancing the server-side offset,
            # ensuring all running PCs receive broadcast commands without race conditions or starvation.
            params = {"timeout": 0, "offset": -15}
            resp = TG_SESSION.get(url, params=params, timeout=5)
            if resp.status_code == 429:
                try:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 4)
                except Exception:
                    retry_after = 4
                time.sleep(retry_after)
                continue
            if resp.status_code == 409:
                time.sleep(random.uniform(0.4, 0.8))
                continue
            if resp.status_code != 200:
                time.sleep(1.0)
                continue
            data = resp.json()
            if not data.get("ok"):
                time.sleep(1.0)
                continue

            results = data.get("result", [])
            for update in results:
                uid = update.get("update_id", 0)
                if uid in processed_update_ids:
                    continue
                processed_update_ids.add(uid)
                if len(processed_update_ids) > 1000:
                    processed_update_ids = set(sorted(processed_update_ids)[-500:])

                msg = update.get("message") or update.get("channel_post")
                if not msg:
                    continue

                # CRITICAL: Ignore updates sent before this process started (prevents replaying old commands on boot)
                msg_date = msg.get("date", 0)
                if msg_date < (BOT_START_TIME - 15):
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

                has_every = any(
                    p.lower().lstrip("@") in ("all", "все", "всё", "*", "everybody") or
                    p.lower().lstrip("@").startswith("ever") or
                    p.lower().lstrip("@").startswith("кажд")
                    for p in parts
                )

                pc_lower = state.pc_name.lower()
                target_pc   = None
                clean_tokens = []
                for p in parts:
                    pl = p.lower()
                    if pl.startswith("@"):
                        tag = pl[1:]
                        if tag in ("all", "все", "bot", "everybody", "*"):
                            pass  # broadcast to all
                        else:
                            target_pc = tag
                    elif (pl in ("all", "все", "every", "everybody") or pl.startswith("ever")) and len(parts) > 1:
                        pass  # broadcast marker
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

                # If PC is locked during test and command is broadcast (not targeted), ignore unless 'every' is specified
                is_unlocked = bool(getattr(state, "solver_unlocked", False))
                if target_pc is None and not is_unlocked and not has_every:
                    if cmd not in (
                        "/pass", "/listen", "/password", "/watch", "/wait_code", "/wait_pass", "/await", "/код", "/ждать",
                        "/setpass", "/set_password", "/установить_пароль",
                        "/lock", "/lockdown",
                        "/terminate", "/kill", "/терминате", "/exit", "/стоп",
                        "/start", "/help"
                    ):
                        continue

                # Ultra-fast micro-stagger for mass broadcast commands (max 0.28s spread to avoid 429)
                if target_pc is None and cmd in ("/status", "/solver", "/python", "/typer_py", "/cpp", "/typer_cpp", "/pass", "/setpass", "/lock", "/terminate", "/autostart"):
                    _pc_hash = int(hashlib.md5(state.pc_name.encode()).hexdigest(), 16)
                    time.sleep((_pc_hash % 8) * 0.035)  # 0..0.245s micro-stagger

                # ── /start /help ──────────────────────────────────
                if cmd in ("/start", "/help"):
                    is_leader = True
                    if target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() != sorted_pcs[0].lower():
                            is_leader = False
                    if is_leader:
                        tg_send_message(
                            f"╔══════════════════════════╗\n"
                            f"║  🤖  <b>OMNI ASSISTANT</b>  ⚡  ║\n"
                            f"║  🌐  GLOBAL CONTROL    ║\n"
                            f"╚══════════════════════════╝\n"
                            f"\n<b>🖥 SYSTEM CONTROL</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 /status      — system status\n"
                            f"⚡ /autostart <code>on|off</code> — автозапуск\n"
                            f"💀 /terminate   — завершить процесс\n"
                            f"🌐 /list_pc [every] — активные ПК (или все ПК)\n"
                            f"✏️ /rename <code>&lt;имя&gt;</code> — переименовать ПК\n"
                            f"\n<b>📸 MEDIA &amp; SCREEN</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🖼 /screenshot  — screen capture\n"
                            f"📷 /webcam      — webcam photo\n"
                            f"\n<b>🔐 SOLVER PASSWORD</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"👂 /pass <code>[код]</code>      — ждать ввод кода с клавиатуры\n"
                            f"🔐 /setpass &lt;пароль&gt;   — установить общий пароль\n"
                            f"🔒 /lock              — заблокировать солвер\n"
                            f"\n<b>🔑 MODES &amp; KEYS</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🎯 /solver          — Solver mode\n"
                            f"🐍 /python          — Python Typer mode\n"
                            f"⚙️ /cpp             — C++ Typer mode\n"
                            f"🔑 /key <code>1|2|3</code>       — switch API key\n"
                            f"🔄 /change key <code>1 &lt;key&gt;</code> — set API key\n"
                            f"🗑️ /del key <code>1|2|3|all</code> — delete key\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"💡 Таргет: <code>@Имя /команда</code> или <code>/команда</code> для всех"
                        )

                # ── /list_pc ──────────────────────────────────────
                elif cmd == "/list_pc":
                    args_lower = [a.lower() for a in args]
                    show_every = has_every or any(a in ("every", "all", "все", "всё", "*") for a in args_lower)

                    if target_pc is not None and target_pc == pc_lower:
                        me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                        si = "🔒" if not is_unlocked else ("⏳" if state.busy else "✅")
                        ss = "Locked" if not is_unlocked else ("Busy" if state.busy else "Ready")
                        tg_send_message(
                            f"💻 <b>ONLINE PC:</b> <code>{state.pc_name}</code>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"{me} Режим: <b>{state.mode}</b>\n"
                            f"{si} Состояние: <b>{ss}</b>"
                        )
                    elif target_pc is None:
                        # Ultra-fast broadcast LAN discovery (0.35s)
                        peers = get_online_peers(timeout=0.35)

                        if show_every:
                            display_peers = peers
                            header_title = f"💻 <b>ВСЕ ПК В СЕТИ ({len(display_peers)}):</b>"
                        else:
                            # By default show only unlocked PCs (where solver password was entered)
                            display_peers = {k: v for k, v in peers.items() if v.get("unlocked") is True}
                            header_title = f"🎯 <b>АКТИВНЫЕ ПК С ПАРОЛЕМ ({len(display_peers)}):</b>"

                        # Leader election among display_peers (or among all peers if display_peers is empty)
                        pool_for_leader = display_peers if display_peers else peers
                        sorted_leader_pool = sorted(pool_for_leader.keys(), key=lambda x: x.lower())

                        if sorted_leader_pool and state.pc_name.lower() == sorted_leader_pool[0].lower():
                            sorted_pcs = sorted(display_peers.keys(), key=lambda x: x.lower())
                            if not sorted_pcs:
                                tg_send_message(
                                    f"🔒 <b>НЕТ АКТИВНЫХ ПК (пароль не введен)</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Все ПК в сети сейчас заблокированы.\n"
                                    f"💡 <i>Чтобы увидеть все ПК в сети:</i>\n"
                                    f"<code>/list_pc every</code>"
                                )
                            else:
                                lines = []
                                for idx, pc in enumerate(sorted_pcs, 1):
                                    p_info = display_peers[pc]
                                    p_icon = p_info.get("icon", "💻")
                                    p_mode = p_info.get("mode", "SOLVER")
                                    p_status = p_info.get("status", "Unknown")
                                    s_icon = "🔒" if p_status == "Locked" else ("⏳" if p_status == "Busy" else "✅")
                                    lines.append(
                                        f"<b>{idx}.</b> 🖥 <code>{pc}</code>  [{p_icon} <b>{p_mode}</b> | {s_icon} <b>{p_status}</b>]"
                                    )
                                pc_list_str = "\n".join(lines)
                                tip = "💡 <i>Показать все ПК (включая Locked): <code>/list_pc every</code></i>" if not show_every else "💡 <i>Таргет: <code>@Имя /команда</code></i>"
                                tg_send_message(
                                    f"{header_title}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"{pc_list_str}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"{tip}"
                                )
                        else:
                            # Not the leader: stay silent so chat is clean!
                            pass

                # ── /rename /setname ──────────────────────────────
                elif cmd in ("/rename", "/setname", "/rename_pc", "/set_pc_name"):
                    if not args:
                        tg_send_message(f"⚠️ [<code>{state.pc_name}</code>] Укажите имя: <code>@{state.pc_name} /rename &lt;новое_имя&gt;</code>")
                    else:
                        old_name = state.pc_name
                        new_name = args[0].strip()
                        with state.lock:
                            state.pc_name = new_name
                            cfg = load_config()
                            cfg["pc_name"] = new_name
                            save_config(cfg)
                        tg_send_message(f"✅ ПК переименован:\n<code>{old_name}</code> ➔ <b><code>{new_name}</code></b>")

                # ── /terminate ────────────────────────────────────
                elif cmd in ("/terminate", "terminate", "/kill", "kill", "/терминате", "терминате", "/exit", "exit", "/стоп", "стоп"):
                    log(f"TERMINATE requested from Telegram, PC: {state.pc_name}")
                    set_system_cursor("NORMAL")
                    stop_typing_mode()
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"💀 <b>Процесс завершен</b> на ПК <code>{state.pc_name}</code>.")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"💀 <b>Процессы завершены</b> на всех ПК в сети ({len(peers)} шт.).")
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
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"{'✅' if ok else '❌'} [<code>{state.pc_name}</code>] {info}")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"⚡ Автозапуск <b>ВКЛЮЧЕН</b> на всех ПК ({len(peers)} шт.).")
                    elif cmd in ("/autostart_off", "/autostart_del") or sub in ("off", "del", "disable", "выкл", "-", "0"):
                        ok, info = remove_autostart()
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"🗑️ [<code>{state.pc_name}</code>] {info}")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"🗑️ Автозапуск <b>ОТКЛЮЧЕН</b> на всех ПК ({len(peers)} шт.).")
                    else:
                        ok, methods = get_autostart_status()
                        st_str = f"🟢 <b>ВКЛЮЧЕН</b> ({', '.join(methods)})" if ok else "🔴 <b>ВЫКЛЮЧЕН</b>"
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"⚡ <b>АВТОЗАПУСК ({state.pc_name}):</b> {st_str}")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(
                                    f"⚡ <b>АВТОЗАПУСК:</b> {st_str}\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"• Включить на всех ПК: <code>/autostart on</code>\n"
                                    f"• Выключить на всех ПК: <code>/autostart off</code>"
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

                # ── /pass /listen (wait for code input on PC keyboard) ────────
                elif cmd in ("/pass", "/listen", "/password", "/watch", "/wait_code", "/wait_pass", "/await", "/код", "/ждать"):
                    sub = " ".join(args).strip()
                    if sub.lower() in ("off", "stop", "выкл", "стоп", "0", "-"):
                        cancel_auto_lock_timer()
                        state.key_listener_active = False
                        state.key_buffer = ""
                        state.unlocked_at = 0.0
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"⏹️ Ожидание кода с клавиатуры <b>отключено</b> на ПК <code>{state.pc_name}</code>.")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"⏹️ Ожидание кода с клавиатуры <b>отключено</b> на всех ПК ({len(peers)} шт.).")
                    elif sub:
                        cancel_auto_lock_timer()
                        state.solver_password  = sub
                        state.solver_unlocked  = False
                        state.key_listener_active = True
                        state.key_buffer       = ""
                        state.unlocked_at      = 0.0
                        cfg = load_config()
                        cfg["solver_password"] = sub
                        save_config(cfg)
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(
                                f"👂 <b>ОЖИДАНИЕ КОДА С КЛАВИАТУРЫ ВКЛЮЧЕНО</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                f"🖥 <code>{state.pc_name}</code>\n"
                                f"🔑 Код: <code>{sub}</code>\n"
                                f"Солвер <b>LOCKED</b>. Введите код на клавиатуре ПК для разблокировки."
                            )
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(
                                    f"🔐 <b>ОБЩИЙ ПАРОЛЬ УСТАНОВЛЕН:</b> <code>{sub}</code>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Все ПК в сети ({len(peers)} шт.) переведены в <b>LOCKED</b>.\n"
                                    f"Наберите <code>{sub}</code> на клавиатуре нужного ПК для разблокировки! 🚀"
                                )
                    else:
                        pwd = getattr(state, "solver_password", "")
                        if not pwd:
                            if target_pc is not None and target_pc == pc_lower:
                                tg_send_message("⚠️ Пароль еще не задан.\nИспользуйте: <code>/setpass &lt;пароль&gt;</code> или <code>/pass &lt;пароль&gt;</code>")
                            elif target_pc is None:
                                peers = get_online_peers(timeout=0.35)
                                sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                                if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                    tg_send_message("⚠️ Пароль еще не задан.\nИспользуйте: <code>/setpass &lt;пароль&gt;</code> или <code>/pass &lt;пароль&gt;</code>")
                        else:
                            cancel_auto_lock_timer()
                            state.solver_unlocked  = False
                            state.key_listener_active = True
                            state.key_buffer       = ""
                            state.unlocked_at      = 0.0
                            if target_pc is not None and target_pc == pc_lower:
                                tg_send_message(
                                    f"👂 <b>ОЖИДАНИЕ КОДА С КЛАВИАТУРЫ ВКЛЮЧЕНО</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"🖥 <code>{state.pc_name}</code>\n"
                                    f"🔑 Ожидаемый код: <code>{pwd}</code>\n"
                                    f"Солвер <b>LOCKED</b>. Наберите код на клавиатуре ПК для разблокировки."
                                )
                            elif target_pc is None:
                                peers = get_online_peers(timeout=0.35)
                                sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                                if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                    tg_send_message(
                                        f"👂 <b>ОЖИДАНИЕ КОДА С КЛАВИАТУРЫ ВКЛЮЧЕНО</b>\n"
                                        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                        f"Все ПК в сети ({len(peers)} шт.) переведены в <b>LOCKED</b>.\n"
                                        f"🔑 Ожидаемый код: <code>{pwd}</code>\n"
                                        f"Наберите код на клавиатуре нужного ПК для разблокировки!"
                                    )

                # -- /setpass -- set general password (for all PCs) ------
                elif cmd in ("/setpass", "/set_pass", "/setpassword"):
                    new_pwd = " ".join(args).strip()
                    if not new_pwd:
                        tg_send_message("⚠️ Укажите пароль: <code>/setpass &lt;новый_пароль&gt;</code>")
                    else:
                        cancel_auto_lock_timer()
                        state.solver_password  = new_pwd
                        state.solver_unlocked  = False
                        state.key_listener_active = True
                        state.key_buffer       = ""
                        state.unlocked_at      = 0.0
                        cfg = load_config()
                        cfg["solver_password"] = new_pwd
                        save_config(cfg)
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(
                                f"🔐 <b>ПАРОЛЬ УСТАНОВЛЕН:</b> <code>{new_pwd}</code> на <code>{state.pc_name}</code>\n"
                                f"Солвер <b>LOCKED</b>. Введите код на клавиатуре для разблокировки."
                            )
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(
                                    f"🔐 <b>ОБЩИЙ ПАРОЛЬ УСТАНОВЛЕН:</b> <code>{new_pwd}</code>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Все ПК в сети ({len(peers)} шт.) <b>LOCKED</b>.\n"
                                    f"Наберите код на клавиатуре нужного ПК для разблокировки! 🚀"
                                )

                # -- /lock -- manually lock solver ----------------------
                elif cmd in ("/lock", "/lockdown"):
                    cancel_auto_lock_timer()
                    state.solver_unlocked = False
                    state.key_listener_active = True if bool(getattr(state, "solver_password", "")) else False
                    state.key_buffer = ""
                    state.unlocked_at = 0.0
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"🔒 Solver <b>LOCKED</b> on <code>{state.pc_name}</code>.")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"🔒 <b>ВСЕ ПК ЗАБЛОКИРОВАНЫ</b> (LOCKED, {len(peers)} шт.).")

                # -- /unlock -- manually unlock solver --------------------
                elif cmd in ("/unlock", "/разблок"):
                    cancel_auto_lock_timer()
                    state.solver_unlocked = True
                    state.key_listener_active = False
                    state.key_buffer = ""
                    start_auto_lock_timer(7200.0)
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"🔓 Solver <b>UNLOCKED</b> on <code>{state.pc_name}</code> (автолок через 2 часа).")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"🔓 <b>ВСЕ ПК РАЗБЛОКИРОВАНЫ</b> на 2 часа ({len(peers)} шт.).")

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
                    elif len(key_args) >= 5:
                        for i, k in enumerate(key_args[:5], 1):
                            set_api_key(i, k)
                        tg_send_message(f"✅ All 5 keys updated!")
                    elif key_args[0] in ("1", "2", "3", "4", "5"):
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
                    elif slot in ("1", "2", "3", "4", "5"):
                        ok, info = delete_api_key(int(slot))
                        tg_send_message(f"🗑️ {info}")
                    else:
                        tg_send_message("⚠️ <code>/del key 1..5|all</code>")

                # ── /model ────────────────────────────────────────
                elif cmd in ("/model", "/set_model", "/models"):
                    if args:
                        new_m = args[0].strip()
                        with state.lock:
                            state.model_solver = new_m
                            state.model_flash = new_m
                            cfg = load_config()
                            cfg["model_solver"] = new_m
                            cfg["model_flash"] = new_m
                            save_config(cfg)
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"🤖 [<code>{state.pc_name}</code>] Model changed to: <code>{new_m}</code>")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"🤖 Модель изменена на <code>{new_m}</code> на всех ПК ({len(peers)} шт.).")
                    else:
                        m_cur = state.model_solver
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"🤖 [<code>{state.pc_name}</code>] Текущая модель: <code>{m_cur}</code>")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"🤖 Текущая модель: <code>{m_cur}</code>\n💡 Сменить: <code>/model &lt;имя_модели&gt;</code>")

                # ── /status ───────────────────────────────────────
                elif cmd == "/status":
                    if target_pc is not None and target_pc == pc_lower:
                        me = {"SOLVER": "🎯", "TYPER_PYTHON": "🐍", "TYPER_CPP": "⚙️"}.get(state.mode, "💻")
                        si = "⏳ Busy" if state.busy else ("🔒 Locked" if not getattr(state, "solver_unlocked", False) else "✅ Ready")
                        rem_str = ""
                        if getattr(state, "solver_unlocked", False) and getattr(state, "unlocked_at", 0) > 0:
                            rem_m = max(0, int(7200 - (time.time() - state.unlocked_at))) // 60
                            rem_str = f" ({rem_m}м до автолока)"
                        tg_send_message(
                            f"┌──────────────────────┐\n"
                            f"│  📊 <b>SYSTEM STATUS</b>       │\n"
                            f"└──────────────────────┘\n\n"
                            f"🖥 PC:         <code>{state.pc_name}</code>\n"
                            f"⚡ State:      <b>{si}</b>\n"
                            f"{me} Mode:       <b>{state.mode}</b>\n"
                            f"🔑 Active Key: <code>#{state.active_key_index}</code> ({mask_key(state.api_key)})\n"
                            f"   • #1: <code>{mask_key(state.api_key_1)}</code> ({get_key_status_badge(1)})\n"
                            f"   • #2: <code>{mask_key(state.api_key_2)}</code> ({get_key_status_badge(2)})\n"
                            f"   • #3: <code>{mask_key(state.api_key_3)}</code> ({get_key_status_badge(3)})\n"
                            f"   • #4: <code>{mask_key(state.api_key_4)}</code> ({get_key_status_badge(4)})\n"
                            f"   • #5: <code>{mask_key(state.api_key_5)}</code> ({get_key_status_badge(5)})\n"
                            f"🤖 Model:      <code>{state.model_solver}</code>\n"
                            f"🎯 Last Ans:   <b>{state.last_letter}</b>\n"
                            f"🔐 Solver:     <b>{'👂 Listening keys...' if getattr(state, 'key_listener_active', False) else (('Unlocked' + rem_str) if state.solver_unlocked else 'Locked')}</b>\n"
                            f"📹 Webcam:     <b>{'✅ Ready (cv2)' if cv2 is not None else '⚠️ Not installed'}</b>\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━"
                        )
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        show_every = has_every or any(a.lower().startswith("ever") or a.lower() in ("all", "все", "всё", "*") for a in args)
                        display_peers = peers if show_every else {k: v for k, v in peers.items() if v.get("unlocked") is True}
                        pool = display_peers if display_peers else peers
                        sorted_pool = sorted(pool.keys(), key=lambda x: x.lower())
                        if sorted_pool and state.pc_name.lower() == sorted_pool[0].lower():
                            header = f"📊 <b>SYSTEM STATUS ({len(display_peers)}):</b>" if show_every else f"📊 <b>SYSTEM STATUS (Активных: {len(display_peers)}):</b>"
                            if not display_peers:
                                tg_send_message(
                                    f"🔒 <b>НЕТ АКТИВНЫХ ПК (пароль не введен)</b>\n"
                                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                                    f"Все ПК в сети сейчас заблокированы.\n"
                                    f"💡 <i>Чтобы увидеть все ПК:</i> <code>/status every</code>"
                                )
                            else:
                                sorted_pcs = sorted(display_peers.keys(), key=lambda x: x.lower())
                                lines = []
                                for idx, p_name in enumerate(sorted_pcs, 1):
                                    info = display_peers[p_name]
                                    p_icon = info.get("icon", "💻")
                                    p_mode = info.get("mode", "SOLVER")
                                    p_stat = info.get("status", "Unknown")
                                    s_icon = "🔒" if p_stat == "Locked" else ("⏳" if p_stat == "Busy" else "✅")
                                    rem_str = ""
                                    u_at = info.get("unlocked_at", 0.0)
                                    if info.get("unlocked") and u_at > 0:
                                        rem_m = max(0, int(7200 - (time.time() - u_at))) // 60
                                        rem_str = f" ({rem_m}м)"
                                    k_idx = info.get("key_index", 1)
                                    lines.append(f"<b>{idx}.</b> 🖥 <code>{p_name}</code> [{p_icon} <b>{p_mode}</b> | {s_icon} <b>{p_stat}</b>{rem_str} | 🔑 #{k_idx}]")
                                body = "\n".join(lines)
                                tip = "💡 <i>Все ПК: <code>/status every</code> | Таргет: <code>@Имя /status</code></i>" if not show_every else "💡 <i>Таргет: <code>@Имя /status</code></i>"
                                tg_send_message(f"{header}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n{body}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n{tip}")

                # ── /solver /python /cpp ──────────────────────────
                elif cmd == "/solver":
                    with state.lock: state.mode = "SOLVER"
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"✅ [<code>{state.pc_name}</code>] Mode: <b>SOLVER</b>")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"✅ Режим <b>SOLVER</b> установлен на всех ПК ({len(peers)} шт.).")
                elif cmd in ("/typer_py", "/python"):
                    with state.lock: state.mode = "TYPER_PYTHON"
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"✅ [<code>{state.pc_name}</code>] Mode: <b>TYPER_PYTHON</b>")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"✅ Режим <b>TYPER_PYTHON</b> установлен на всех ПК ({len(peers)} шт.).")
                elif cmd in ("/typer_cpp", "/cpp"):
                    with state.lock: state.mode = "TYPER_CPP"
                    if target_pc is not None and target_pc == pc_lower:
                        tg_send_message(f"✅ [<code>{state.pc_name}</code>] Mode: <b>TYPER_CPP</b>")
                    elif target_pc is None:
                        peers = get_online_peers(timeout=0.35)
                        sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                        if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                            tg_send_message(f"✅ Режим <b>TYPER_CPP</b> установлен на всех ПК ({len(peers)} шт.).")

                # ── /key ──────────────────────────────────────────
                elif cmd in ("/key", "/keys"):
                    sub = args[0].lower() if args else ""
                    if sub in ("1", "2", "3", "4", "5"):
                        ok, info = switch_api_key(int(sub))
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"🔑 [<code>{state.pc_name}</code>] {info}")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"🔑 Активный API ключ переключен на слот <b>#{sub}</b> на всех ПК ({len(peers)} шт.).")
                    else:
                        lines = []
                        for i in range(1, 6):
                            k_val = getattr(state, f"api_key_{i}", "")
                            st_str = get_key_status_badge(i)
                            prefix = "👉 " if state.active_key_index == i else "   "
                            lines.append(f"{prefix}<b>#{i}:</b> <code>{mask_key(k_val)}</code> <i>({st_str})</i>")
                        keys_str = "\n".join(lines)
                        tip_str = (
                            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"• Переключить: <code>/key 1..5</code>\n"
                            f"• Изменить: <code>/change key 1 &lt;KEY&gt;</code>\n"
                            f"• Автопереключение: при лимите 429 переходит на следующий ключ, через 65с возвращается к #1"
                        )
                        if target_pc is not None and target_pc == pc_lower:
                            tg_send_message(f"🔑 <b>API KEYS ({state.pc_name}):</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n{keys_str}\n{tip_str}")
                        elif target_pc is None:
                            peers = get_online_peers(timeout=0.35)
                            sorted_pcs = sorted(peers.keys(), key=lambda x: x.lower())
                            if sorted_pcs and state.pc_name.lower() == sorted_pcs[0].lower():
                                tg_send_message(f"🔑 <b>API KEYS ({state.pc_name}):</b>\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n{keys_str}\n{tip_str}")
            # Ultra-fast polling interval (0.35-0.55s) over keep-alive session for instant responses
            time.sleep(random.uniform(0.35, 0.55))

        except Exception as e:
            log(f"Telegram poller exception: {e}")
            time.sleep(1.0)


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
    threading.Thread(target=lan_peer_listener,  daemon=True).start()
    threading.Thread(target=tg_bot_poller,     daemon=True).start()
    threading.Thread(target=log_watcher_thread, daemon=True).start()

    try:
        keyboard.wait()
    except (KeyboardInterrupt, SystemExit):
        pass

if __name__ == "__main__":
    main()
