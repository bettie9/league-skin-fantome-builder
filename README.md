# league-skin-fantome-builder

Build per-skin `.fantome` mod files from a local League of Legends installation.

Extracts the full champion roster — every skin, chroma, and alternate form — into
ready-to-use fantome packages with a single command.

> **Don't want to build yourself?** Pre-extracted skins for the latest patch are
> available at [**bettie9/LeagueSkins**](https://github.com/bettie9/LeagueSkins).

> **Never pay for free things.** Don't use paid skin changers — skin mods are free and information should be free.

---

## How it works

Each fantome ships a patched `data/characters/<champ>/skins/skin0.bin` (and a
matching `animations/skin0.bin`). The game loads the default skin slot,
and the bin redirects every asset hash to the target skin's textures,
models, and VFX — which are already on disk in the player's WAD.

## Setup

```powershell
pip install -r requirements.txt
```

`_vendor/LtMAO/` (Python WAD/BIN reader) and `_vendor/ritobin_cli.exe`
(bin↔text converter) ship with the repo — no extra downloads needed.

## Usage

```powershell
python build.py `
  --league "C:\Riot Games\League of Legends" `
  --out    .\out
```

First run downloads CommunityDragon hash tables (~50 MB) into
`pref/hashes/cdtb_hashes/` and caches them. Add `--refresh-hashes` after
a League patch to re-pull.

Full roster (170+ champions, all skins + chromas + forms) takes ~3-5 min.

### Flags

| Flag               | Description                                     |
| ------------------ | ----------------------------------------------- |
| `--league <path>`  | League of Legends install root (required)       |
| `--out <dir>`      | Output directory (required)                     |
| `--only Ahri,Nami` | Comma-separated champion keys; build only these |
| `--limit N`        | Max N skins per champion (for quick tests)      |
| `--refresh-hashes` | Force re-download of CDragon hash tables        |

### Quick test

```powershell
python build.py --league "C:\Riot Games\League of Legends" --out .\out --only Katarina --limit 8
```

## Output

```
out/
  index.json
  skins/
    Ahri/
      Foxfire Ahri.fantome
    Katarina/
      Battle Queen Katarina.fantome
      Battle Queen Katarina/
        Ruby.fantome              # chroma
        Sapphire.fantome
        Emerald.fantome
        ...
```

`index.json`:

```json
{
  "patch": "15.x.y",
  "champions": {
    "Katarina": {
      "skins": { "29": "Battle Queen Katarina" },
      "chromas": { "29": { "30": "Ruby", "31": "Sapphire", "32": "Emerald" } },
      "forms": {}
    }
  }
}
```

Each `.fantome` is a standard zip:

```
META/info.json
WAD/<Champion>.wad.client       # WAD3 binary holding patched skin0.bin
                                # + animations/skin0.bin
```

## Credits

- **[LtMAO](https://github.com/tarngaina/LtMAO)** by Tarngaina — vendored
  as `_vendor/LtMAO/`. Provides the WAD/BIN Python reader and the
  baseline skin0-swap (`no_skin`) technique.
- **[ritobin](https://github.com/moonshadow565/ritobin)** by moonshadow565
  — `ritobin_cli.exe` is the byte-perfect bin↔text converter the
  pipeline relies on.
- **[CommunityDragon](https://communitydragon.org)** — champion/skin
  catalog and WAD hash tables.

## Disclaimer

This is an **educational project**. It is not affiliated with, endorsed
by, or associated with Riot Games in any way. All game assets remain
the property of Riot Games.

## License

MIT — see [LICENSE](LICENSE).

---

<p align="center">
  Powered by <a href="https://github.com/bettie9/Sunshine"><b>Sunshine</b></a>
</p>
