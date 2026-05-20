# league-skin-fantome-builder

Build per-skin `.fantome` mod files from a local League of Legends installation.

Extracts the full champion roster — every skin, chroma, and alternate form — into
ready-to-use fantome packages with a single command.

> **Don't want to build yourself?** Pre-extracted skins for the latest patch are
> available at [**bettie9/LeagueSkins**](https://github.com/bettie9/LeagueSkins).

> **Never pay for free things.** Don't use paid skin changers — skin mods are free and information should be free.

---

## How it works

Each fantome contains only a patched `skin0.bin`. The game loads the default
skin slot and the bin redirects every asset hash to the target skin's textures,
models, and VFX — which are already on disk in the player's WAD. 

## Setup

```powershell
pip install -r requirements.txt
```

The trimmed `_vendor/LtMAO/` sources are included — no extra downloads needed.

## Usage

```powershell
python build.py `
  --league "C:\Riot Games\League of Legends" `
  --out    .\out
```

First run downloads CommunityDragon hash tables (~50 MB) into
`pref/hashes/cdtb_hashes/` and caches them. Add `--refresh-hashes` after a
League patch to re-pull.

Full roster (170+ champions, all skins + chromas + forms) takes ~3-5 min.

### Flags

| Flag | Description |
|---|---|
| `--league <path>` | League of Legends install root (required) |
| `--out <dir>` | Output directory (required) |
| `--only Ahri,Nami` | Comma-separated champion keys; build only these |
| `--limit N` | Max N skins per champion (for quick tests) |
| `--refresh-hashes` | Force re-download of CDragon hash tables |

### Quick test

```powershell
python build.py --league "C:\Riot Games\League of Legends" --out .\out --only Nami --limit 2
```

## Output

```
out/
  index.json
  skins/
    Ahri/
      Foxfire Ahri.fantome
      Risen Legend Ahri/
        Obsidian.fantome       # chroma
        Ruby.fantome
    Nami/
      River Spirit Nami.fantome
```

`index.json`:

```json
{
  "patch": "15.x.y",
  "champions": {
    "Ahri": {
      "skins":   { "1": "Foxfire Ahri" },
      "chromas": { "1": { "11": "Obsidian", "12": "Ruby" } },
      "forms":   {}
    }
  }
}
```

Each `.fantome` is a standard zip:

```
META/info.json
WAD/<Champion>.wad.client/data/characters/<champion>/skins/skin0.bin
```

## Credits

- **[LtMAO](https://github.com/tarngaina/LtMAO)** by Tarngaina — bin parsing
  and the original skin0-swap (no_skin) technique.
- **[CommunityDragon](https://communitydragon.org)** — champion/skin catalog
  and WAD hash tables.

## Disclaimer

This is an **educational project**. It is not affiliated with, endorsed by, or
associated with Riot Games in any way. All game assets remain the property of
Riot Games.

## License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  Powered by <a href="https://github.com/bettie9/Sunshine"><b>Sunshine</b></a>
</p>
