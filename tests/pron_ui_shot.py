"""Screenshots of the Pronunciation page with real checks, for a visual check of the page.

    uv run python tests/pron_ui_shot.py <synthetic folder from pron_synth.py> <output folder>

Starts a test server on port 8799 with throwaway data, checks a few synthetic recordings through the API (so
the statistics and recent checks have content), opens the page in headless Chrome (DevTools protocol), opens
the /p/-/b/ drill and a saved result, and saves screenshots. Prints any JavaScript error.
"""

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
PORT = 8799


class Page:
    def __init__(self, ws_url: str):
        self.ws = connect(ws_url, max_size=50_000_000, legacy=True)
        self.n = 0

    def call(self, method: str, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.n:
                return msg.get("result", {})

    def js(self, expr: str):
        r = self.call("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        return r.get("result", {}).get("value")

    def shot(self, path: Path):
        # The page scrolls inside the app's main area, not the document: the window grows to the page's height
        h = self.js("Math.max(document.documentElement.scrollHeight, document.querySelector('#page-host')?.scrollHeight || 0)")
        self.call("Emulation.setDeviceMetricsOverride", width=1300, height=min(int(h), 3000), deviceScaleFactor=1,
                  mobile=False)
        time.sleep(0.4)
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", format="png")["data"]))


def main(folder: str, out: str) -> int:
    folder, out = Path(folder), Path(out)
    out.mkdir(parents=True, exist_ok=True)
    data = Path(tempfile.mkdtemp(prefix="pron-ui-"))
    env = {**os.environ, "TUTOR_PORT": str(PORT), "TUTOR_DATA_DIR": str(data / "data"),
           "TUTOR_MATERIALS_DIR": str(data / "materials"), "TUTOR_LOGS_DIR": str(data / "logs")}
    server = subprocess.Popen([sys.executable, "-m", "tutor"], cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                              stderr=open(data / "server.err.log", "w"))
    chrome = None
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=300)
        for _ in range(60):
            try:
                c.get("/api/profile")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest:
            if item["voice"] in ("af_heart", "bm_george") and item["text"] in (
                    "Please pass the paper.", "pen", "very", "street", "I think there are three things."):
                drill = {"pen": "p-b", "Please pass the paper.": "p-b", "very": "f-v"}.get(item["text"])
                c.post("/api/pronunciation/check", files={"audio": ("s.wav", (folder / f"{item['id']}.wav").read_bytes(), "audio/wav")},
                       data={"text": item["text"], **({"drill": drill} if drill else {})}).raise_for_status()
        chrome = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9333", "--window-size=1300,1400",
                                   f"--user-data-dir={data / 'chrome'}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                target = next(t for t in httpx.get("http://127.0.0.1:9333/json/list").json() if t["type"] == "page")
                break
            except (httpx.HTTPError, StopIteration):
                time.sleep(0.25)
        page = Page(target["webSocketDebuggerUrl"])
        page.call("Page.enable")
        page.call("Page.addScriptToEvaluateOnNewDocument", source=(
            "window.__errors = []; window.addEventListener('error', e => window.__errors.push(String(e.message)));"
            "window.addEventListener('unhandledrejection', e => window.__errors.push(String(e.reason)));"))
        page.call("Page.navigate", url=f"http://127.0.0.1:{PORT}/#pronunciation")
        time.sleep(2.5)
        page.shot(out / "1-pronunciation-page.png")
        page.js("[...document.querySelectorAll('.chip')].find(b => b.textContent === '/p/ and /b/').click()")
        time.sleep(1.5)
        page.shot(out / "2-drill.png")
        page.js("[...document.querySelectorAll('#page-host .pron-recent .link-btn')].find(b => b.textContent.startsWith('Please pass')).click()")
        time.sleep(1.5)
        page.shot(out / "3-result.png")
        page.js("[...document.querySelectorAll('#page-host .pron-recent .link-btn')].find(b => b.textContent === 'street').click()")
        time.sleep(1.5)
        page.shot(out / "4-result-added-vowel.png")
        page.js("document.querySelector('.sb-item[data-route=home]').click()")
        time.sleep(1.5)
        page.shot(out / "5-home.png")
        print("JavaScript errors:", page.js("window.__errors"))
    finally:
        if chrome:
            chrome.terminate()
        server.terminate()
        server.wait(timeout=30)
    print(f"screenshots in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
