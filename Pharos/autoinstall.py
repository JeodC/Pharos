#!/usr/bin/env python3
"""
AutoInstaller
"""
from pathlib import Path
import zipfile
import shutil
import xml.etree.ElementTree as ET

AUTOINSTALL_DIR = Path(__file__).parent / "autoinstall"
PORTS_DIR = Path("/roms/ports")
WINDOWS_DIR = Path("/roms/windows")


class AutoInstaller:
    def __init__(self, autoinstall_dir: Path = AUTOINSTALL_DIR):
        self.autoinstall_dir = Path(autoinstall_dir)
        self.autoinstall_dir.mkdir(parents=True, exist_ok=True)

    def install_zip(self, zip_name: str) -> int:
        zip_path = self.autoinstall_dir / zip_name
        if not zip_path.is_file():
            print(f"[ERROR] {zip_path} not found")
            return 255

        print(f"[INSTALL] Installing {zip_name}...")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                # Find port.json or bottle.json
                target_json = None
                for file in zf.namelist():
                    if file.endswith(("port.json", "bottle.json")):
                        target_json = file
                        break
                if not target_json:
                    print(f"[ERROR] No port.json or bottle.json in {zip_name}")
                    return 255

                is_port = target_json.endswith("port.json")
                base_dir = PORTS_DIR if is_port else WINDOWS_DIR
                gamelist_path = base_dir / "gamelist.xml"
                base_dir.mkdir(parents=True, exist_ok=True)

                print(f"[EXTRACT] Extracting {zip_name} → {base_dir}/ (flat)")
                zf.extractall(base_dir)

                # Clean macOS junk
                for junk in base_dir.rglob("__MACOSX"):
                    if junk.is_dir():
                        shutil.rmtree(junk, ignore_errors=True)
                for junk in base_dir.rglob("*.DS_Store"):
                    junk.unlink(missing_ok=True)

                # Find gameinfo.xml in extracted files
                gameinfo_file = None
                for file in zf.namelist():
                    if file.endswith("gameinfo.xml"):
                        candidate = base_dir / file
                        if candidate.is_file():
                            gameinfo_file = candidate
                            break

                if gameinfo_file:
                    print(f"[GAMELIST] Found {gameinfo_file}")
                    self.gamelist_add(gameinfo_file, gamelist_path)
                else:
                    print(f"[WARN] No gameinfo.xml found in {zip_name}")

        except zipfile.BadZipFile:
            print(f"[ERROR] {zip_name} is not a valid zip")
            return 255
        except Exception as e:
            print(f"[ERROR] Failed to install {zip_name}: {e}")
            return 255

        zip_path.unlink(missing_ok=True)
        print(f"[OK] Installed {zip_name}")
        return 0

    def gamelist_add(self, gameinfo_file: Path, gamelist_path: Path) -> None:
        try:
            tree = ET.parse(gameinfo_file)
            root_gameinfo = tree.getroot()
        except ET.ParseError as e:
            print(f"[ERROR] Bad gameinfo.xml ({gameinfo_file.name}): {e}")
            return

        # Load or create gamelist.xml
        if gamelist_path.exists():
            try:
                tree = ET.parse(gamelist_path)
                root = tree.getroot()
                if root.tag.lower() != "gamelist":
                    root.tag = "gameList"
            except ET.ParseError:
                print(f"[WARN] Corrupted gamelist.xml — creating new")
                root = ET.Element("gameList")
                tree = ET.ElementTree(root)
        else:
            root = ET.Element("gameList")
            tree = ET.ElementTree(root)
            print(f"[CREATE] Creating {gamelist_path}")

        # Avoid duplicates by <path>
        existing_paths = {
            game.find("path").text
            for game in root.findall("game")
            if game.find("path") is not None
        }

        count = 0
        for game in root_gameinfo.findall("game"):
            path_elem = game.find("path")
            if path_elem is not None:
                path = path_elem.text
                if path not in existing_paths:
                    root.append(game)
                    existing_paths.add(path)
                    count += 1

        if count > 0:
            if hasattr(ET, "indent"):
                ET.indent(root, space="  ", level=0)
            gamelist_path.parent.mkdir(parents=True, exist_ok=True)
            tree.write(
                gamelist_path,
                encoding="utf-8",
                xml_declaration=True,
                short_empty_elements=False
            )
            print(f"[GAMELIST] Added {count} game(s) → {gamelist_path.name}")
        else:
            print(f"[INFO] No new entries from {gameinfo_file.name}")