"""Capture screenshots of the working system for the Phase 2/3 submissions.

    python deliverables/make_screenshots.py cli "How much is 2,000 UAE dirhams in Indian rupees?"   # live, needs key
    python deliverables/make_screenshots.py streamlit S1                                            # offline replay

cli        runs cli.py with the given messages, converts the ANSI output to HTML and screenshots it
streamlit  starts the Streamlit app in replay mode (no API calls) and screenshots the page
"""
from __future__ import annotations

import html
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PYTHON = sys.executable
ANSI = {"2": "color:#8b949e", "1": "font-weight:bold", "36": "color:#58c4dc", "31": "color:#ff7b72"}


def chrome_screenshot(url: str, out: Path, width: int, height: int) -> None:
    args = [CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
            f"--window-size={width},{height}", f"--screenshot={out}"]
    subprocess.run(args + [url], check=True, capture_output=True, timeout=180)


def chrome_screenshot_after(url: str, out: Path, width: int, height: int, wait_s: float, port: int = 9333) -> None:
    """Screenshot a page that renders after load (Streamlit draws over a websocket), via the DevTools protocol."""
    import base64
    import itertools
    import json
    import tempfile

    from tornado.ioloop import IOLoop
    from tornado.websocket import websocket_connect

    profile = tempfile.mkdtemp(prefix="tripmate-chrome-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                               f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                               f"--window-size={width},{height}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                target = next(p for p in pages if p["type"] == "page")
                break
            except (OSError, StopIteration, ValueError):
                time.sleep(0.25)
        ids = itertools.count(1)

        async def session():
            ws = await websocket_connect(target["webSocketDebuggerUrl"], max_message_size=64 * 1024 * 1024)

            async def send(method, **params):
                msg_id = next(ids)
                ws.write_message(json.dumps({"id": msg_id, "method": method, "params": params}))
                while True:
                    reply = json.loads(await ws.read_message())
                    if reply.get("id") == msg_id:
                        return reply.get("result", {})

            await send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=2,
                       mobile=False)
            await send("Page.navigate", url=url)
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                ws.write_message(json.dumps({"id": 0, "method": "Runtime.evaluate", "params": {"expression": "1"}}))
                await ws.read_message()
                time.sleep(0.5)
            shot = await send("Page.captureScreenshot", format="png")
            out.write_bytes(base64.b64decode(shot["data"]))
            ws.close()

        IOLoop.current().run_sync(session, timeout=wait_s + 60)
    finally:
        chrome.terminate()
        chrome.wait(timeout=10)


def ansi_to_html(text: str) -> str:
    out, open_spans = [], 0
    for part in re.split(r"(\x1b\[[0-9;]*m)", text):
        m = re.fullmatch(r"\x1b\[([0-9;]*)m", part)
        if not m:
            out.append(html.escape(part))
        elif m.group(1) in ("0", ""):
            out.append("</span>" * open_spans)
            open_spans = 0
        else:
            out.append(f"<span style='{ANSI.get(m.group(1), '')}'>")
            open_spans += 1
    return "".join(out) + "</span>" * open_spans


def cli(messages: list[str], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    script = "\n".join(messages + ["/quit"]) + "\n"
    run = subprocess.run([PYTHON, "cli.py"], input=script, capture_output=True, text=True, cwd=ROOT, timeout=900)
    transcript = run.stdout
    for message in messages:                              # echo the piped input the way a terminal would show it
        transcript = transcript.replace("You:\x1b[0m ", f"You:\x1b[0m {message}\n", 1)
    (out.with_suffix(".txt")).write_text(re.sub(r"\x1b\[[0-9;]*m", "", transcript))
    lines = transcript.count("\n") + 4
    body = ansi_to_html(transcript)
    page = out.with_suffix(".html")
    page.write_text(
        "<html><body style='margin:0;background:#0d1117'>"
        "<div style='background:#161b22;color:#8b949e;font:13px -apple-system,Helvetica;padding:8px 14px'>"
        "● ● ●&nbsp;&nbsp;&nbsp;tripmate: python cli.py</div>"
        "<pre style='margin:0;padding:14px 18px;color:#e6edf3;font:14px/1.45 Menlo,Consolas,monospace;"
        f"white-space:pre-wrap'>{body}</pre></body></html>")
    chrome_screenshot(page.as_uri(), out, 1100, min(60 + lines * 21, 4000))
    page.unlink()
    print("wrote", out)


def streamlit(scenario: str, out: Path, port: int = 8599) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([PYTHON, "-m", "streamlit", "run", "streamlit_app.py", "--server.headless", "true",
                             "--server.port", str(port), "--browser.gatherUsageStats", "false", "--theme.base", "light"],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://localhost:{port}/_stcore/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        chrome_screenshot_after(f"http://localhost:{port}/?replay={scenario}", out, 1440, 2000, wait_s=15)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print("wrote", out)


if __name__ == "__main__":
    mode, rest = sys.argv[1], sys.argv[2:]
    if mode == "cli":
        cli(rest, HERE / "phase2" / "cli_screenshot.png")
    elif mode == "streamlit":
        name = rest[0] if rest else "S1"
        streamlit(name, HERE / "phase3" / f"streamlit_replay_{name}.png")
