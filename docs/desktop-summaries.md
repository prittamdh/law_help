# Free AI summaries on your own PC

This runs the whole pipeline on a Windows gaming PC, with summaries written by a local
model through [Ollama](https://ollama.com). It costs nothing beyond electricity. It was
written for a Ryzen 5 5600 with 32 GB of RAM and a Radeon RX 9060 XT (16 GB).

Ollama runs on Windows, where it can use the graphics card. law_help runs in WSL (Ubuntu
inside Windows), because the text extractor uses Linux process pools.

## 1. Ollama on the graphics card (Windows)

1. Update Ollama to the latest version from <https://ollama.com/download>. The RX 9000
   series is new, and older versions fall back to the CPU.
2. In PowerShell:

   ```powershell
   ollama pull qwen3:14b
   ollama run qwen3:14b "Reply with OK"
   ollama ps
   ```

   The `PROCESSOR` column of `ollama ps` should say `100% GPU`. If it says `CPU`, Ollama's
   ROCm build doesn't support the card yet. Turn on its Vulkan backend instead: add a user
   environment variable `OLLAMA_VULKAN` = `1`, quit Ollama from the system tray, start it
   again, and check `ollama ps` once more.

Why `qwen3:14b`: it is the strongest model that fits comfortably in 16 GB. The model is
about 9 GB, which leaves room for the 16,000-token context the summarizer asks for. It
follows English legal text well and fills the summary format reliably. If it is too slow,
`qwen3:8b` is about twice as fast with somewhat weaker summaries. Set
`LAW_HELP_OLLAMA_MODEL=qwen3:8b` to use it.

## 2. Let WSL reach Ollama

Create or edit `%UserProfile%\.wslconfig` so that `localhost` inside WSL is the Windows
`localhost`:

```ini
[wsl2]
networkingMode=mirrored
```

Then run `wsl --shutdown` in PowerShell.

## 3. law_help in WSL

```powershell
wsl --install -d Ubuntu      # once; restart if asked, then open "Ubuntu" from the Start menu
```

Inside Ubuntu:

```bash
sudo apt update && sudo apt install -y postgresql python3-venv git
sudo service postgresql start
sudo -u postgres psql -c "CREATE USER law WITH PASSWORD 'law' SUPERUSER"
sudo -u postgres createdb -O law law_help

git clone https://github.com/prittamdh/law_help && cd law_help
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

curl -s localhost:11434/api/tags | head -c 200    # should list qwen3:14b
```

## 4. Import judgments and extract text (free)

```bash
python -m law_help.importer metadata              # every year, about 1.1 million judgments
python -m law_help.importer text --year 2024      # try one year first
python -m law_help.importer text                  # then everything; stop and re-run any time
```

The full run downloads about 79 GB, which is streamed and not kept, and needs about 12 GB
for the database. On a 6-core CPU it should take roughly 2 to 3 hours, or longer if the
internet connection is slower than about 100 Mbit/s. This step also writes each
judgment's headline, extractive summary and, for long judgments, a key passage. None of
that needs a model.

## 5. Write summaries

```bash
python -m law_help.summarize --min-chars 8000 --limit 50      # a few long ones first
uvicorn law_help.api:app                                      # read them at http://localhost:8000
python -m law_help.summarize --min-chars 8000 --limit 100000  # then every long judgment
```

Each finished summary is saved as soon as it is written. You can stop with Ctrl+C and run
the same command again later, and it continues with the ones not yet done. Newest
judgments go first.

### How long it takes

These are estimates from the card's memory speed, not a measurement:

| What | qwen3:14b | qwen3:8b |
| --- | --- | --- |
| A typical short order | ~15 s | ~8 s |
| A long judgment (over 8,000 characters) | ~35 s | ~20 s |
| All long judgments, about 80,000 | ~9 days of running | ~5 days |
| Everything, about 1 million | ~6 months | ~3 months |
| New judgments each day, long ones only | ~15 minutes | ~8 minutes |

Summarizing only the long judgments is the useful part. The short orders already get an
accurate headline and summary from the rules for free. Running overnight works fine.

For judgments over about 40,000 characters (roughly 2%), the model sees the first and
last 20,000 characters, which are the facts and the reasons and order. The middle is
left out.
