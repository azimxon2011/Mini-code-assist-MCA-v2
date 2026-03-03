# test_solver_gemini_3.py
import time
import hashlib
from io import BytesIO
import threading
import google.generativeai as genai
import keyboard
import win32api
import win32con
from PIL import ImageGrab

# --- CONFIGURATION ---
API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
MODEL_NAME = "gemini-3-flash-preview"  # 2026 Flagship model for high-speed vision
RIGHT_SHIFT_KEY = "right shift"

# Virtual Key Codes for keyboard indicators
VK_NUM = 0x90
VK_CAPS = 0x14
VK_SCROLL = 0x91

# Mapping answers to indicator states: (NumLock, CapsLock, ScrollLock)
INDICATOR_MAP = {
    "A": (True,  False, False),
    "B": (False, True,  False),
    "C": (False, False, True),
    "D": (True,  True,  True),
    "LOADING": (True, False, True),  # A and C
    "?": (False, False, False),
}

SYSTEM_PROMPT = (
    "You are an Elite Solver. Solve the Math/Physics/English question in the image. "
    "Reason internally but Output ONLY the uppercase letter (A, B, C, or D). "
    "No text, no explanation. If there is answer marked already recheck it and output right answer."
)

# --- INITIALIZATION ---
genai.configure(api_key=API_KEY)
model = genai.GenerativeModel(
    model_name=MODEL_NAME,
    system_instruction=SYSTEM_PROMPT
)

# --- UTILITIES ---
def get_toggle_state(vk_code: int) -> bool:
    return (win32api.GetKeyState(vk_code) & 1) != 0

def press_key(vk_code: int):
    win32api.keybd_event(vk_code, 0, 0, 0)
    time.sleep(0.01)
    win32api.keybd_event(vk_code, 0, win32con.KEYEVENTF_KEYUP, 0)

def set_indicators(state_key: str):
    """Sets keyboard LEDs to reflect the state (letter or LOADING)."""
    states = INDICATOR_MAP.get(state_key.upper(), INDICATOR_MAP["?"])
    vks = [VK_NUM, VK_CAPS, VK_SCROLL]
    
    for vk, target_on in zip(vks, states):
        if get_toggle_state(vk) != target_on:
            press_key(vk)

def loading_heartbeat(stop_event):
    """Blinks every 10 seconds while loading."""
    while not stop_event.is_set():
        set_indicators("LOADING")
        if stop_event.wait(10.0):
            break
        # Blink: OFF then back to LOADING
        set_indicators("?")
        time.sleep(0.2)
        set_indicators("LOADING")

def get_image_hash(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return hashlib.sha256(buf.getvalue()).hexdigest()

def solve_image(img) -> str:
    """Sends image to Gemini with a loading indicator heartbeat."""
    stop_blink = threading.Event()
    blink_thread = threading.Thread(target=loading_heartbeat, args=(stop_blink,))
    
    try:
        blink_thread.start()
        response = model.generate_content(img)
        text = response.text.strip().upper()
        
        # Stop loading indicator
        stop_blink.set()
        blink_thread.join()

        for char in text:
            if char in "ABCD?":
                return char
    except Exception as e:
        stop_blink.set()
        if blink_thread.is_alive():
            blink_thread.join()
        print(f"\n[!] Gemini API Error: {e}")
    return "?"

# --- MAIN LOOP ---
def main():
    print(f"--- TestSolver Gemini 3 (2026) ---")
    print(f"Model: {MODEL_NAME}")
    print(f"Hotkeys: Hold [{RIGHT_SHIFT_KEY}] to solve from clipboard.\n")

    last_hash = None
    last_letter = "?"

    while True:
        try:
            if not keyboard.is_pressed(RIGHT_SHIFT_KEY):
                time.sleep(0.1)
                continue

            img = ImageGrab.grabclipboard()
            if not img:
                print("\r[!] Clipboard is empty.         ", end="")
                time.sleep(0.5)
                continue

            current_hash = get_image_hash(img)
            
            if current_hash == last_hash:
                set_indicators(last_letter)
                time.sleep(0.3)
                continue

            print(f"[*] Analyzing new image... ", end="", flush=True)
            
            letter = solve_image(img)
            print(f"Answer: {letter}")
            
            set_indicators(letter)
            last_hash = current_hash
            last_letter = letter

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n[!] Error: {e}")
            time.sleep(1)
            
        time.sleep(0.1)

if __name__ == "__main__":
    main()
