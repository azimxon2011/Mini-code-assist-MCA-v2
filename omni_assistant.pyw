# -*- coding: utf-8 -*-
import sys
import os
import io
import time
import base64
import threading
import hashlib
import re 
import json
import argparse
import subprocess

# ----- BOOTSTRAP VENV -----
REQUIRED_PACKAGES = ["google-generativeai", "pillow", "keyboard", "pyperclip", "mss"]
VENV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".omni_venv")

def bootstrap_venv():
    # Check if we are already inside our virtual environment
    if sys.prefix == VENV_DIR:
        return

    print("[Bootstrap] Checking virtual environment...")
    if not os.path.exists(VENV_DIR):
        print(f"[Bootstrap] Creating venv at {VENV_DIR}...")
        import venv
        venv.create(VENV_DIR, with_pip=True)
    
    if sys.platform == "win32":
        venv_python = os.path.join(VENV_DIR, "Scripts", "python.exe")
        venv_pip = os.path.join(VENV_DIR, "Scripts", "pip.exe")
    else:
        venv_python = os.path.join(VENV_DIR, "bin", "python")
        venv_pip = os.path.join(VENV_DIR, "bin", "pip")

    print("[Bootstrap] Installing/Verifying dependencies...")
    subprocess.run([venv_pip, "install", "--quiet"] + REQUIRED_PACKAGES, check=True)
    
    print("[Bootstrap] Restarting script inside venv...")
    os.execv(venv_python, [venv_python, os.path.abspath(__file__)] + sys.argv[1:])

bootstrap_venv()
# --------------------------

IS_WINDOWS = sys.platform == "win32"

import keyboard
import google.generativeai as genai
from PIL import ImageGrab, Image
# (Tkinter removed)

if IS_WINDOWS:
    import ctypes
    import win32api
    import win32con
    import win32clipboard

import pyperclip
import mss
import mss.tools

# --- CONSTANTS ---
DEFAULT_CPP_TEMPLATE = (
    "#include <bits/stdc++.h>\n"
    "using namespace std;\n\n"
    "#define int long long\n"
    "#define double long double\n\n"
    "signed main() {\n"
    "  ios::sync_with_stdio(0); cin.tie(0);\n"
    "  // START\n"
    "  \n"
    "  // END\n"
    "  return 0;\n"
    "}"
)

SECOND_CPP_TEMPLATE = (
    "#include <bits/stdc++.h>\n"
    "using namespace std;\n"
    "#define int long long\n"
    "signed main(){\n"
    "// START\n"
    "  \n"
    "// END\n"
    "  return 0;\n"
    "}"
)

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
    "You are an Elite Solver. Solve the Math/Physics/English question in the image. "
    "Reason internally but Output ONLY the uppercase letter (A, B, C, or D). "
    "No text, no explanation. If there is answer marked already recheck it and output right answer."
)
#"2. Code should look like it was written by a normal human student, not a system engineer. Avoid overly complex syntax or obscure STL features unless necessary. Keep it simple and readable.\n"

#"2. Code MUST look like it was written by a normal human student. No over-engineering, NO INPUT CHECKS (no if(cin>>...)), NO ALIGNMENT with spaces.\n"
TYPER_CPP_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write VERY CLEAR, UNDERSTANDABLE C++ code.\n"
    "2. Code should look like it was written by a normal human student in contest, not a system engineer or AI. AVOID overly complex syntax or obscure STL features unless necessary. Keep it simple and readable.\n"
    "3. Use the provided template. You MUST wrap your logic with `// START` and `// END` markers. These are critical for the tool. DO NOT include any other comments in the code.\n"
    "4. Use `#define int long long` and `signed main()` as per template.\n"
    "5. Use 2-SPACE INDENTATION strictly.\n"
    "6. Short but meaningful variable names related to the problem.\n"
    "7. STUDENT HABITS: NEVER DO INPUT CHECKS. (e.g., `if (!(cin >> smth))`). NO vertical alignment with spaces. NO unnecessary intermediate variables or excessive casting. Use input variables directly in formulas.\n"
    "8. Return ONLY the raw code. No markdown, no explanations, no comments (EXCEPT the START/END markers)."
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
    "TYPER_PYTHON": (True, False, False),
    "TYPER_CPP": (False, False, True),
}

