# Vera — magicpin Merchant AI Assistant

> magicpin AI Challenge submission — Build a Merchant AI Assistant (Vera)

## What This Bot Does

Vera is a production-grade FastAPI bot that engages merchants on WhatsApp using 4-context AI composition:

```
compose(category, merchant, trigger, customer?) → WhatsApp message
```

It receives context pushes from the magicpin judge harness, processes tick events to send proactive messages, and handles multi-turn conversations with merchants and customers.

---

## Architecture

```
vera_bot/
├── app/
│   ├── main.py              # FastAPI app, lifespan, LLM init
│   ├── api/
│   │   └── routes.py        # All 5 endpoints + teardown
│   ├── core/
│   │   ├── config.py        # Settings from env vars
│   │   ├── context_store.py # Thread-safe in-memory context store (versioned)
│   │   └── conversation.py  # Conversation state, intent detection, auto-reply
│   ├── llm/
│   │   └── providers.py     # LLM abstraction (Gemini/OpenAI/Anthropic/DeepSeek/Groq)
│   └── services/
│       ├── composer.py      # Main AI composer (LLM + fallback)
│       ├── fallback_composer.py  # Deterministic templates for all 25 trigger kinds
│       ├── tick_service.py  # Tick logic: trigger prioritization & dedup
│       └── reply_service.py # Reply routing by intent
├── requirements.txt
├── Procfile
├── render.yaml
├── .env.example
└── .gitignore
```

### Key Design Decisions

1. **4-Context Composition**: Every message is composed from category + merchant + trigger + optional customer context.
2. **LLM-first with deterministic fallback**: Gemini/OpenAI/etc at temperature=0 for determinism. If LLM fails/times out, falls back to grounded templates that never hallucinate.
3. **Trigger-kind dispatch**: Different prompt framing for each trigger kind (research_digest, recall_due, perf_dip, etc.).
4. **Thread-safe in-memory store**: RLock-protected ContextStore with atomic version replacement.
5. **Stateful conversations**: Per-conversation state tracks turns, bot messages, auto-reply count, intent.

---

## API Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/v1/healthz` | GET | Liveness probe; returns context counts |
| `/v1/metadata` | GET | Bot identity (team, model, approach) |
| `/v1/context` | POST | Receive context pushes (category/merchant/customer/trigger) |
| `/v1/tick` | POST | Periodic wake-up; bot decides what to send |
| `/v1/reply` | POST | Receive merchant/customer reply; bot responds |
| `/v1/teardown` | POST | (Optional) Wipe all state at end of test |

---

## Context Management

- **Versioned**: Same (scope, context_id, version) = idempotent (no-op). Higher version = atomic replace. Lower version = 409 Stale.
- **Thread-safe**: All reads/writes protected by `threading.RLock`.
- **Dynamic**: Judge can push new contexts mid-test; bot automatically uses the latest version.
- **Scopes**: `category`, `merchant`, `customer`, `trigger`

---

## LLM Integration

Set `LLM_PROVIDER` and `LLM_API_KEY` in your `.env`:

| Provider | `LLM_PROVIDER` value | Default model |
|---|---|---|
| Google Gemini | `gemini` | `gemini-2.0-flash` |
| OpenAI | `openai` | `gpt-4o-mini` |
| Anthropic | `anthropic` | `claude-3-5-haiku-20241022` |
| DeepSeek | `deepseek` | `deepseek-chat` |
| Groq | `groq` | `llama-3.1-70b-versatile` |

All providers use **temperature=0** for deterministic output.

The bot requests structured JSON from the LLM. Post-LLM validation checks:
- Non-empty body
- Valid CTA value
- Valid send_as value
- No URLs (Meta policy)
- No category taboo words
- Correct suppression_key

---

## Fallback Strategy

If the LLM is unavailable, times out, or returns invalid JSON:
1. Attempt repair of minor issues (invalid CTA, missing suppression_key)
2. Fall back to `fallback_composer.py` — **deterministic templates** for all 25 trigger kinds
3. Templates only use facts from the provided contexts — **no hallucination**

The API **never crashes** — always returns a valid response within the 30s timeout.

---

## Auto-Reply Detection

The bot detects WhatsApp Business canned auto-replies using regex patterns:
- "Thank you for contacting..."
- "Our team will respond shortly"
- "Automated assistant" / "I am an automated"
- Business hours messages
- Hindi equivalents ("aapki jaankari ke liye shukriya", etc.)

**Response strategy**:
1. **1st auto-reply**: Send one bridging message ("Looks like an auto-reply — when the owner sees this...")
2. **2nd auto-reply**: `wait` 24 hours
3. **3rd+ auto-reply**: `end` gracefully

---

## Intent Handling

The bot classifies merchant messages into intents:

