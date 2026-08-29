# Telegram Business Auto-Reply Bot ("John")

An AI persona that instantly auto-replies to messages sent to your personal
Telegram account via **Telegram Business** — powered by FastAPI + a webhook,
with either OpenAI or Anthropic as the reply engine.

## How it works

1. You connect this bot to your Telegram account under **Settings → Telegram
   Business → Chatbots**.
2. Telegram forwards every message in your connected chats to the bot's
   webhook as a `business_message` update (this includes messages *you* send
   from your own phone, and the bot's own replies — see Loop Protection).
3. The bot filters out anything that isn't a genuine incoming message from
   the other party, sends the real ones to the configured AI model with
   short per-chat conversation history, and replies on your behalf via
   `sendMessage` with the connection's `business_connection_id`.

## Files

| File | Purpose |
|---|---|
| `config.py` | Loads and validates all configuration from env vars |
| `memory.py` | In-memory per-chat conversation history (`defaultdict(deque)`) |
| `ai_client.py` | Raw-HTTP client for OpenAI Responses API / Anthropic Messages API |
| `handlers.py` | Loop-protection logic (`should_reply`) + owner-id recovery |
| `main.py` | FastAPI app, webhook route, startup registration |
| `requirements.txt` | Python dependencies |
| `Procfile` | Railway/Heroku process definition |
| `runtime.txt` | Pins the Python version |

## Loop protection (read this before deploying)

A Telegram Business connection delivers **every** message in a connected
chat to your webhook — not just messages from the other person. Without
filtering, the bot would see its own replies and reply to itself forever.
`handlers.MessageHandler.should_reply()` is the single choke point and skips
a message if **any** of the following is true:

1. `sender.id == bot_id` — it's the bot's own account.
2. `sender.id == chat.id` — heuristic for "this message was authored by the
   business account itself" in a 1:1 chat.
3. `message.sender_business_bot` is set — Telegram marks messages sent by a
   bot acting on behalf of the business account this way.
4. `sender.id == owner_id` — your own outgoing message, typed by hand from
   your phone/desktop. The owner id is either the configured
   `OWNER_TELEGRAM_USER_ID`, or recovered from `getBusinessConnection` the
   first time a message arrives on a given `business_connection_id` — this
   works even after a restart, with no database, because the connection id
   itself is stable and Telegram will re-supply the owner on request.
5. `sender.is_bot` — ignore other bots in the chat.

Only messages that pass all five checks are sent to the AI and replied to.

## Environment variables

| Variable | Required | Default | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | — | From [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_WEBHOOK_SECRET` | yes | — | Min 20 chars; verified against `X-Telegram-Bot-Api-Secret-Token` |
| `AI_PROVIDER` | no | `openai` | `openai` or `anthropic` |
| `OPENAI_API_KEY` | if provider=openai | — | |
| `ANTHROPIC_API_KEY` | if provider=anthropic | — | |
| `OPENAI_MODEL` | no | `gpt-4o-mini` | |
| `ANTHROPIC_MODEL` | no | `claude-sonnet-5` | See note below |
| `OWNER_TELEGRAM_USER_ID` | strongly recommended | — | Your numeric Telegram id (get from [@userinfobot](https://t.me/userinfobot)) |
| `WEBHOOK_URL` | no | auto from `RAILWAY_PUBLIC_DOMAIN` | Full base URL, no trailing slash/path |
| `PORT` | no | `8000` | Railway sets this automatically |
| `SYSTEM_PROMPT` | no | John persona (see `config.py`) | Override the default persona |
| `MAX_HISTORY_PER_CHAT` | no | `20` | Deque maxlen per chat (messages, not pairs) |

> **Note on `ANTHROPIC_MODEL` default:** the model string is set to
> `claude-sonnet-5`, a currently-supported model. Older date-suffixed model
> IDs (e.g. `claude-3-5-sonnet-20241022`) get retired over time — if you
> pin a specific dated snapshot, confirm it's still live in the Anthropic
> docs before deploying.

## Deployment (Railway)

1. **Push to GitHub**

   ```bash
   git init
   git add .
   git commit -m "Telegram Business auto-reply bot"
   git branch -M main
   git remote add origin https://github.com/<you>/<repo>.git
   git push -u origin main
   ```

2. **Deploy on Railway**
   - [railway.app](https://railway.app) → New Project → Deploy from GitHub repo → select your repo.
   - Railway detects `Procfile` + `runtime.txt` automatically.

3. **Generate a public domain**
   - Project → your service → **Settings → Networking → Generate Domain**.
   - This becomes `RAILWAY_PUBLIC_DOMAIN`, which `config.py` uses to build
     `WEBHOOK_URL` automatically if you don't set `WEBHOOK_URL` yourself.

4. **Set environment variables**
   - Project → your service → **Variables** → add everything from the table
     above (at minimum: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`,
     `AI_PROVIDER`, the matching API key, `OWNER_TELEGRAM_USER_ID`).

5. **Check the logs**
   - Deployments → View Logs. On startup you should see:
     ```
     Authenticated as @your_bot_username (id=...)
     Webhook registered at https://your-app.up.railway.app/webhook (result=True)
     ```
   - If you don't see these, the bot is silent because webhook registration
     failed — check `TELEGRAM_BOT_TOKEN` and that a domain was generated
     before this deploy (redeploy after generating the domain if needed).

6. **Connect it on Telegram**
   - Telegram mobile app → **Settings → Telegram Business → Chatbots**.
   - Add your bot, enable **"Reply to messages"** (and any other permissions
     you want it to have).

7. **Send a test message from a second account**
   - Watch the logs for:
     ```
     Recovered business connection <id>: owner_id=<your id> can_reply=True
     ```
     (only appears if `OWNER_TELEGRAM_USER_ID` wasn't set, since the code
     short-circuits to the configured value otherwise)
   - The other account should get an instant AI reply as "John".
   - Send a message *from your own account* in the same chat and confirm the
     bot does **not** reply to it (loop protection working).

## Local development

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env  # fill in values
uvicorn main:app --reload --port 8000
```

Webhook registration requires a publicly reachable HTTPS URL — for local
testing, tunnel with `ngrok http 8000` (or similar) and set `WEBHOOK_URL` to
the tunnel URL before starting the server.

## Troubleshooting

- **Bot never replies, no errors anywhere:** almost always missing
  `allowed_updates: ["business_message", "message"]` on `setWebhook` —
  already handled in `main.py`, but double-check the startup log line
  confirms webhook registration succeeded.
- **Bot replies to itself in a loop:** confirm `OWNER_TELEGRAM_USER_ID` is
  set correctly, or check the "Recovered business connection" log line for
  the right owner id.
- **401 on `/webhook`:** `TELEGRAM_WEBHOOK_SECRET` in your environment
  doesn't match what was registered with Telegram — redeploy after changing
  it so `setWebhook` re-registers with the new secret.
- **OpenAI o-series model errors about `temperature`:** already handled —
  `ai_client.py` drops `temperature` automatically for models starting with
  `o1`/`o3`/`o4`.
