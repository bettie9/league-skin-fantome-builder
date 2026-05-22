"""
Sunshine skin builder — produces per-skin .fantome mods that load as
skin0 bin-swap.

Usage:
    python build.py --league "C:\\Riot Games\\League of Legends" --out .\\out
        [--only Ahri,Katarina] [--limit 3] [--refresh-hashes]
"""

from __future__ import annotations
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).parent.resolve()

LTMAO_SRC = HERE / "_vendor" / "LtMAO" / "src"
if not LTMAO_SRC.exists():
    sys.exit(f"vendored LtMAO not found at {LTMAO_SRC}")
sys.path.insert(0, str(LTMAO_SRC))

WORK_DIR = HERE
os.makedirs(WORK_DIR / "pref" / "hashes" / "cdtb_hashes", exist_ok=True)
os.chdir(WORK_DIR)

from LtMAO import pyRitoFile, hash_helper  # type: ignore
from LtMAO.hash_helper import CDTBHashes, CustomHashes, Storage  # type: ignore
from LtMAO import no_skin  # type: ignore

for _d in (CDTBHashes.local_dir, CustomHashes.local_dir,
           "./pref/hashes/extracted_hashes"):
    os.makedirs(_d, exist_ok=True)


# ----- CommunityDragon catalog ---------------------------------------------

CDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
CDRAGON_BASE     = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"
CDRAGON_SUMMARY  = f"{CDRAGON_BASE}/v1/champion-summary.json"
CDRAGON_SKINS    = f"{CDRAGON_BASE}/v1/skins.json"


def http_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Sunshine-skin-builder/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_champion_catalog() -> tuple[str, dict[str, dict[int, str]], dict[str, dict[int, dict]]]:
    """Returns (patch, {key: {skinNum: name}}, chroma_meta)."""
    try:
        patch = http_json(CDRAGON_VERSIONS)[0]
    except Exception:
        patch = "latest"

    summary = http_json(CDRAGON_SUMMARY)
    skins   = http_json(CDRAGON_SKINS)
    id_to_key = {int(c["id"]): c["alias"] for c in summary if int(c["id"]) > 0}
    print(f"[catalog] patch {patch} — {len(id_to_key)} champions, {len(skins)} skin entries")

    catalog: dict[str, dict[int, str]] = {key: {} for key in id_to_key.values()}
    for sid_str, sinfo in skins.items():
        try:
            sid = int(sid_str)
        except (ValueError, TypeError):
            continue
        champ_id, skin_num = (sid, 0) if sid < 1000 else (sid // 1000, sid % 1000)
        key = id_to_key.get(champ_id)
        if key is None or skin_num == 0:
            continue
        catalog[key][skin_num] = sinfo.get("name") or f"{key} skin {skin_num}"

    # Per-champion chroma + form metadata, fetched in parallel
    chroma_meta: dict[str, dict[int, dict]] = {key: {} for key in id_to_key.values()}
    chroma_total = form_total = 0

    def _fetch_one(cid: int) -> tuple[int, list[dict]]:
        url = f"{CDRAGON_BASE}/v1/champions/{cid}.json"
        try:
            return cid, http_json(url).get("skins", [])
        except Exception:
            return cid, []

    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(_fetch_one, cid) for cid in id_to_key]
        for fut in as_completed(futs):
            cid, champ_skins = fut.result()
            key = id_to_key.get(cid)
            if not key:
                continue
            for s in champ_skins:
                parent_id = int(s.get("id") or 0)
                if parent_id < 1000 or parent_id // 1000 != cid:
                    continue
                parent_num  = parent_id % 1000
                parent_name = s.get("name") or f"{key} skin {parent_num}"
                for chroma in s.get("chromas") or []:
                    cid_full = int(chroma["id"])
                    if cid_full < 1000 or cid_full // 1000 != cid:
                        continue
                    n = cid_full % 1000
                    if n in catalog[key]:
                        continue
                    full = chroma.get("name") or f"{parent_name} (Chroma {n})"
                    short = full
                    if "(" in short and short.endswith(")"):
                        short = short.rsplit("(", 1)[1].rstrip(")").strip()
                    catalog[key][n] = full
                    chroma_meta[key][n] = {
                        "parent_num":  parent_num,
                        "parent_name": parent_name,
                        "short_name":  short or full,
                        "kind":        "chroma",
                    }
                    chroma_total += 1
                tiers = (s.get("questSkinInfo") or {}).get("tiers") or []
                if len(tiers) >= 2:
                    for t in tiers:
                        tid = int(t.get("id") or 0)
                        if tid < 1000 or tid // 1000 != cid:
                            continue
                        tnum = tid % 1000
                        if tnum == parent_num:
                            continue
                        tname = t.get("name") or f"{parent_name} (Stage {t.get('stage', tnum)})"
                        catalog[key][tnum] = tname
                        short = t.get("stage") or t.get("name") or f"Stage {tnum}"
                        if isinstance(short, int):
                            short = f"Stage {short}"
                        chroma_meta[key][tnum] = {
                            "parent_num":  parent_num,
                            "parent_name": parent_name,
                            "short_name":  str(short),
                            "kind":        "form",
                        }
                        form_total += 1

    print(f"[catalog] +{chroma_total} chromas, +{form_total} forms")
    return patch, catalog, chroma_meta