| Intent | Examples | Bot Response |
|---|---|---|
| `commit` | "ok let's do it", "go ahead", "proceed" | **Immediate action** — no qualifying questions |
| `yes` | "yes", "haan", "sure" | Take the offered action |
| `no` | "not interested", "stop" | `end` gracefully |
| `wait` | "give me time", "later" | `wait` 1 hour |
| `hostile` | "stop bothering me", "useless spam" | `end` immediately |
| `auto_reply` | Canned WA Business messages | Multi-stage detection above |
| `question` | "what is this?", "how much?" | Answer from context |
| `unknown` | General conversation | Continue helpfully |

---

## Local Setup

### 1. Install dependencies

```bash
cd vera_bot
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env and set your LLM_API_KEY
```

### 3. Run the bot

```bash
# Load .env and start
$env:LLM_PROVIDER="gemini"; $env:LLM_API_KEY="your_key_here"; uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Or on Linux/Mac:
```bash
export LLM_PROVIDER=gemini
export LLM_API_KEY=your_key_here
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

### 4. Test with judge simulator

```bash
# In a separate terminal, from the project root
cd ..  # go to project root where judge_simulator.py lives
# Edit judge_simulator.py: set LLM_PROVIDER, LLM_API_KEY, BOT_URL
python judge_simulator.py
```

---

## Testing with judge_simulator.py

The simulator runs these scenarios:

- **warmup**: healthz + metadata + context pushes
- **phase2_short**: warmup + tick + scoring
- **auto_reply_hell**: 4 identical auto-replies → bot must detect and exit
- **intent_transition**: merchant says "ok let's do it" → bot must act immediately
- **hostile**: merchant says "stop messaging me" → bot must end gracefully
- **all**: runs all 4 above
- **full_evaluation**: all contexts + all triggers scored

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `LLM_PROVIDER` | Yes | `gemini` | Provider: gemini/openai/anthropic/deepseek/groq |
| `LLM_API_KEY` | Yes | — | Your API key |
| `LLM_MODEL` | No | Provider default | Specific model to use |
| `LLM_TIMEOUT` | No | `20` | Seconds to wait for LLM response |
| `TEAM_NAME` | No | `Team Vera` | Team name for /v1/metadata |
| `TEAM_MEMBERS` | No | `Member1` | Comma-separated team member names |
| `CONTACT_EMAIL` | No | — | Contact email for /v1/metadata |
| `APP_VERSION` | No | `1.0.0` | Bot version |
| `MODEL_NAME` | No | `gemini-2.0-flash` | Model name advertised in metadata |
| `PORT` | No | `8080` | Server port (set automatically on Render) |

---

## Deployment to Render

1. Push the `vera_bot/` directory to a GitHub repository
2. Create a new **Web Service** on [render.com](https://render.com)
3. Connect your GitHub repo
4. Set build command: `pip install -r requirements.txt`
5. Set start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
6. Add environment variables in Render dashboard:
   - `LLM_PROVIDER=gemini`
   - `LLM_API_KEY=your_actual_key`
   - `LLM_MODEL=gemini-2.0-flash`
   - Other TEAM_NAME, CONTACT_EMAIL etc.
7. Deploy — Render gives you a public HTTPS URL

Your endpoints will be at:
```
https://your-service.onrender.com/v1/healthz
https://your-service.onrender.com/v1/metadata
https://your-service.onrender.com/v1/context
https://your-service.onrender.com/v1/tick
https://your-service.onrender.com/v1/reply
```

---

## Final Submission Checklist

- [ ] Bot deployed and public URL reachable
- [ ] `/v1/healthz` returns 200 with correct context counts
- [ ] `/v1/metadata` returns team info
- [ ] Context versioning works (same version = idempotent, higher = replace, lower = 409)
- [ ] `/v1/tick` returns actions within 30s
- [ ] `/v1/reply` handles auto-reply, intent transition, hostile correctly
- [ ] No secrets in source code
- [ ] Submit public URL via challenge submission portal

---

## Approach

**What works well:**
- 4-context composition with trigger-kind dispatch gives highly specific messages
- Temperature=0 LLM ensures deterministic responses for identical inputs
- Deterministic fallback means the bot never fails, even without internet
- Auto-reply detection prevents wasted conversation turns
- Intent classification ensures "ok let's do it" transitions immediately to action

**Tradeoffs:**
- In-memory store = fast but requires no restarts during test (Render keeps instances warm)
- Single LLM call per composition = simple and within 30s budget
- Template fallback is grounded but less creative than LLM output

**What additional context would help:**
- Merchant's current open appointment slots (for recall/booking triggers)
- Real GBP data (current rating, photos, posts)
- Customer phone numbers for actual WhatsApp send simulation
