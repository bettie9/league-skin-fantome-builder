"""
Sunshine skin builder — extracts every champion skin from a local League of
Legends installation into per-skin .fantome mod files using LtMAO's bin-swap
technique.

Output layout:

    out/
      skins/
        Ahri/
          Foxfire Ahri.fantome
          ...
      index.json

Usage:
    python build.py \
        --league "C:\\Riot Games\\League of Legends" \
        --out    .\\out \
        [--only Ahri,Nami]    # comma-separated champion keys
        [--limit 3]           # max N skins per champion
        [--refresh-hashes]    # re-download CDragon hashes
"""

from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).parent.resolve()

# ----- Vendored LtMAO ------------------------------------------------------
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


# ----- Champion + skin catalog (CommunityDragon) ----------------------------
# Skin ID encoding: base = championId, skin N = championId*1000 + N
CDRAGON_VERSIONS = "https://ddragon.leagueoflegends.com/api/versions.json"
CDRAGON_BASE     = "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default"
CDRAGON_SUMMARY  = f"{CDRAGON_BASE}/v1/champion-summary.json"
CDRAGON_SKINS    = f"{CDRAGON_BASE}/v1/skins.json"


def http_json(url: str):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Sunshine-skin-builder/0.1"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def fetch_champion_catalog() -> tuple[str, dict[str, dict[int, str]], dict[str, dict[int, dict]]]:
    """Returns (patch, {championKey: {skinNum: skinDisplayName}}, chroma_meta)."""
    try:
        patch = http_json(CDRAGON_VERSIONS)[0]
    except Exception as e:
        print(f"  ! patch fetch failed ({e}) — falling back to 'latest'")
        patch = "latest"

    summary = http_json(CDRAGON_SUMMARY)
    skins   = http_json(CDRAGON_SKINS)
    id_to_key: dict[int, str] = {
        int(c["id"]): c["alias"] for c in summary if int(c["id"]) > 0
    }
    print(f"[catalog] patch {patch} — {len(id_to_key)} champions, {len(skins)} skin entries")

    catalog: dict[str, dict[int, str]] = {key: {} for key in id_to_key.values()}
    for sid_str, sinfo in skins.items():
        try:
            sid = int(sid_str)
        except (ValueError, TypeError):
            continue
        # Derive (champion_id, skin_num) from the encoded id.
        if sid < 1000:
            champ_id, skin_num = sid, 0
        else:
            champ_id, skin_num = sid // 1000, sid % 1000
        key = id_to_key.get(champ_id)
        if key is None or skin_num == 0:
            continue  # unknown champion or base skin (doesn't need a mod)
        catalog[key][skin_num] = sinfo.get("name") or f"{key} skin {skin_num}"

    # Fetch per-champion chroma + form metadata in parallel.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    chroma_total = 0
    form_total   = 0
    chroma_meta: dict[str, dict[int, dict]] = {key: {} for key in id_to_key.values()}
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
                    skin_num = cid_full % 1000
                    if skin_num in catalog[key]:
                        continue  # already known as a base skin
                    full_name  = chroma.get("name") or f"{parent_name} (Chroma {skin_num})"
                    short = full_name
                    if "(" in short and short.endswith(")"):
                        short = short.rsplit("(", 1)[1].rstrip(")").strip()
                    catalog[key][skin_num] = full_name
                    chroma_meta[key][skin_num] = {
                        "parent_num":  parent_num,
                        "parent_name": parent_name,
                        "short_name":  short or full_name,
                        "kind":        "chroma",
                    }
                    chroma_total += 1
                # Alternate forms (questSkinInfo.tiers)
                qsi = s.get("questSkinInfo") or {}
                tiers = qsi.get("tiers") or []
                if len(tiers) >= 2:
                    for t in tiers:
                        tid = int(t.get("id") or 0)
                        if tid < 1000 or tid // 1000 != cid:
                            continue
                        tnum = tid % 1000
                        if tnum == parent_num:
                            continue  # base skin, already in catalog
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

    print(f"[catalog] +{chroma_total} chromas, +{form_total} forms merged into per-champion skin lists")

    for k in sorted(catalog):
        if catalog[k]:
            print(f"  · {k}: {len(catalog[k])} skins")
    return patch, catalog, chroma_meta


# ----- LtMAO setup ---------------------------------------------------------

def load_hashes(refresh: bool):
    """Download and load CDragon WAD/BIN hashes into LtMAO's Storage."""
    cache = WORK_DIR / "pref" / "hashes" / "cdtb_hashes"
    if refresh and cache.exists():
        for f in cache.iterdir():
            f.unlink()
        print(f"[hashes] cleared cache at {cache}")
    print("[hashes] syncing WAD + BIN hashes from CommunityDragon (cached after first run)...")
    CDTBHashes.sync_all()
    CustomHashes.read_all_hashes()
    print(
        f"[hashes] loaded "
        f"game={len(Storage.hashtables['hashes.game.txt'])} "
        f"entries={len(Storage.hashtables['hashes.binentries.txt'])}"
    )