# --- STATE ---
class AssistantState:
    def __init__(self):
        self.mode = "TYPER_CPP"  # Default mode
        self.busy = False
        self.lock = threading.Lock()
        
        # Configurable Settings
        self.api_key = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
        self.model_flash = "gemini-3-flash-preview"
        self.model_pro = "gemini-3.1-pro-preview"
        self.model_solver = "gemini-3-flash-preview"
        self.system_instr_typer = TYPER_INSTRUCTION
        self.system_instr_typer_cpp = TYPER_CPP_INSTRUCTION
        self.system_instr_solver = SOLVER_INSTRUCTION
        self.cpp_templates = [DEFAULT_CPP_TEMPLATE, SECOND_CPP_TEMPLATE]
        self.cpp_template_idx = 0
        self.cpp_template = self.cpp_templates[0]
        
        # Typer Specific
        self.code_buffer = ""
        self.prefix_buffer = ""
        self.suffix_buffer = ""
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
        self.cancel_event = threading.Event()
        self.rshift_timer = None
        self.request_id = 0

state = AssistantState()

# Initialize GenAI with default key
genai.configure(api_key=state.api_key)

# --- VIRTUAL KEYS ---
VK_NUM = 0x90
VK_CAPS = 0x14
VK_SCROLL = 0x91
# --- UTILITIES ---
def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{level}] {msg}", flush=True)

def get_toggle_state(vk_code: int) -> bool:
    if not IS_WINDOWS: return False
    return (win32api.GetKeyState(vk_code) & 1) != 0

def press_key(vk_code: int):
    if not IS_WINDOWS: return
    win32api.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.01)
    win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)

def set_leds(num=None, caps=None, scroll=None):
    """Sets keyboard LEDs to requested states (None means keep current)."""
    if not IS_WINDOWS: return
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
    return MODE_INDICATOR_MAP.get(state.mode, (False, False, False))

def refresh_indicators():
    if state.loading_event.is_set():
        return
    set_leds(*get_indicator_state())

def loading_heartbeat():
    """Blinks indicators while loading_event is set."""
    while state.loading_event.is_set():
        # Быстрое мигание (знак что процесс жив)
        set_leds(True, False, True)
        time.sleep(0.5)
        set_leds(False, False, False)
        
        # Ожидание 5 секунд (разбито на мелкие шаги для быстрого выхода, если загрузка окончена)
        for _ in range(50):
            if not state.loading_event.is_set():
                break
            time.sleep(0.1)
    refresh_indicators()

def get_screenshot():
    tmp_path = "/tmp/omni_screen.png"
    
    def is_black(img):
        if img is None: return True
        # Check if image is entirely black/empty
        return not img.getbbox()

    # Method 1: Spectacle (KDE Native)
    try:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        subprocess.run(["spectacle", "-b", "-n", "-o", tmp_path], capture_output=True, timeout=5)
        if os.path.exists(tmp_path):
            img = Image.open(tmp_path)
            if not is_black(img):
                log("DEBUG: Screenshot captured via Spectacle.")
                return img
    except Exception: pass

    # Method 2: Grim (Wayland Generic)
    try:
        if os.path.exists(tmp_path): os.remove(tmp_path)
        subprocess.run(["grim", tmp_path], capture_output=True, timeout=5)
        if os.path.exists(tmp_path):
            img = Image.open(tmp_path)
            if not is_black(img):
                log("DEBUG: Screenshot captured via Grim.")
                return img
    except Exception: pass

    # Method 3: MSS (X11 / XWayland)
    try:
        with mss.MSS() as sct:
            sct_img = sct.shot(output=tmp_path)
            img = Image.open(sct_img)
            if not is_black(img):
                log("DEBUG: Screenshot captured via MSS.")
                return img
    except Exception: pass

    log("ERROR: All screenshot methods failed or returned black screen.", "ERROR")
    return None

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
        
        # Prevent massive alignment jumps that break the typer
        if target_indent > editor_indent + 4:
            target_indent = editor_indent + 2
            
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

# --- TYPER LOGIC ---
def type_one_char(ch):
    if ch == '\x1b[A': # Special marker for Arrow Up
        keyboard.press_and_release('up')
        return

    if len(ch) > 1:
        parts = re.split(r'([\n\t\x08 ])', ch)
        for p in parts:
            if not p: continue
            if p == '\n': 
                keyboard.press_and_release('enter')
                time.sleep(0.01)  # Faster but still safe
            elif p == '\t': keyboard.press_and_release('tab')
            elif p == '\x08': keyboard.press_and_release('backspace')
            elif p == ' ': keyboard.press_and_release('space')
            else: keyboard.write(p)
    else:
        if ch == '\n': keyboard.press_and_release('enter')
        elif ch == '\t': keyboard.press_and_release('tab')
        elif ch == '\x08': keyboard.press_and_release('backspace')
        elif ch == ' ': keyboard.press_and_release('space')
        else: keyboard.write(ch)

