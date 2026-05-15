import ctypes
import time

OCR_NORMAL = 32512
OCR_APPSTARTING = 32650
SPI_SETCURSORS = 0x0057

def set_loading_cursor():
    hWaitCursor = ctypes.windll.user32.LoadCursorW(0, OCR_APPSTARTING)
    hCursorCopy = ctypes.windll.user32.CopyIcon(hWaitCursor)
    ctypes.windll.user32.SetSystemCursor(hCursorCopy, OCR_NORMAL)

def restore_cursor():
    ctypes.windll.user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, 0)

if __name__ == "__main__":
    print("Setting cursor...")
    set_loading_cursor()
    time.sleep(3)
    print("Restoring cursor...")
    restore_cursor()
    print("Done")