def load_hashes(refresh: bool):
    cache = WORK_DIR / "pref" / "hashes" / "cdtb_hashes"
    if refresh and cache.exists():
        for f in cache.iterdir():
            f.unlink()
    print("[hashes] syncing CDragon hashes...")
    CDTBHashes.sync_all()
    CustomHashes.read_all_hashes()
    print(f"[hashes] game={len(Storage.hashtables['hashes.game.txt'])} entries={len(Storage.hashtables['hashes.binentries.txt'])}")


# ----- Per-skin fantome builder --------------------------------------------

class SkinBuilder:
    def __init__(self, champions_dir: Path, output_dir: Path, catalog: dict,
                 chroma_meta: dict[str, dict[int, dict]] | None = None):
        self.champions_dir = champions_dir
        self.output_dir = output_dir
        self.catalog = catalog
        self.chroma_meta = chroma_meta or {}
        self._cdtb_hash_dir = HERE / "pref" / "hashes" / "cdtb_hashes"

        # Pre-filter game hashes to per-character skin + animation bins
        all_game = hash_helper.Storage.hashtables["hashes.game.txt"]
        self.skin_bin_hashes: dict[int, str] = {}
        for h, path in all_game.items():
            pl = path.lower()
            if pl.endswith(".bin") and "data/characters/" in pl and "root.bin" not in pl:
                if "/skins/" in pl or "/animations/" in pl:
                    self.skin_bin_hashes[h] = path

    def build_all(self, only_keys: list[str] | None, limit: int | None):
        wads = sorted(self.champions_dir.glob("*.wad.client"))
        wads = [w for w in wads
                if w.name.count(".") == 2
                and "_" not in w.name.split(".", 1)[0]]
        print(f"[builder] {len(wads)} champion WADs")

        index: dict[str, dict[int, str]] = {}
        for wad_path in wads:
            champ_key = wad_path.name.split(".", 1)[0]
            if only_keys and champ_key not in only_keys:
                continue
            built = self.build_champion(wad_path, champ_key, limit)
            if built:
                index[champ_key] = built

        # index.json: split chromas/forms out of base skin list
        idx_champions: dict[str, dict] = {}
        for k, v in index.items():
            cmap = self.chroma_meta.get(k, {})
            base_skins: dict[str, str] = {}
            chromas: dict[str, dict[str, str]] = {}
            forms:   dict[str, dict[str, str]] = {}
            for n, name in v.items():
                meta = cmap.get(n)
                if meta:
                    bucket = forms if meta.get("kind") == "form" else chromas
                    bucket.setdefault(str(meta["parent_num"]), {})[str(n)] = meta["short_name"]
                else:
                    base_skins[str(n)] = name
            idx_champions[k] = {"skins": base_skins, "chromas": chromas, "forms": forms}

        idx_file = self.output_dir / "index.json"
        idx_file.write_text(json.dumps(
            {"patch": getattr(self, "_patch", "unknown"), "champions": idx_champions},
            indent=2, ensure_ascii=False,
        ), encoding="utf-8")
        print(f"[builder] wrote {idx_file}")

    def build_champion(self, wad_path: Path, champ_key: str, limit: int | None) -> dict[int, str]:
        names = self.catalog.get(champ_key)
        if not names:
            return {}
        skips = no_skin.SKIPS.get(champ_key.lower())

        wad = pyRitoFile.wad.WAD().read(str(wad_path))
        wad.un_hash({"hashes.game.txt": self.skin_bin_hashes})

        # WADs bundle the main champion + auxiliary characters (e.g.
        # ZedShadow, AnnieTibbers). Patch all of them for a complete swap.
        champ_lower = champ_key.lower()
        characters: dict[str, dict] = {}    # char_lower -> {"skin0": chunk, "skinN": {n: chunk}}
        animations: dict[str, dict] = {}
        for chunk in wad.chunks:
            if chunk.extension != "bin":
                continue
            parts = chunk.hash.lower().split("/")
            if (len(parts) < 5 or parts[0] != "data" or parts[1] != "characters"):
                continue
            kind = parts[3]
            skinx = parts[4]
            if not skinx.endswith(".bin") or kind not in ("skins", "animations"):
                continue
            char = parts[2]
            base = skinx[:-4]
            target = characters if kind == "skins" else animations
            ent = target.setdefault(char, {"skin0": None, "skinN": {}})
            if base == "skin0":
                ent["skin0"] = chunk
            else:
                try:
                    n = int(base.removeprefix("skin"))
                except ValueError:
                    continue
                ent["skinN"][n] = chunk

        if champ_lower not in characters or not characters[champ_lower]["skin0"]:
            return {}

        main_skinN = characters[champ_lower]["skinN"]
        built: dict[int, str] = {}
        out_dir = self.output_dir / "skins" / champ_key
        out_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for num, _chunk in sorted(main_skinN.items()):
            if num not in names:
                continue
            display = names[num]
            if skips:
                if skips == "all":
                    continue
                if isinstance(skips, list) and f"skin{num}.bin" in skips:
                    continue
            try:
                self._build_one(wad_path, champ_key, num, display,
                                characters, animations, out_dir)
                built[num] = display
                count += 1
                if limit and count >= limit:
                    break
            except Exception as e:
                print(f"  ! {champ_key} skin{num}: {e}")
        print(f"  · {champ_key}: {len(built)} fantomes")
        return built

    def _build_one(self, wad_path, champ_key, num, display,
                   characters, animations, out_dir):
        """Build one fantome: skin{N}.bin -> skin0.bin + animation patch."""
        patched: list[tuple[str, bytes]] = []

        for char_lower, info in characters.items():
            if num not in info["skinN"]:
                continue
            chunk = info["skinN"][num]
            with pyRitoFile.stream.BytesStream.reader(str(wad_path)) as bs:
                chunk.read_data(bs)
                skin_raw = bytes(chunk.data)
                chunk.free_data()
            try:
                skin0_bytes = self._patch_skin_bin(char_lower, skin_raw, num)
            except RuntimeError as e:
                print(f"      · skip {char_lower}: {e}")
                continue
            patched.append((f"data/characters/{char_lower}/skins/skin0.bin", skin0_bytes))

        for char_lower, info in animations.items():
            if num not in info["skinN"]:
                continue
            chunk = info["skinN"][num]
            with pyRitoFile.stream.BytesStream.reader(str(wad_path)) as bs:
                chunk.read_data(bs)
                anim_raw = bytes(chunk.data)
                chunk.free_data()
            try:
                anim_bytes = self._patch_animation_bin(char_lower, anim_raw, num)
            except RuntimeError:
                continue
            patched.append((f"data/characters/{char_lower}/animations/skin0.bin", anim_bytes))

        if not patched:
            raise RuntimeError("no characters patched")

        # Output path with chroma/form parent grouping
        def _safe(s: str) -> str:
            return s.replace("/", "_").replace("\\", "_").replace(":", "").strip()
        info = self.chroma_meta.get(champ_key, {}).get(num)
        if info:
            parent = out_dir / _safe(info["parent_name"])
            parent.mkdir(parents=True, exist_ok=True)
            out_path = parent / f"{_safe(info['short_name'])}.fantome"
        else:
            out_path = out_dir / f"{_safe(display)}.fantome"

        meta = {
            "Name": display, "Author": "Sunshine Builder",
            "Version": "1.0.0", "Description": "Auto-built by Sunshine Builder.",
        }
        wad_binary = self._pack_wad(patched)
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
            zf.writestr("META/info.json", json.dumps(meta, indent=2))
            zf.writestr(f"WAD/{wad_path.name}", wad_binary)
        print(f"    + {champ_key} skin{num} -> {out_path.name} ({out_path.stat().st_size}B)")

    def _patch_skin_bin(self, char_lower: str, skin_raw: bytes, num: int) -> bytes:
        """Hybrid bin patcher.

        Most skins go through the fast in-memory path (`_patch_via_pyritofile`):
        ~50ms each, BIN.write is lossless on plain skins.

        Gear-tier skins (Battle Queen Katarina, Pajama Guardian Lulu, etc.)
        have `mGearSkinUpgrades` entries that pyRitoFile's BIN.write
        truncates. Those go through `_patch_via_ritobin` for a byte-perfect
        text-level round-trip plus the gear-resource inlining.
        """
        skin_bin = self._read_bin_full(skin_raw)
        if self._is_gear_tier(skin_bin):
            return self._patch_via_ritobin(char_lower, skin_raw, num)
        return self._patch_via_pyritofile(char_lower, skin_bin)

    @staticmethod
    def _is_gear_tier(skin_bin) -> bool:
        """A skin is gear-tier if the bin contains any GearSkinUpgrade
        top-level entry (Battle Queen Katarina, Pajama Guardian Lulu,
        Battlecast Skarner, etc.)."""
        from LtMAO.pyRitoFile.helper import FNV1a  # type: ignore
        gear_type = f"{FNV1a('GearSkinUpgrade'):08x}"
        for entry in skin_bin.entries or []:
            if entry.type == gear_type:
                return True
        return False

    @staticmethod
    def _read_bin_full(data: bytes):
        """Parse a raw bin via temp file (pyRitoFile needs a path)."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tmp = tf.name
            tf.write(data)
        try:
            return pyRitoFile.bin.BIN().read(tmp)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @staticmethod
    def _patch_via_pyritofile(char_lower: str, skin_bin) -> bytes:
        """Fast path: in-memory transforms via pyRitoFile.
        Lossless for non-gear-tier skins (the vast majority).

        pyRitoFile stores entry.type and field.hash as 8-char lowercase
        hex strings (FNV1a32). We compute the same form for our targets.
        """
        from LtMAO.pyRitoFile.helper import FNV1a  # type: ignore
        char_cap = char_lower[:1].upper() + char_lower[1:]

        scdp_type = f"{FNV1a('SkinCharacterDataProperties'):08x}"
        rr_type   = f"{FNV1a('ResourceResolver'):08x}"
        mrr_field = f"{FNV1a('mResourceResolver'):08x}"
        # championSkinName field hash
        skin_name_field = f"{FNV1a('championSkinName'):08x}"

        skin0_scdp = f"{FNV1a(f'Characters/{char_cap}/Skins/Skin0'):08x}"
        skin0_rr   = f"{FNV1a(f'Characters/{char_cap}/Skins/Skin0/Resources'):08x}"

        scdp_entry = None
        for entry in skin_bin.entries or []:
            if entry.type == scdp_type:
                entry.hash = skin0_scdp
                scdp_entry = entry
            elif entry.type == rr_type:
                entry.hash = skin0_rr

        if scdp_entry is None:
            raise RuntimeError(f"{char_lower}: no SkinCharacterDataProperties")

        for field in scdp_entry.data or []:
            if field.hash == mrr_field:
                # mResourceResolver is a link — pyRitoFile stores it as
                # a hex string too.
                field.data = skin0_rr
            elif field.hash == skin_name_field:
                if isinstance(field.data, str) and field.data.lower().startswith(char_lower + "skin"):
                    field.data = field.data[:len(char_lower)]

        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tmp = tf.name
        try:
            skin_bin.write(tmp)
            return Path(tmp).read_bytes()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    def _patch_via_ritobin(self, char_lower: str, skin_raw: bytes, num: int) -> bytes:
        """Slow path: ritobin_cli round-trip + text transforms +
        gear-resource inlining. Required for gear-tier skins where
        pyRitoFile.BIN.write loses bytes."""
        ritobin = self._ritobin_cli_path()
        if ritobin is None:
            raise RuntimeError("ritobin_cli.exe not found in _vendor/")

        char_cap = char_lower[:1].upper() + char_lower[1:]
        with tempfile.TemporaryDirectory() as td:
            in_bin  = Path(td) / "in.bin"
            in_txt  = Path(td) / "in.txt"
            out_bin = Path(td) / "out.bin"
            in_bin.write_bytes(skin_raw)

            r = subprocess.run([str(ritobin), "-d", str(self._cdtb_hash_dir),
                                str(in_bin), str(in_txt)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ritobin bin->text: {r.stderr or r.stdout}")

            text = in_txt.read_text(encoding="utf-8")

            # SCDP / RR / mResourceResolver entry-name renames
            text = re.sub(
                rf'("Characters/{re.escape(char_cap)}/Skins/Skin){num}(" = SkinCharacterDataProperties)',
                r'\g<1>0\g<2>', text)
            text = re.sub(
                rf'("Characters/{re.escape(char_cap)}/Skins/Skin){num}(/Resources" = ResourceResolver)',
                r'\g<1>0\g<2>', text)
            text = re.sub(
                rf'(mResourceResolver: link = "Characters/{re.escape(char_cap)}/Skins/Skin){num}(/Resources")',
                r'\g<1>0\g<2>', text)
            text = re.sub(
                rf'(championSkinName: string = ")\w+Skin{num}(")',
                rf'\g<1>{char_cap}\g<2>', text)

            text = self._inline_gear_resources(text, char_cap, num)

            in_txt.write_text(text, encoding="utf-8")

            r = subprocess.run([str(ritobin), "-d", str(self._cdtb_hash_dir),
                                str(in_txt), str(out_bin)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise RuntimeError(f"ritobin text->bin: {r.stderr or r.stdout}")
            return out_bin.read_bytes()

    def _patch_animation_bin(self, char_lower: str, anim_data: bytes, num: int) -> bytes:
        """Surgical 4-byte FNV1a swap: AnimationGraphData entry name
        Skin<N> -> Skin0. pyRitoFile's parse-write is lossy so we patch
        in place."""
        from LtMAO.pyRitoFile.helper import FNV1a  # type: ignore
        char_cap = char_lower[:1].upper() + char_lower[1:]
        candidates = [
            (f"Characters/{char_cap}/Animations/Skin{num}",
             f"Characters/{char_cap}/Animations/Skin0"),
            (f"characters/{char_lower}/animations/skin{num}",
             f"characters/{char_lower}/animations/skin0"),
        ]
        out = bytearray(anim_data)
        for old, new in candidates:
            old_b = FNV1a(old).to_bytes(4, "little")
            new_b = FNV1a(new).to_bytes(4, "little")
            idx = out.find(old_b)
            if idx >= 0:
                out[idx:idx+4] = new_b
                return bytes(out)
        raise RuntimeError(f"animation entry hash not found: {char_lower}/Skin{num}")

    @staticmethod
    def _inline_gear_resources(text: str, char_cap: str, num: int) -> str:
        """For gear-tier skins, inline the level-1 gear's
        mVFXResourceResolver.resourceMap into the main ResourceResolver,
        and strip mCharacterSubmeshesToShow tokens from
        initialSubmeshToHide so spawn-state shows level-1 weapons."""
        gm = re.search(r'mGearSkinUpgrades:\s*list\[link\]\s*=\s*\{([^}]*)\}', text, re.DOTALL)
        if not gm:
            return text
        m = re.search(r'"([^"]+)"', gm.group(1))
        if not m:
            return text
        first_gear_path = m.group(1)

        marker = f'"{first_gear_path}" = GearSkinUpgrade'
        idx = text.find(marker)
        if idx < 0:
            return text
        bo = text.find('{', idx)
        gear_block = text[bo:SkinBuilder._find_matching_brace(text, bo)]

        rr_idx = gear_block.find('mVFXResourceResolver: pointer = ResourceResolver')
        if rr_idx < 0:
            return text
        rr_bo = gear_block.find('{', rr_idx)
        rr_block = gear_block[rr_bo:SkinBuilder._find_matching_brace(gear_block, rr_bo)]

        rm_idx = rr_block.find('resourceMap: map[hash,link]')
        if rm_idx < 0:
            return text
        rm_bo = rr_block.find('{', rm_idx)
        rm_close = SkinBuilder._find_matching_brace(rr_block, rm_bo)
        gear_resources = rr_block[rm_bo + 1:rm_close - 1]

        # mCharacterSubmeshesToShow tokens (e.g. "Blade1", "Gem1")
        show_tokens: set[str] = set()
        sm = re.search(r'mCharacterSubmeshesToShow:\s*list\[hash\]\s*=\s*\{([^}]*)\}',
                       gear_block, re.DOTALL)
        if sm:
            show_tokens = set(re.findall(r'"([^"]+)"', sm.group(1)))

        # Inject gear resources into main ResourceResolver.resourceMap
        for rr_name in (
            f'"Characters/{char_cap}/Skins/Skin0/Resources" = ResourceResolver',
            f'"Characters/{char_cap}/Skins/Skin{num}/Resources" = ResourceResolver',
        ):
            rr_pos = text.find(rr_name)
            if rr_pos < 0:
                continue
            rm_marker = 'resourceMap: map[hash,link] = {'
            rm_pos = text.find(rm_marker, rr_pos)
            if rm_pos < 0:
                continue
            insert_at = rm_pos + len(rm_marker)
            text = text[:insert_at] + '\n' + gear_resources.rstrip('\n') + '\n' + text[insert_at:]
            break

        # Strip show-tokens from initialSubmeshToHide
        if show_tokens:
            def _strip(m):
                kept = [t for t in m.group(2).split() if t not in show_tokens]
                return m.group(1) + ' '.join(kept) + m.group(3)
            text = re.sub(r'(initialSubmeshToHide: string = ")([^"]*)(")', _strip, text)

        return text

    @staticmethod
    def _find_matching_brace(text: str, start: int) -> int:
        depth = 0
        in_str = False
        i = start
        while i < len(text):
            ch = text[i]
            if ch == '"' and (i == 0 or text[i-1] != '\\'):
                in_str = not in_str
            elif not in_str:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
        return len(text)

    @staticmethod
    def _pack_wad(entries: list[tuple[str, bytes]]) -> bytes:
        """Pack (path, data) entries into a WAD3 binary."""
        wad = pyRitoFile.wad.WAD()
        wad.chunks = [pyRitoFile.wad.WADChunk.default() for _ in entries]
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wad.client") as tf:
            tmp = tf.name
        try:
            wad.write(tmp)
            with pyRitoFile.stream.BytesStream.updater(tmp) as bs:
                for i, chunk in enumerate(wad.chunks):
                    path, data = entries[i]
                    chunk.write_data(bs, i, path, data, previous_chunks=wad.chunks[:i])
                    chunk.free_data()
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

    @staticmethod
    def _ritobin_cli_path() -> Path | None:
        """ritobin_cli.exe lives in _vendor/ alongside LtMAO."""
        cls = SkinBuilder
        cached = getattr(cls, "_ritobin_cached", "__unset__")
        if cached != "__unset__":
            return cached
        path = HERE / "_vendor" / "ritobin_cli.exe"
        cls._ritobin_cached = path if path.exists() else None
        return cls._ritobin_cached


# ----- Entrypoint ----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", required=True, help="League install root")
    ap.add_argument("--out", required=True, help="Output dir")
    ap.add_argument("--only", help="Comma-separated champion keys")
    ap.add_argument("--limit", type=int, help="Max N skins per champion")
    ap.add_argument("--refresh-hashes", action="store_true")
    args = ap.parse_args()

    league = Path(args.league)
    champions_dir = league / "Game" / "DATA" / "FINAL" / "Champions"
    if not champions_dir.exists():
        sys.exit(f"Champions dir not found: {champions_dir}")
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    only = [k.strip() for k in args.only.split(",")] if args.only else None

    print("=" * 60)
    print(f"Sunshine skin builder")
    print(f"  League: {league}")
    print(f"  Out:    {out_dir}")
    if only: print(f"  Only:   {','.join(only)}")
    if args.limit: print(f"  Limit:  {args.limit}")
    print("=" * 60)

    t0 = time.time()
    load_hashes(args.refresh_hashes)
    patch, catalog, chroma_meta = fetch_champion_catalog()
    builder = SkinBuilder(champions_dir, out_dir, catalog, chroma_meta)
    builder._patch = patch
    builder.build_all(only_keys=only, limit=args.limit)
    print(f"\nDone in {time.time() - t0:.1f}s.")


if __name__ == "__main__":
    main()
