#!/usr/bin/env python3
"""
Pharos
"""
import os
import json
import queue
import sys
import threading
import time
import urllib.parse
import urllib.request
from itertools import cycle
from typing import List
import sdl2
import sdl2.ext

# ----------------------------------------------------------------------
# Manifest MD5 cache
# ----------------------------------------------------------------------
MANIFEST_PATH = os.path.join(os.path.dirname(__file__), "resources", "manifest.json")
local_md5s = {}
if os.path.exists(MANIFEST_PATH):
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest = json.load(f)
        for entry in manifest.get("ports", []) + manifest.get("bottles", []):
            local_md5s[entry["name"]] = entry.get("md5")

# ----------------------------------------------------------------------
# Local imports
# ----------------------------------------------------------------------
from config import get_controller_layout, Repository, Port
from input import Input
from ui import (
    UserInterface,
    color_menu_bg,
    color_text,
    FOOTER_HEIGHT,
    BUTTON_AREA_HEIGHT,
)
from download import Downloader

# ----------------------------------------------------------------------
# Safe background task runner
# ----------------------------------------------------------------------
def _safe_bg(func, arg):
    def wrapper():
        thread_name = threading.current_thread().name
        try:
            func(arg)
        except Exception as e:
            import traceback
            print(f"[{thread_name}] CRASHED: {e}\n{traceback.format_exc()}")
    return wrapper

