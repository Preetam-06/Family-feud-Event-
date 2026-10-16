# Family Feud — Tournament (Python, zero dependencies)

    python app.py          # Python 3.9+, nothing to install

    docker compose up --build   # Docker, state persisted in ./data

- Host:   http://localhost:8000/admin   (PIN = env `ADMIN_PIN`, default `feud2026`)
- Screen: http://localhost:8000/screen  (open on the projector, press **F** for full screen)
- Other devices on the same Wi-Fi: use the LAN address printed at startup.

## Run with Docker Compose

Docker stores tournament state in the local `data/` folder.

```bash
# Build the image and start the server
docker compose up --build

# Start in the background
docker compose up -d --build

# View live logs
docker compose logs -f

# Stop the server
docker compose down
```

Set a custom admin PIN before starting:

```bash
ADMIN_PIN=my-secure-pin docker compose up -d --build
```

Open the admin page at http://localhost:8000/admin and the screen at http://localhost:8000/screen.

## Running a match
1. **Matches** → Start Semifinal 1. The screen plays a VS intro.
2. **Live control**: `1`–`6` = correct answer, `X` = wrong (strike), `N` = next question.
   3 strikes → the other team gets a steal. Undo, Pause and ± Score are on the page.
3. After the last question → **Finish match**. The winner moves into the Grand Final automatically.
4. Ties can't be finished: use **Start tie-break question** (uses an unused enabled question).

## Data (all JSON, in `data/`)
`tournament.json` (teams, matches, scores, question snapshots, champion) · `questions.json` · `audit.json` · `backups/`.
Writes are atomic (temp file + rename) and keep a `.bak`. If the server dies, restart it: the live match resumes exactly where it was.
A corrupted file is never reset silently — the server stops and tells you to restore from `data/backups/`.

## Test
    python tests/smoke.py     # plays a whole tournament over HTTP

Run ONE server instance only (JSON files are not multi-process safe).

## Deploy to Vercel

The repository includes `vercel.json` and `api/index.py`, so it can be imported directly into Vercel or deployed with the Vercel CLI:

```bash
vercel login
vercel --prod
```

Set `ADMIN_PIN` in Vercel Project Settings before the event. Vercel serverless instances have temporary, instance-local storage; for a multi-host production event, use the local/Docker deployment or connect the state layer to a durable database before relying on server restarts or simultaneous hosts.