def clipboard_paste(text):
    """Pastes text via clipboard, preserving original clipboard content."""
    clean_text = text.replace('\x08', '')
    
    if IS_WINDOWS:
        old_data = None
        try:
            win32clipboard.OpenClipboard()
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                old_data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
        except Exception: pass

        try:
            win32clipboard.OpenClipboard()
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(clean_text, win32clipboard.CF_UNICODETEXT)
            win32clipboard.CloseClipboard()
            
            keyboard.press('ctrl')
            keyboard.press('v')
            time.sleep(0.01)
            keyboard.release('v')
            keyboard.release('ctrl')
            time.sleep(0.1) # Wait for editor to process
        except Exception as e:
            log(f"Clipboard paste error: {e}", "WARN")
            keyboard.write(text)

        if old_data is not None:
            try:
                time.sleep(0.05)
                win32clipboard.OpenClipboard()
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(old_data, win32clipboard.CF_UNICODETEXT)
                win32clipboard.CloseClipboard()
            except Exception: pass
    else:
        # Linux Paste
        try:
            success = False
            if os.environ.get("WAYLAND_DISPLAY"):
                subprocess.run(["wl-copy"], input=clean_text.encode('utf-8'), check=True)
                try:
                    subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=True)
                    success = True
                except Exception: pass
            else:
                subprocess.run(["xclip", "-selection", "clipboard"], input=clean_text.encode('utf-8'), check=True)
                try:
                    subprocess.run(["xdotool", "key", "ctrl+v"], check=True)
                    success = True
                except Exception: pass
            
            if not success:
                log("!!! AUTO-PASTE FAILED: Please press Ctrl+V manually !!!", "IMPORTANT")
            else:
                time.sleep(0.1)
        except Exception as e:
            log(f"Linux clipboard paste error: {e}", "WARN")
            log("!!! PLEASE PRESS Ctrl+V MANUALLY !!!", "IMPORTANT")

import queue
typing_queue = queue.Queue()

def typing_worker():
    while True:
        ch = typing_queue.get()
        if ch is None: break
        if isinstance(ch, tuple) and ch[0] == 'PASTE':
            clipboard_paste(ch[1])
        else:
            type_one_char(ch)
        typing_queue.task_done()

threading.Thread(target=typing_worker, daemon=True).start()

def on_typing_key(event):
    is_down = event.event_type == keyboard.KEY_DOWN
    
    # Ignore modifier and lock keys to prevent ghost triggers
    if event.name in ('shift', 'ctrl', 'alt', 'left shift', 'right shift', 
                     'left ctrl', 'right ctrl', 'left alt', 'right alt',
                     'num lock', 'scroll lock', 'caps lock', 'f8', 'f9', 'tab',
                     'up', 'down', 'left', 'right', 'home', 'end', 'page up', 'page down'):
        
        # Still need to handle mode switch (F9) and edit mode (F8)
        if event.name == 'f8' and is_down:
            state.edit_mode = not state.edit_mode
            log(f"Edit Mode: {'ON' if state.edit_mode else 'OFF'}")
            return False
        if event.name == 'f9' and is_down:
            stop_typing_mode("Mode switch requested.")
            toggle_mode()
            return False
        return True # Let the system handle these keys
    
    if state.edit_mode: return True

# Settings menu removed (no tkinter)

    if event.name == 'esc':
        if is_down and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')):
            stop_typing_mode("Typing cancelled.")
            return False
        return True

    if event.name == 'backspace':
        if is_down:
            with state.lock:
                if state.code_index > 0: state.code_index -= 1
            typing_queue.put('\x08')
        return False
    
    # Typer Trigger
    if is_down:
        with state.lock:
            if not state.typing_active: return False
            
            # First keypress: Insert Full Template (Prefix + Suffix) and Move Cursor
            if state.code_index == 0 and (state.prefix_buffer or state.suffix_buffer):
                full_boilerplate = state.prefix_buffer + state.suffix_buffer
                typing_queue.put(('PASTE', full_boilerplate))
                
                # Manual navigation requested: cursor stays at the end of boilerplate
                
                state.prefix_buffer = "" 
                state.suffix_buffer = "" # Both already handled
                return False

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
    refresh_indicators()

def stop_typing_mode(reason=""):
    state.typing_active = False
    if state.typing_hook:
        keyboard.unhook(state.typing_hook)
        state.typing_hook = None
    if reason: log(reason)
    refresh_indicators()

