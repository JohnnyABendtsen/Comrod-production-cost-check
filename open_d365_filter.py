"""
Finder aaben D365 All Production Orders og saetter filter.
Krav: pip install pyautogui pywinauto
Brug: python open_d365_filter.py 113165
"""
import sys, time, subprocess, re
import pyautogui
import pywinauto

PROD_ID = sys.argv[1] if len(sys.argv) > 1 else "113165"
D365_URL = "https://comrodgroup-prod.operations.eu.dynamics.com/?cmp=com&mi=ProdTableListPage"

def find_d365_window():
    from pywinauto import Desktop
    for w in Desktop(backend="uia").windows():
        title = w.window_text()
        if "production orders" in title.lower() and ("chrome" in title.lower() or "edge" in title.lower() or "citrix" in title.lower() or "operations" in title.lower()):
            return w
    return None

# 1. Aaben D365 hvis ikke allerede aaben
win = find_d365_window()
if not win:
    print("Aabner D365 All Production Orders...")
    subprocess.Popen(["cmd", "/c", "start", "", D365_URL], shell=True)
    print("Venter 15 sekunder paa load...")
    time.sleep(15)
    win = find_d365_window()

# 2. Fokuser vinduet
if win:
    win.set_focus()
    time.sleep(1.5)
    print(f"Fundet: {win.window_text()}")
else:
    print("Ingen D365-vindue fundet - sikr at All Production Orders er aaben")
    sys.exit(1)

# 3. Ctrl+F = Quick Filter i D365
pyautogui.hotkey("ctrl", "f")
time.sleep(1.5)

# 4. Skriv ProdId
pyautogui.write(PROD_ID, interval=0.08)
time.sleep(0.5)

# 5. Enter for at anvende filter
pyautogui.press("enter")
print(f"Filter sat: {PROD_ID}")
