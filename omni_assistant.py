# -*- coding: utf-8 -*-
import sys
import io
import time
import threading
import hashlib
import queue
import keyboard
import ctypes
import win32api
import win32con
from PIL import ImageGrab
import tkinter as tk
from tkinter import ttk, messagebox
import re 

try:
    from google import genai as genai_sdk
    from google.genai import types as genai_types
except ImportError:
    genai_sdk = None
    genai_types = None

# --- SYSTEM PROMPTS ---
TYPER_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write efficient, READABLE Python code.\n"
    "2. Do not overengineer. But beware Runtime Errors.\n"
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

TYPER_CPP_INSTRUCTION = (
    "You are an Elite Competitive Programmer.\n"
    "Rules:\n"
    "1. Solve the problem EXACTLY. Write VERY CLEAR, UNDERSTANDABLE C++ code.\n"
    "2. Code should look like it was written by a normal human student, not a system engineer. Avoid overly complex syntax, macros, or obscure STL features unless necessary. Keep it simple and readable.\n"
    "3. Use 2-SPACE INDENTATION strictly.\n"
    "4. Include standard headers like <iostream>, <vector>, <string>, etc. using `using namespace std;`.\n"
    "5. Use `int main()` as the entry point.\n"
    "6. Short but meaningful variable names related to the problem.\n"
    "7. Return ONLY the raw code. No markdown, no explanations, no comments."
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
        self.mode = "SOLVER"  # "SOLVER", "TYPER_PYTHON" or "TYPER_CPP"
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
        self.gemini_stream_first_chunk_timeout = 20
        self.gemini_stream_chunk_timeout = 20
        self.gemini_pro_stream_first_chunk_timeout = 60
        
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

def loading_heartbeat():
    """Blinks indicators while loading_event is set."""
    while state.loading_event.is_set():
        if state.mode.startswith("TYPER"):
            set_leds(num=True)
            time.sleep(6)
            set_leds(num=False)
            time.sleep(6)
        else:
            set_leds(num=True, scroll=True)
            time.sleep(6)
            set_leds(num=False, scroll=False)
            time.sleep(6)
    set_leds(num=False, scroll=False)

def sdk_supports_thinking_stream():
    return genai_sdk is not None and genai_types is not None

def require_genai_sdk():
    if not sdk_supports_thinking_stream():
        raise RuntimeError(
            "google-genai is not installed. Install the new SDK and remove legacy google-generativeai from the build."
        )

def get_stream_timeouts(model_name):
    first_chunk_timeout = state.gemini_stream_first_chunk_timeout
    if "pro" in model_name.lower():
        first_chunk_timeout = state.gemini_pro_stream_first_chunk_timeout
    return first_chunk_timeout, state.gemini_stream_chunk_timeout

def iter_chunk_parts(chunk):
    candidates = getattr(chunk, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            yield part

def consume_stream_with_watchdog(stream_factory, first_chunk_timeout, chunk_timeout):
    events = queue.Queue()

    def worker():
        try:
            for chunk in stream_factory():
                events.put(("chunk", chunk))
            events.put(("done", None))
        except Exception as e:
            events.put(("error", e))

    threading.Thread(target=worker, daemon=True).start()

    saw_thought = False
    thought_buf = []
    answer_buf = []
    saw_any_chunk = False

    while True:
        current_timeout = chunk_timeout if saw_any_chunk else first_chunk_timeout
        try:
            event_type, payload = events.get(timeout=current_timeout)
        except queue.Empty:
            if saw_thought:
                status = "after thinking started"
            elif saw_any_chunk:
                status = "between stream chunks before final answer"
            else:
                status = "before any thinking/answer chunks"
            raise TimeoutError(f"Gemini stream stalled for {current_timeout}s {status}.")

        if event_type == "error":
            raise payload
        if event_type == "done":
            break

        chunk = payload
        saw_any_chunk = True
        chunk_used = False
        for part in iter_chunk_parts(chunk):
            text = getattr(part, "text", None)
            if not text:
                continue
            chunk_used = True
            if getattr(part, "thought", False):
                if not saw_thought:
                    log("Gemini: thinking stream started.")
                saw_thought = True
                thought_buf.append(text)
            else:
                if saw_thought and not answer_buf:
                    log("Gemini: thinking finished, receiving final answer.")
                answer_buf.append(text)

        if not chunk_used:
            text = getattr(chunk, "text", None)
            if text:
                answer_buf.append(text)

    final_text = "".join(answer_buf).strip()
    if not final_text:
        raise RuntimeError("Gemini stream ended without a final answer.")
    return final_text, saw_thought, "".join(thought_buf)

def generate_gemini_text(model_name, system_instr, prompt, image_bytes):
    require_genai_sdk()

    client = genai_sdk.Client(api_key=state.api_key)
    contents = [
        prompt,
        genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
    ]

    def stream_with_thoughts():
        config = genai_types.GenerateContentConfig(
            system_instruction=system_instr,
            thinking_config=genai_types.ThinkingConfig(include_thoughts=True),
        )
        return client.models.generate_content_stream(
            model=model_name,
            contents=contents,
            config=config,
        )

    first_chunk_timeout, chunk_timeout = get_stream_timeouts(model_name)

    text, saw_thought, _ = consume_stream_with_watchdog(
        stream_with_thoughts,
        first_chunk_timeout,
        chunk_timeout,
    )
    if saw_thought:
        log("Gemini: request completed after visible thinking.")
    else:
        log("Gemini: stream completed, but thoughts were not exposed.", "WARN")
    return text

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
        is_cpp = (state.mode == "TYPER_CPP")
        lang_str = "C++" if is_cpp else "Python"
        env_str = "GCC" if is_cpp else "PyPy"
        system_instr = state.system_instr_typer_cpp if is_cpp else state.system_instr_typer
    
    try:
        state.loading_event.set()
        threading.Thread(target=loading_heartbeat, daemon=True).start()
        
        img = get_screenshot()
        if img:
            img_data = img_to_bytes(img)
            log(f"Typing ({lang_str}): Capture taken. Asking Gemini ({model_name})...")
            code_text = generate_gemini_text(
                model_name,
                system_instr,
                f"Solve this Robocontest problem and return the {lang_str} code. (Environment: {env_str})",
                img_data,
            )
            code = adjust_for_autoindent(strip_markdown(code_text))
            
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
        response_text = generate_gemini_text(
            model_name,
            state.system_instr_solver,
            "Solve this question (A/B/C/D).",
            img_bytes,
        )
        ans = response_text.strip().upper()[:1]
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
            state.mode = "TYPER_PYTHON"
        elif state.mode == "TYPER_PYTHON":
            state.mode = "TYPER_CPP"
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
    log("F9            : Switch Mode (Solver -> Typer_Python -> Typer_CPP)")
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