# ----- Core: per-skin fantome builder --------------------------------------

class SkinBuilder:
    def __init__(self, champions_dir: Path, output_dir: Path, catalog: dict,
                 chroma_meta: dict[str, dict[int, dict]] | None = None):
        self.champions_dir = champions_dir
        self.output_dir = output_dir
        self.catalog = catalog
        self.chroma_meta = chroma_meta or {}
        # Pre-filter hashes to just the per-character skin bins.
        all_game = hash_helper.Storage.hashtables["hashes.game.txt"]
        self.skin_bin_hashes: dict[int, str] = {}
        for h, path in all_game.items():
            pl = path.lower()
            if pl.endswith(".bin") and "data/characters/" in pl and "/skins/" in pl and "root.bin" not in pl:
                self.skin_bin_hashes[h] = path
        # binentries hashes filtered to Characters/.../Skins/
        all_be = hash_helper.Storage.hashtables["hashes.binentries.txt"]
        self.scdp_skin0_hashes: set[int] = set()
        self.scdp_other_hashes: set[int] = set()
        for h, value in all_be.items():
            if "Characters/" in value and "/Skins/" in value and "/Root" not in value:
                if "/Skin0" in value:
                    self.scdp_skin0_hashes.add(h)
                else:
                    self.scdp_other_hashes.add(h)
        print(
            f"[builder] {len(self.skin_bin_hashes)} skin-bin paths · "
            f"{len(self.scdp_skin0_hashes)} skin0 SCDP / "
            f"{len(self.scdp_other_hashes)} skinN SCDP hashes"
        )

    def build_all(self, only_keys: list[str] | None, limit: int | None):
        wads = sorted(self.champions_dir.glob("*.wad.client"))
        def champ_key_of(p: Path) -> str:
            return p.name.split(".", 1)[0]
        # Keep only main WAD per champion (exclude locale/TFT variants)
        wads = [
            w for w in wads
            if w.name.count(".") == 2          # `<Name>.wad.client`
            and "_" not in champ_key_of(w)     # no Champion_TFT etc.
        ]
        print(f"[builder] {len(wads)} champion WADs found")
        index: dict[str, dict[int, str]] = {}
        for wad_path in wads:
            champ_key = champ_key_of(wad_path)
            if only_keys and champ_key not in only_keys:
                continue
            built = self.build_champion(wad_path, champ_key, limit)
            if built:
                index[champ_key] = built
        # Write index.json
        idx_champions: dict[str, dict] = {}
        for k, v in index.items():
            cmap = self.chroma_meta.get(k, {})
            base_skins: dict[str, str] = {}
            chromas_by_parent: dict[str, dict[str, str]] = {}
            forms_by_parent:   dict[str, dict[str, str]] = {}
            for n, name in v.items():
                meta = cmap.get(n)
                if meta:
                    bucket = forms_by_parent if meta.get("kind") == "form" else chromas_by_parent
                    bucket.setdefault(str(meta["parent_num"]), {})[str(n)] = meta["short_name"]
                else:
                    base_skins[str(n)] = name
            idx_champions[k] = {
                "skins":   base_skins,
                "chromas": chromas_by_parent,
                "forms":   forms_by_parent,
            }
        idx_file = self.output_dir / "index.json"
        idx_file.write_text(json.dumps(
            {"patch": getattr(self, "_patch", "unknown"), "champions": idx_champions},
            indent=2, ensure_ascii=False,
        ), encoding="utf-8")
        print(f"[builder] wrote {idx_file}")

    def build_champion(self, wad_path: Path, champ_key: str, limit: int | None) -> dict[int, str]:
        """Returns map of {skinNum: skinDisplayName} actually built."""
        names = self.catalog.get(champ_key)
        if not names:
            print(f"  - {champ_key}: no DataDragon catalog entry, skipping")
            return {}

        skips = no_skin.SKIPS.get(champ_key.lower())

        wad = pyRitoFile.wad.WAD().read(str(wad_path))
        wad.un_hash({"hashes.game.txt": self.skin_bin_hashes})

        # WADs bundle the main champion + aux characters (e.g. ZedShadow,
        # AnnieTibbers). We patch all of them for a complete skin swap.
        champ_lower = champ_key.lower()
        characters: dict[str, dict] = {}  # char_lower -> {"skin0": chunk, "skinN": {num: chunk}}
        for chunk in wad.chunks:
            if chunk.extension != "bin":
                continue
            parts = chunk.hash.lower().split("/")
            # data/characters/<char>/skins/<skinX>.bin
            if (len(parts) < 5 or parts[0] != "data"
                    or parts[1] != "characters" or parts[3] != "skins"):
                continue
            skinx = parts[4]
            if not skinx.endswith(".bin"):
                continue
            char = parts[2]
            base = skinx[:-4]
            ent = characters.setdefault(char, {"skin0": None, "skinN": {}})
            if base == "skin0":
                ent["skin0"] = chunk
            else:
                try:
                    n = int(base.removeprefix("skin"))
                except ValueError:
                    continue
                ent["skinN"][n] = chunk

        if champ_lower not in characters or not characters[champ_lower]["skin0"]:
            print(f"  ! {champ_key}: no skin0.bin found, can't bin-swap")
            return {}

        # Extract SCDP + ResourceResolver hashes from each character's skin0.
        char_skin0_hashes: dict[str, tuple] = {}
        char_skin0_bins:   dict[str, object] = {}
        for char_lower2, info in characters.items():
            if info["skin0"] is None:
                continue
            with pyRitoFile.stream.BytesStream.reader(str(wad_path)) as bs:
                info["skin0"].read_data(bs)
                try:
                    s0 = self._read_bin_full(info["skin0"].data)
                finally:
                    info["skin0"].free_data()
            scdp_h = rr_h = None
            for entry in s0.entries or []:
                if entry.type == hash_helper.Storage.bin_hashes["SkinCharacterDataProperties"]:
                    scdp_h = entry.hash
                    for field in entry.data:
                        if field.hash == hash_helper.Storage.bin_hashes["mResourceResolver"]:
                            rr_h = field.data
                            break
                elif entry.type == hash_helper.Storage.bin_hashes["ResourceResolver"]:
                    if rr_h is None:
                        rr_h = entry.hash
            if scdp_h is not None:
                char_skin0_hashes[char_lower2] = (scdp_h, rr_h)
                char_skin0_bins[char_lower2] = s0

        if champ_lower not in char_skin0_hashes:
            print(f"  ! {champ_key}: skin0.bin has no SkinCharacterDataProperties, can't swap")
            return {}

        main_skinN = characters[champ_lower]["skinN"]
        built: dict[int, str] = {}
        out_champ_dir = self.output_dir / "skins" / champ_key
        out_champ_dir.mkdir(parents=True, exist_ok=True)
        count = 0
        for num, _chunk in sorted(main_skinN.items()):
            if num not in names:
                continue  # DDragon doesn't list this skin (rare orphan)
            display = names[num]
            if skips:
                if skips == "all":
                    print(f"  · {champ_key} skin{num} ({display}): SKIP (champion blocked)")
                    continue
                if isinstance(skips, list) and f"skin{num}.bin" in skips:
                    print(f"  · {champ_key} skin{num} ({display}): SKIP (per-skin blocked)")
                    continue
            try:
                self._build_one(
                    wad_path, champ_key, num, display,
                    characters, char_skin0_hashes, char_skin0_bins, out_champ_dir,
                )
                built[num] = display
                count += 1
                if limit and count >= limit:
                    break
            except Exception as e:
                print(f"  ! {champ_key} skin{num} ({display}): build failed: {e}")
        print(f"  · {champ_key}: built {len(built)} fantomes -> {out_champ_dir}")
        return built

    def _build_one(self, wad_path, champ_key, num, display,
                   characters, hashes_by_char, skin0_bins, out_dir):
        """Build a single .fantome for the given skin number."""
        patched: list[tuple[str, bytes]] = []

        for char_lower, info in characters.items():
            if num not in info["skinN"]:
                continue  # this character has no variant for this skin number
            if char_lower not in hashes_by_char:
                continue  # no usable skin0 reference for this aux
            scdp_h, rr_h = hashes_by_char[char_lower]
            skin0_bin = skin0_bins.get(char_lower)
            chunk = info["skinN"][num]
            with pyRitoFile.stream.BytesStream.reader(str(wad_path)) as bs:
                chunk.read_data(bs)
                try:
                    skin_bin = self._read_bin_full(chunk.data)
                finally:
                    chunk.free_data()
            try:
                patched_bytes = self._patch_skin_bin(
                    char_lower, skin_bin, skin0_bin, scdp_h, rr_h,
                    main_champ_key=champ_key, num=num,
                )
            except RuntimeError as e:
                print(f"      · {champ_key} skin{num}: skip {char_lower} ({e})")
                continue
            inner = f"data/characters/{char_lower}/skins/skin0.bin"
            patched.append((inner, patched_bytes))

        if not patched:
            raise RuntimeError("no characters patched (main champion bin missing?)")

        def _safe(s: str) -> str:
            return s.replace("/", "_").replace("\\", "_").replace(":", "").strip()
        chroma_info = self.chroma_meta.get(champ_key, {}).get(num)
        if chroma_info:
            parent_dir = out_dir / _safe(chroma_info["parent_name"])
            parent_dir.mkdir(parents=True, exist_ok=True)
            file_label = _safe(chroma_info["short_name"])
            out_path = parent_dir / f"{file_label}.fantome"
        else:
            file_label = _safe(display)
            out_path = out_dir / f"{file_label}.fantome"
        meta = {
            "Name":        f"{display}",
            "Author":      "Sunshine Builder",
            "Version":     "1.0.0",
            "Description": f"Auto-built by Sunshine Builder.",
        }
        wad_name = wad_path.name
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=3) as zf:
            zf.writestr("META/info.json", json.dumps(meta, indent=2))
            for inner, data in patched:
                zf.writestr(f"WAD/{wad_name}/{inner}", data)
        rel = out_path.relative_to(self.output_dir)
        print(f"    + {champ_key} skin{num} ({display}) -> {rel} ({out_path.stat().st_size} bytes, {len(patched)} char)")

    @staticmethod
    def _read_bin_full(data: bytes):
        """Parse a .bin from raw bytes via temp file (LtMAO needs a path)."""
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

    def _patch_skin_bin(self, char_lower: str, skin_bin, skin0_bin, skin0_scdp_hash, skin0_rr_hash,
                        main_champ_key: str, num: int) -> bytes:
        """Apply bin-swap transforms: rewrite SCDP/RR hashes to skin0's,
        strip object-name suffix, and overlay skin0 entries."""
        scdp_entry = mrr_field = rr_entry = None
        for entry in skin_bin.entries:
            if entry.type == hash_helper.Storage.bin_hashes["SkinCharacterDataProperties"]:
                scdp_entry = entry
                for field in entry.data:
                    if field.hash == hash_helper.Storage.bin_hashes["mResourceResolver"]:
                        mrr_field = field
                        break
            elif entry.type == hash_helper.Storage.bin_hashes["ResourceResolver"]:
                rr_entry = entry
        if scdp_entry is None:
            raise RuntimeError(f"{char_lower}: bin has no SkinCharacterDataProperties")

        scdp_entry.hash = skin0_scdp_hash
        if rr_entry is not None and skin0_rr_hash is not None:
            rr_entry.hash = skin0_rr_hash
        if mrr_field is not None and skin0_rr_hash is not None:
            mrr_field.data = skin0_rr_hash

        # Strip skin number from object-name: "ZedSkin38" -> "Zed"
        SKIN_OBJECT_NAME_FIELD = 0x2d78c328
        for field in scdp_entry.data or []:
            if field.type != pyRitoFile.bin.BINType.STRING:
                continue
            if not isinstance(field.data, str):
                continue
            fh = field.hash
            try:
                fh_int = fh if isinstance(fh, int) else int(str(fh), 16)
            except ValueError:
                continue
            if fh_int != SKIN_OBJECT_NAME_FIELD:
                continue
            if field.data.lower().startswith(char_lower + "skin"):
                field.data = field.data[:len(char_lower)]

        # Overlay skin0's entries that skin<N> doesn't override.
        if skin0_bin is not None:
            patched_hashes = {e.hash for e in skin_bin.entries}
            merged_entries = list(skin_bin.entries)
            for e0 in (skin0_bin.entries or []):
                if e0.hash not in patched_hashes:
                    merged_entries.append(e0)
            skin_bin.entries = merged_entries

        # Serialize patched bin.
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as tf:
            tmp_bin = tf.name
        try:
            skin_bin.write(tmp_bin)
            with open(tmp_bin, "rb") as f:
                return f.read()
        finally:
            try:
                os.unlink(tmp_bin)
            except OSError:
                pass


# ----- Entrypoint ----------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Build per-skin fantome files from local League WADs.")
    ap.add_argument("--league", required=True, help="League of Legends install root (e.g. C:\\Riot Games\\League of Legends)")
    ap.add_argument("--out", required=True, help="Output directory for fantome files + index.json")
    ap.add_argument("--only", help="Comma-separated champion keys to build (default: all)")
    ap.add_argument("--limit", type=int, help="Build at most N skins per champion (for smoke tests)")
    ap.add_argument("--refresh-hashes", action="store_true", help="Re-download CDragon hash files")
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
    print(f"  League:    {league}")
    print(f"  Champions: {champions_dir}")
    print(f"  Output:    {out_dir}")
    if only:
        print(f"  Only:      {','.join(only)}")
    if args.limit:
        print(f"  Limit:     {args.limit} skins/champion")
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