def load_code_to_typer(clean_code, adjusted_code):
    """Parses code with markers and loads into buffers."""
    if "// START" in clean_code and "// END" in clean_code:
        # Use ORIGINAL (clean) code for the boilerplate (prefix/suffix)
        # because clipboard paste doesn't trigger auto-indent.
        clean_parts = clean_code.split("// START")
        clean_logic_suffix = clean_parts[1].split("// END")
        state.prefix_buffer = clean_parts[0]
        state.suffix_buffer = clean_logic_suffix[1]
        
        # Use ADJUSTED code for the logic buffer
        # because it's typed character-by-character.
        adj_parts = adjusted_code.split("// START")
        adj_logic_suffix = adj_parts[1].split("// END")
        state.code_buffer = adj_logic_suffix[0]
    else:
        state.prefix_buffer = ""
        state.code_buffer = adjusted_code
        state.suffix_buffer = ""
    
    state.code_index = 0
    state.typing_active = True

# --- ACTION HANDLERS ---
def handle_typer_action(model_name=None):
    with state.lock:
        if state.busy: return
        state.busy = True
        state.cancel_event.clear()
        state.request_id += 1
        my_req_id = state.request_id
        if model_name is None:
            # On Linux without root, we can't check Ctrl key globally. Default to Flash.
            if IS_WINDOWS:
                is_ctrl = keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')
                model_name = state.model_pro if is_ctrl else state.model_flash
            else:
                model_name = state.model_flash
                
        is_cpp = (state.mode == "TYPER_CPP")
        lang_str = "C++" if is_cpp else "Python"
        env_str = "GCC" if is_cpp else "PyPy"
        system_instr = state.system_instr_typer_cpp if is_cpp else state.system_instr_typer
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        log("DEBUG: Starting get_screenshot()...")
        img = get_screenshot()
        if img:
            img_data = img_to_bytes(img)
            log(f"Typing ({lang_str}): Capture taken. Asking Gemini ({model_name})...")
            
            prompt = f"Solve this Robocontest problem and return the {lang_str} code. (Environment: {env_str})"
            if is_cpp:
                prompt += f"\n\nUse this template:\n{state.cpp_template}"
            
            log("DEBUG: Sending request to Gemini API...")
            gen_model = genai.GenerativeModel(model_name=model_name, system_instruction=system_instr)
            response = gen_model.generate_content([
                prompt, 
                {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(img_data).decode()}}
            ])
            log("DEBUG: Gemini response received.")
            if state.cancel_event.is_set() or state.request_id != my_req_id:
                log("Typing request cancelled. Ignoring late Gemini response.")
                return
            
            clean_code = strip_markdown(response.text)
            adjusted_code = adjust_for_autoindent(clean_code)
             
            with state.lock:
                load_code_to_typer(clean_code, adjusted_code)
            
            log(f"Ready! Logic: {len(state.code_buffer)} chars. Prefix: {len(state.prefix_buffer)} chars.")
        else:
            log("DEBUG: get_screenshot() returned None. Action aborted.", "WARN")
            
        if not IS_WINDOWS and state.typing_active:
            log("Auto-inserting code in 2 seconds... (Focus your editor!)")
            time.sleep(2)
            full_text = state.prefix_buffer + state.code_buffer + state.suffix_buffer
            clipboard_paste(full_text)
            state.typing_active = False
        else:
            start_typing_mode()
    except Exception as e:
        log(f"Typer Error: {e}", "ERROR")
    finally:
        with state.lock:
            if state.request_id == my_req_id:
                state.loading_event.clear()
                state.busy = False
                refresh_indicators()

# Solver removed as requested.

def clipboard_monitor_loop():
    log("=== CLIPBOARD MONITOR ACTIVE ===")
    log("Instruction: Copy the letter 'a' to trigger capture & solve.")
    log("Note: After trigger, you will have 2 seconds to focus your editor.")
    
    last_clip = ""
    while True:
        try:
            curr = pyperclip.paste()
            if curr != last_clip:
                # Trigger on EXACT letter 'a' (case insensitive)
                if curr.strip().lower() == "a":
                    log("!!! Trigger 'a' detected !!!")
                    # Clear clipboard immediately to prevent double trigger and clean up
                    pyperclip.copy("")
                    last_clip = ""
                    
                    # Run action in THREAD so it doesn't block the monitor
                    threading.Thread(target=handle_typer_action, daemon=True).start()
                else:
                    last_clip = curr
        except Exception as e:
            pass
        time.sleep(0.5)

