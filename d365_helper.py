"""
D365 Helper - lokal HTTP-server der modtager prodid og saetter filter i D365 via Citrix.

Krav:
    pip install pywin32 pyautogui

Koen:  python d365_helper.py
"""

import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

try:
    import win32gui
    import win32con
    import ctypes
    import pyautogui
    pyautogui.FAILSAFE = False
except ImportError:
    print("Mangler pakker - koer: pip install pywin32 pyautogui")
    raise

WINDOW_KEYWORDS = [
    "Finance and Operations",
    "Dynamics 365",
    "comrodgroup-prod",
]


def find_d365_hwnd():
    hits = []
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        for kw in WINDOW_KEYWORDS:
            if kw.lower() in title.lower():
                hits.append((hwnd, title))
                break
    win32gui.EnumWindows(cb, None)
    return hits[0][0] if hits else None


def bring_to_front(hwnd):
    ctypes.windll.user32.AllowSetForegroundWindow(
        ctypes.windll.kernel32.GetCurrentProcessId()
    )
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
    target_thread  = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
    ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.AttachThreadInput(current_thread, target_thread, False)
    time.sleep(0.4)


def apply_filter(prod_id: str):
    time.sleep(0.3)
    hwnd = find_d365_hwnd()
    if not hwnd:
        print(f"[FEJL] Fandt ikke D365-vinduet for {prod_id}")
        return
    print(f"[OK] Fandt: {win32gui.GetWindowText(hwnd)!r}")
    bring_to_front(hwnd)
    pyautogui.hotkey("ctrl", "f")
    time.sleep(0.6)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.write(prod_id, interval=0.04)
    time.sleep(0.2)
    pyautogui.press("enter")
    print(f"[OK] Filter sat: {prod_id}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if parsed.path == "/open":
            params  = parse_qs(parsed.query)
            prod_id = params.get("prodid", [""])[0].strip()
            if prod_id:
                self.wfile.write(f"Aabner {prod_id}...\n".encode())
                threading.Thread(target=apply_filter, args=(prod_id,), daemon=True).start()
            else:
                self.wfile.write(b"Mangler ?prodid=\n")
        else:
            self.wfile.write(b"D365 Helper koerer\n")

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port   = 9999
    server = HTTPServer(("localhost", port), Handler)
    print(f"D365 Helper koerer paa http://localhost:{port}/open?prodid=<prodid>")
    print("Tast Ctrl+C for at stoppe.")
    server.serve_forever()