# ----------------------------------------------------------------------
# Pharos core
# ----------------------------------------------------------------------
class Pharos:
    def __init__(self) -> None:
        self.input = Input()
        self.ui = UserInterface()
        self.sources_path = os.path.join(os.path.dirname(__file__), ".sources")
        self.repositories: List[Repository] = []
        self.repo_idx = 0
        self.port_idx = 0
        self.dl_queue: queue.Queue = queue.Queue()
        self.progress_q: queue.Queue = queue.Queue()
        self.layout = get_controller_layout()

        # Loading spinner (only used during repo fetch)
        self.spinner = cycle(["|", "/", "-", "\\"])
        self.spinner_speed = 0.12
        self.last_spinner = 0.0
        self.spinner_frame = ""

        # 2-second sticky message handling
        self.last_progress_msg = None  # (dl, tot, txt, stage)
        self.last_progress_time = 0.0
        self.PROGRESS_STICKY_SECONDS = 2.0

        self.current_view = "repos"
        self.running = True
        self.download_active = False

        self._load_sources()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    def cleanup(self) -> None:
        self.running = False
        try:
            self.dl_queue.put(None)
        except Exception:
            pass

        deadline = time.time() + 5.0
        while time.time() < deadline:
            alive = any(
                t.is_alive() and t.name in ("DownloaderThread", "InputThread")
                for t in threading.enumerate()
                if t is not threading.main_thread()
            )
            if not alive:
                break
            time.sleep(0.05)

        for q in (self.dl_queue, self.progress_q):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

        for repo in self.repositories:
            for port in getattr(repo, "ports", []) or []:
                port.image_path = None
            for bottle in getattr(repo, "bottles", []) or []:
                bottle.image_path = None
        self.repositories.clear()

        try:
            if sdl2.SDL_WasInit(sdl2.SDL_INIT_VIDEO):
                sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_VIDEO)
            if sdl2.SDL_WasInit(sdl2.SDL_INIT_GAMECONTROLLER):
                sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_GAMECONTROLLER)
        except Exception as e:
            print(f"[CLEANUP] SDL error: {e}")
        print("[CLEANUP] Done.")

    # ------------------------------------------------------------------
    # Load .sources
    # ------------------------------------------------------------------
    def _load_sources(self) -> None:
        if len(sys.argv) > 1:
            self.sources_path = sys.argv[1]
            print(f"Using sources path: {self.sources_path}")
        if not os.path.exists(self.sources_path):
            self.ui.draw_log(text_line_1=f"Missing .sources file at {self.sources_path}", background=True)
            return
        with open(self.sources_path, "r", encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if not url or url.startswith("#"):
                    continue
                parsed = urllib.parse.urlparse(url)
                path = parsed.path.lstrip("/")
                if path.endswith(".git"):
                    path = path[:-4]
                if "/" not in path:
                    continue
                owner, name = path.split("/", 1)
                repo = Repository(
                    name=name,
                    url=f"https://github.com/{path}",
                    images_dir=os.path.join(os.path.dirname(__file__), "resources", f"{owner}-{name}-images"),
                    images_zip_url=f"https://github.com/{path}/releases/download/screenshots-latest/images.zip",
                )
                self.repositories.append(repo)
                threading.Thread(
                    target=_safe_bg(self._fetch_images, repo),
                    name=f"ImageFetcher-{repo.name}",
                    daemon=True
                ).start()
        self._fetch_all_ports_or_bottles()

    # ------------------------------------------------------------------
    # Fetch images
    # ------------------------------------------------------------------
    def _fetch_images(self, repo: Repository) -> None:
        worker = Downloader(queue.Queue(), queue.Queue())
        try:
            worker.fetch_repo_images(repo)
        except Exception as e:
            print(f"[ImageFetcher-{repo.name}] Failed: {e}")

    # ------------------------------------------------------------------
    # Fetch ports.json / winecask.json
    # ------------------------------------------------------------------
    def _fetch_ports_json(self, repo: Repository) -> None:
        gh = repo.url.replace("https://github.com/", "").rstrip("/")
        owner, repo_name = gh.split("/", 1)
        branch = "main"
        try:
            api_url = f"https://api.github.com/repos/{owner}/{repo_name}"
            data = json.loads(self._gh_request(api_url))
            branch = data.get("default_branch", "main")
        except Exception:
            pass
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
        for candidate in [f"{raw_base}/ports.json", f"{raw_base}/docs/ports.json"]:
            try:
                raw = self._gh_request(candidate)
                data = json.loads(raw)
                ports_raw = data.get("ports", data) if isinstance(data, dict) else data
                repo.ports = []
                for p in ports_raw:
                    src = p.get("source", {})
                    name = os.path.splitext(p.get("name", "Unnamed"))[0]
                    port = Port(
                        name=name,
                        title=p.get("attr", {}).get("title", p.get("name", "")),
                        desc=p.get("attr", {}).get("desc", "Missing description"),
                        download_url=src.get("download_url", ""),
                        size=src.get("size"),
                        date_updated=src.get("date_updated"),
                    )
                    port.md5 = src.get("md5")
                    local_md5 = local_md5s.get(port.name)
                    port.update_available = bool(local_md5 and port.md5 and local_md5 != port.md5)
                    repo.ports.append(port)
                return
            except Exception:
                continue

    def _fetch_winecask_json(self, repo: Repository) -> None:
        gh = repo.url.replace("https://github.com/", "").rstrip("/")
        owner, repo_name = gh.split("/", 1)
        branch = "main"
        try:
            api_url = f"https://api.github.com/repos/{owner}/{repo_name}"
            data = json.loads(self._gh_request(api_url))
            branch = data.get("default_branch", "main")
        except Exception:
            pass
        raw_base = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}"
        for candidate in [f"{raw_base}/winecask.json", f"{raw_base}/docs/winecask.json"]:
            try:
                raw = self._gh_request(candidate)
                data = json.loads(raw)
                bottles_raw = data.get("bottles", data) if isinstance(data, dict) else data
                repo.bottles = []
                for p in bottles_raw:
                    src = p.get("source", {})
                    name = os.path.splitext(p.get("name", "Unnamed"))[0]
                    bottle = Port(
                        name=name,
                        title=p.get("attr", {}).get("title", p.get("name", "")),
                        desc=p.get("attr", {}).get("desc", "Missing description"),
                        download_url=src.get("download_url", ""),
                        size=src.get("size"),
                        date_updated=src.get("date_updated"),
                    )
                    bottle.md5 = src.get("md5")
                    local_md5 = local_md5s.get(bottle.name)
                    bottle.update_available = bool(local_md5 and bottle.md5 and local_md5 != bottle.md5)
                    repo.bottles.append(bottle)
                return
            except Exception:
                continue

    def _fetch_all_ports_or_bottles(self) -> None:
        for r in self.repositories:
            threading.Thread(target=_safe_bg(self._fetch_ports_json, r), name=f"PortsFetcher-{r.name}", daemon=True).start()
            threading.Thread(target=_safe_bg(self._fetch_winecask_json, r), name=f"WineCaskFetcher-{r.name}", daemon=True).start()

    def _gh_request(self, url: str, timeout: int = 12) -> bytes:
        hdr = {"User-Agent": "Pharos/1.0"}
        req = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    # ------------------------------------------------------------------
    # Spinner
    # ------------------------------------------------------------------
    def _spin(self) -> None:
        now = time.time()
        if now - self.last_spinner >= self.spinner_speed:
            self.last_spinner = now
            self.spinner_frame = next(self.spinner)

    # ------------------------------------------------------------------
    # UI Helpers
    # ------------------------------------------------------------------
    def _draw_button_bar(self, buttons: List[dict]) -> None:
        x = 20
        for b in buttons:
            self.ui.button_circle((x, self.ui.screen_height - 60), b["key"], b["label"], color=b["color"])
            x += 50 + len(b["label"]) * 7

    def _map_port_images(self, repo: Repository) -> None:
        items = getattr(repo, "ports", None) or getattr(repo, "bottles", None)
        if not items or not repo.images_dir or not os.path.isdir(repo.images_dir):
            return
        for item in items:
            for ext in ("png", "jpg", "jpeg"):
                path = os.path.join(repo.images_dir, f"{item.name}.screenshot.{ext}")
                if os.path.exists(path):
                    item.image_path = path
                    break
            else:
                item.image_path = None

    # ------------------------------------------------------------------
    # Render Views
    # ------------------------------------------------------------------
    def _render_repos(self) -> None:
        self.ui.draw_header("Select A Repository", color_text)
        pending = [r for r in self.repositories if not (getattr(r, "ports", None) or getattr(r, "bottles", None))]
        if pending:
            self._spin()
            self.ui.draw_log(text_line_1=f"{self.spinner_frame} Loading {len(pending)} repo(s)…", background=True)
            self._draw_button_bar([])
            return

        total_items = sum(len(getattr(r, "ports", [])) + len(getattr(r, "bottles", [])) for r in self.repositories)
        total_repos = sum(1 for r in self.repositories if getattr(r, "ports", None) or getattr(r, "bottles", None))
        self.ui.draw_log(text_line_1=f"Loaded {total_items} items in {total_repos} repositories", background=True)

        max_vis = 12
        start = max(0, self.repo_idx - max_vis + 1)
        visible = self.repositories[start: start + max_vis]
        for i, repo in enumerate(visible):
            y = 30 + i * 30
            sel = (start + i) == self.repo_idx
            cnt = len(getattr(repo, "ports", []) or getattr(repo, "bottles", []))
            owner = repo.url.split("github.com/")[1].split("/")[0]
            txt = f"{owner}/{repo.name} ({cnt} items)"
            self.ui.row_list(txt, (20, y), self.ui.screen_width // 2, 28, sel, color=color_text if sel else color_menu_bg)

        btns = [
            {"key": self.layout["a"]["btn"], "label": "Open", "color": self.layout["a"]["color"]},
            {"key": self.layout["b"]["btn"], "label": "Exit", "color": self.layout["b"]["color"]},
        ]
        self._draw_button_bar(btns)

    def _render_ports(self) -> None:
        repo = self.repositories[self.repo_idx]
        items = getattr(repo, "ports", None) or getattr(repo, "bottles", None)
        owner = repo.url.split("github.com/")[1].split("/")[0]
        self.ui.draw_header(f"{owner}/{repo.name}", color_text)

        max_vis = 12
        start = max(0, self.port_idx - max_vis + 1)
        visible = items[start: start + max_vis] if items else []
        selected_item = None
        for i, item in enumerate(visible):
            y = 30 + i * 30
            sel = (start + i) == self.port_idx
            size = f" [{item.size / (1024*1024):.2f} MB]" if item.size else ""
            self.ui.row_list(
                f"{item.title}{size}",
                (20, y),
                self.ui.screen_width // 3,
                28,
                sel,
                color=color_text if sel else color_menu_bg,
                highlight=item.update_available
            )
            if sel:
                selected_item = item

        if selected_item and selected_item.image_path and os.path.exists(selected_item.image_path):
            try:
                self.ui.draw_port_image(selected_item)
            except Exception:
                pass  # Image error handled below
        if selected_item and selected_item.desc:
            self.ui.draw_wrapped_text_centered(
                selected_item.desc,
                center_x=(self.ui.screen_width * 3 // 4 - 20),
                start_y=400 - FOOTER_HEIGHT - BUTTON_AREA_HEIGHT - 5,
                max_width=self.ui.screen_width // 2,
                color=color_text,
            )

        # ------------------------------------------------------------------
        # Progress Queue Handling
        # ------------------------------------------------------------------
        has_download = not self.dl_queue.empty() or not self.progress_q.empty()
        latest = None
        while True:
            try:
                item = self.progress_q.get_nowait()
                if isinstance(item, tuple) and len(item) == 4:
                    latest = item
            except queue.Empty:
                break

        if latest is not None:
            self.last_progress_msg = latest
            self.last_progress_time = time.time()
            dl, tot, txt, stage = latest
            if stage == "download" and tot:
                self.progress_percent = (dl / tot) * 100
        elif self.last_progress_msg is not None:
            elapsed = time.time() - self.last_progress_time
            if elapsed >= self.PROGRESS_STICKY_SECONDS:
                self.last_progress_msg = None
                self.progress_percent = 0
        else:
            self.progress_percent = 0

        # ------------------------------------------------------------------
        # Bottom Log
        # ------------------------------------------------------------------
        if self.last_progress_msg:
            _, _, txt, stage = self.last_progress_msg
            bottom_log = txt
            if stage == "download":
                self.ui.draw_loader(self.progress_percent)
        else:
            selected = items[self.port_idx] if items else None
            local_md5 = local_md5s.get(selected.name) if selected else None
            if selected and selected.md5 and local_md5 and selected.md5 != local_md5:
                bottom_log = f"{selected.title} – Update available!"
            else:
                bottom_log = f"{len(items)} items in {owner}/{repo.name}"

        self.ui.draw_log(text_line_1=bottom_log, background=True)

        btns = [
            {"key": self.layout["a"]["btn"], "label": "Download", "color": self.layout["a"]["color"]},
            {"key": self.layout["b"]["btn"], "label": "Back", "color": self.layout["b"]["color"]},
            {"key": self.layout["y"]["btn"], "label": "All", "color": self.layout["y"]["color"]},
        ]
        self._draw_button_bar(btns)

    # ------------------------------------------------------------------
    # Input Handling
    # ------------------------------------------------------------------
    def _update_repos(self) -> None:
        if not self.repositories:
            return
        repo = self.repositories[self.repo_idx]
        if self.input.key(self.layout["a"]["key"]) and (getattr(repo, "ports", None) or getattr(repo, "bottles", None)):
            self.current_view = "ports"
            self.port_idx = 0
            if repo.images_dir and os.path.isdir(repo.images_dir):
                self._map_port_images(repo)
        elif self.input.key(self.layout["b"]["key"]):
            if self.current_view == "repos":
                print("[USER] Exit requested via B button")
                self.running = False
            else:
                self.current_view = "repos"
        else:
            self.repo_idx = self.input.handle_navigation(self.repo_idx, 10, len(self.repositories))

    def _update_ports(self) -> None:
        repo = self.repositories[self.repo_idx]
        items = getattr(repo, "ports", None) or getattr(repo, "bottles", None)
        if not items:
            return
        if self.input.key(self.layout["a"]["key"]):
            self.dl_queue.put((items[self.port_idx], "port" if getattr(repo, "ports", None) else "bottle"))
            self._ensure_worker()
        elif self.input.key(self.layout["y"]["key"]):
            for item in items:
                self.dl_queue.put((item, "port" if getattr(repo, "ports", None) else "bottle"))
            self._ensure_worker()
        if self.input.key(self.layout["b"]["key"]):
            for item in items:
                item.image_path = None
            self.current_view = "repos"
            while not self.dl_queue.empty():
                try:
                    self.dl_queue.get_nowait()
                except queue.Empty:
                    break
        else:
            self.port_idx = self.input.handle_navigation(self.port_idx, 10, len(items))

    # ------------------------------------------------------------------
    # Worker Management
    # ------------------------------------------------------------------
    def _ensure_worker(self) -> None:
        if self.download_active:
            return
        if any(t.name == "DownloaderThread" and t.is_alive() for t in threading.enumerate()):
            return
        self.download_active = True
        worker = Downloader(self.dl_queue, self.progress_q)
        t = threading.Thread(
            target=self._download_wrapper,
            args=(worker,),
            name="DownloaderThread",
            daemon=True,
        )
        t.start()

    def _download_wrapper(self, worker: Downloader) -> None:
        try:
            worker.run()
        except Exception:
            import traceback
            print(f"[DOWNLOAD FAILED]\n{traceback.format_exc()}")
        finally:
            self.download_active = False

    # ------------------------------------------------------------------
    # Main Loop
    # ------------------------------------------------------------------
    def start(self) -> None:
        threading.Thread(target=self._monitor_input, name="InputThread", daemon=True).start()

    def _monitor_input(self) -> None:
        while self.running:
            try:
                for ev in sdl2.ext.get_events():
                    self.input.check_event(ev)
                    if ev.type == sdl2.SDL_QUIT:
                        continue
            except Exception as e:
                print(f"[INPUT THREAD ERROR] {e}")
                self.running = False
            time.sleep(0.001)

    def update(self) -> None:
        if self.current_view == "repos":
            self._render_repos()
            self._update_repos()
        else:
            self._render_ports()
            self._update_ports()