def toggle_mode():
    with state.lock:
        if state.mode == "TYPER_CPP":
            state.mode = "TYPER_PYTHON"
        else:
            state.mode = "TYPER_CPP"
        log(f"MODE SWITCHED: Now in {state.mode} mode.")
    set_leds(True, True, True)
    time.sleep(0.2)
    refresh_indicators()

def toggle_cpp_template():
    with state.lock:
        state.cpp_template_idx = (state.cpp_template_idx + 1) % len(state.cpp_templates)
        state.cpp_template = state.cpp_templates[state.cpp_template_idx]
        log(f"C++ TEMPLATE SWITCHED: Now using Template #{state.cpp_template_idx + 1}")
    # Visual feedback
    set_leds(True, False, False)
    time.sleep(0.1)
    set_leds(False, True, False)
    time.sleep(0.1)
    set_leds(False, False, True)
    time.sleep(0.1)
    refresh_indicators()

def on_global_hotkey(event):
    is_down = event.event_type == keyboard.KEY_DOWN

    # Trigger (Right Shift) with 0.5-second hold
    is_rshift = (event.name in ('right shift', 'rightshift') or event.scan_code == 54)
    if is_rshift:
        if is_down:
            if state.rshift_timer is None:
                def trigger():
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

    if event.name == 'esc' and (keyboard.is_pressed('ctrl') or keyboard.is_pressed('left ctrl')) and state.loading_event.is_set():
        state.cancel_event.set()
        state.loading_event.clear()
        with state.lock:
            state.busy = False
            state.request_id += 1
        log("Cancellation requested. Ready for new action immediately.")
        refresh_indicators()
        return
    
    # Mode switch
    if event.name == 'f9':
        toggle_mode()
        return

    # Template switch
    if event.name == 'f4':
        toggle_cpp_template()
        return

# Settings Menu removed (no tkinter)

    # Instant Template Dump (F10) - Character-by-character mode
    if event.name == 'f10' and is_down:
        if state.typing_active or state.busy: return
        log("F10: Loading template into Typer (Character-by-character mode)...")
        with state.lock:
            # For F10, we remove marker lines entirely to avoid extra empty lines
            clean_template = re.sub(r'[ \t]*// (START|END)[ \t]*\n?', '', state.cpp_template)
            adjusted_template = adjust_for_autoindent(clean_template)
            state.code_buffer = adjusted_template
            state.prefix_buffer = ""
            state.suffix_buffer = ""
            state.code_index = 0
            state.typing_active = True
        start_typing_mode()
        return

def main():
    parser = argparse.ArgumentParser(description="Omni Assistant")
    parser.add_argument("--action", type=str, choices=["type-python", "type-cpp"], help="Run in stateless CLI mode")
    parser.add_argument("--daemon", action="store_true", help="Run in traditional daemon mode (Requires root on Linux)")
    args = parser.parse_args()

    if args.action:
        log(f"Running in stateless mode: {args.action}")
        if args.action == "type-python":
            state.mode = "TYPER_PYTHON"
            handle_typer_action()
        elif args.action == "type-cpp":
            state.mode = "TYPER_CPP"
            handle_typer_action()
        
        while state.busy:
            time.sleep(0.5)
            
        if not IS_WINDOWS and state.typing_active and state.code_index == 0:
            if state.prefix_buffer or state.code_buffer or state.suffix_buffer:
                full_text = state.prefix_buffer + state.code_buffer + state.suffix_buffer
                clipboard_paste(full_text)
                state.typing_active = False
        return

    if args.daemon:
        log("=== OMNI ASSISTANT DAEMON MODE ===")
        if not IS_WINDOWS:
            log("WARNING: Running daemon mode on Linux. `keyboard` hooks require sudo and may not work on Wayland.", "WARN")
        
        log("F4            : Switch C++ Template (Template 1 <-> Template 2)")
        log("F9            : Switch Mode (Typer_Python <-> Typer_CPP)")
        log("F10            : Load Template into Typer (Type it manually)")
        log("RightShift    : Capture & Execute Action (Hold for 0.5 seconds)")
        log("Default Mode  : TYPER_CPP")
        log("-------------------------------")
        refresh_indicators()
        
        if IS_WINDOWS:
            keyboard.hook(on_global_hotkey)
        else:
            try:
                keyboard.hook(on_global_hotkey)
            except Exception as e:
                log(f"Failed to hook keyboard: {e}", "ERROR")
                log("On Linux, you must run with sudo to use the daemon mode.", "ERROR")
                return
        
        try:
            keyboard.wait()
        except KeyboardInterrupt:
            log("Exiting...")
        return

    # Default: Clipboard Monitor Mode
    clipboard_monitor_loop()

if __name__ == "__main__":
    main()
