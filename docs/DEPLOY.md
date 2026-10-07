# Deploying Handoff (private, access-code version)

This puts Handoff online so that anyone with the link **and** the access code can use it:
course staff, say. Nothing is public: the music lives in a private Cloudflare R2 bucket, and the
backend only hands out links that expire (6 hours) to visitors who entered the code.

```
 visitor's browser
   │  1. opens the board          ─────────►  handoff-web   (Render static site, free)
   │  2. enters the access code   ─────────►  handoff-api   (Render web service, free)
   │  3. gets signed links        ◄─────────    checks the code, signs links,
   │                                            composes with Claude (rate-limited)
   │  4. downloads audio          ─────────►  R2 bucket "handoff-library" (private, free tier)
```

What it costs:
- **Render:** free.
- **R2:** free up to 10 GB of storage. Downloads are free; the library is about 2.7 GB.
- **Anthropic:** about $0.15–0.30 per AI composition, limited to 10 an hour per visitor.

Plan on about 45 minutes the first time, most of it the 2.7 GB upload.

---

## 0. Before you start

You need:
- **Accounts:** GitHub (with this repo pushed), [Cloudflare](https://dash.cloudflare.com/sign-up) and [Render](https://dashboard.render.com/register). Signing in to Render with GitHub is easiest.
- **Your Anthropic API key:** the one already in `pipeline/.env`.
- **An access code to give your graders,** e.g. `handoff-fall-2026`. Avoid spaces.
- **Code pushed to GitHub:**
  ```bash
  git add -A && git commit -m "Hosting: access code, signed R2 links, Render blueprint"
  git push
  ```
  The music library (`web/public/library`) and `pipeline/.env` are deliberately **not** in git:
  the library goes to R2 in step 1, and secrets go into Render in step 2.

---

## 1. Cloudflare R2: the private music bucket

### 1a. Create the bucket

1. In the [Cloudflare dashboard](https://dash.cloudflare.com), open **R2 Object Storage** from
   the left sidebar. If this is your first time, Cloudflare may ask you to enable R2 and add a
   payment method; the free allowance still applies.
2. Click **Create bucket**:
   - **Name:** `handoff-library`.
   - **Location:** Automatic.
   - **Storage class:** **Standard**. The free tier doesn't cover Infrequent Access.
3. Click **Create bucket**.

Leave **Public access off**. Don't enable the `r2.dev` URL and don't connect a custom domain:
the signed links only work on R2's own address, and a public bucket would defeat the point.

### 1b. Note your Account ID

On the R2 Object Storage overview page, **Account Details** shows your **Account ID**, a long
hex string. Copy it.

### 1c. Create two API tokens

Still on the R2 overview page, under **Account Details**, click **Manage** next to
**API Tokens**, then **Create Account API token**. Make two tokens:

| Token name | Permissions | Bucket scope | Used by |
|---|---|---|---|
| `handoff-upload` | **Object Read & Write** | `handoff-library` only | you, to upload the library |
| `handoff-render` | **Object Read only** | `handoff-library` only | the Render backend |

After each one, Cloudflare shows an **Access Key ID** and a **Secret Access Key**. Copy both
right away: the secret is never shown again. If you lose one, delete the token and make a new one.

### 1d. Upload the library

On your computer, add the **upload** token to `pipeline/.env` (gitignored, next to your
Anthropic key):

```bash
R2_ACCOUNT_ID=<your account id>
R2_ACCESS_KEY_ID=<handoff-upload access key id>
R2_SECRET_ACCESS_KEY=<handoff-upload secret>
R2_BUCKET=handoff-library
```

Then:

```bash
cd pipeline
.venv/bin/pip install "boto3>=1.34"             # once
.venv/bin/python upload_library.py --dry-run    # lists ~300 files, ~2.7 GB
.venv/bin/python upload_library.py              # uploads (time depends on your upload speed)
```

It's safe to stop and re-run: files already in the bucket unchanged are skipped. Re-run it any
time you add tracks or compose new transitions locally.

Check it worked: R2 → `handoff-library` should list one folder per track, plus `index.json`
and `transitions.json`.

---

## 2. Render: the board and the backend

### 2a. Create both services from the Blueprint

1. In the [Render dashboard](https://dashboard.render.com), click **New +** → **Blueprint**.
2. Connect your GitHub account if asked, and pick this repository. Render finds `render.yaml`
   at the root and shows two services: **handoff-api** (web service, Python, free) and
   **handoff-web** (static site).
3. Render asks for the values marked secret in `render.yaml`:

   | Service | Key | Value |
   |---|---|---|
   | handoff-api | `ACCESS_CODE` | the code you chose in step 0 |
   | handoff-api | `ANTHROPIC_API_KEY` | your Anthropic key |
   | handoff-api | `ALLOWED_ORIGINS` | `https://handoff-web.onrender.com` (see the note below) |
   | handoff-api | `R2_ACCOUNT_ID` | from 1b |
   | handoff-api | `R2_ACCESS_KEY_ID` | the **handoff-render** (read-only) access key ID |
   | handoff-api | `R2_SECRET_ACCESS_KEY` | the **handoff-render** secret |
   | handoff-api | `R2_BUCKET` | `handoff-library` |
   | handoff-web | `VITE_API_URL` | `https://handoff-api.onrender.com` (see the note below) |

   **About the two URLs:** Render names a service `https://<name>.onrender.com`. If that name
   is already taken by someone else, it adds a suffix (e.g. `handoff-web-x7k2.onrender.com`).
   Enter the plain names now; step 2c fixes them if Render picked different ones. Use full
   `https://` URLs with **no trailing slash**.
4. Click **Apply**. Both services build:
   - **handoff-api:** installs a few Python packages (1–2 minutes).
   - **handoff-web:** runs `npm ci && npm run build`, which also downloads the hand-tracking
     model (2–4 minutes).

### 2b. Check the backend

Open `https://handoff-api.onrender.com/api/health` (your backend's URL). It should show:

```json
{"ok": true, "hosted": true}
```

If the deploy failed instead, open **handoff-api → Logs**. The most likely cause is a
missing or misspelled `R2_*` value: the server refuses to start when hosting is half-configured,
or when `ACCESS_CODE` is empty.

### 2c. Fix the URLs if Render changed them

Look at the URL at the top of each service's page.
- **If the board's URL isn't `https://handoff-web.onrender.com`:** open **handoff-api →
  Environment**, set `ALLOWED_ORIGINS` to the real board URL and save. It restarts by itself.
- **If the backend's URL isn't `https://handoff-api.onrender.com`:** open **handoff-web →
  Environment**, set `VITE_API_URL` to the real backend URL and save. Then **Manual Deploy →
  Deploy latest commit**. The board bakes this URL in when it's built, so it needs a rebuild.

---

## 3. Let the board read the bucket (CORS)

Browsers only accept files from the bucket if the bucket names the board's address. Print the
rule with your board's real URL:

```bash
cd pipeline
.venv/bin/python upload_library.py --cors https://handoff-web.onrender.com
```

It prints:

```json
[
  {
    "AllowedOrigins": ["https://handoff-web.onrender.com"],
    "AllowedMethods": ["GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

In Cloudflare: **R2 Object Storage** → `handoff-library` → **Settings** → **CORS Policy** →
**Add CORS policy** → **JSON** tab. Paste it and click **Save**.

---

## 4. Try it as a grader would

Use a private/incognito window, so nothing from your own testing is remembered.

1. Open the board URL. You should see **Handoff, Enter the access code**.
2. Enter the code. If the backend was asleep, it says **Starting the server…** for up to a
   minute, then the board appears.
3. **Library** → pick two tracks (**Load to A**, **Load to B**). Each track downloads its stems
   (50–150 MB), so the first loads take a few seconds to tens of seconds.
4. Press **Play** on A. Try **Try transition** and **Compose with AI**.
5. Reload the page: no code prompt this time, and any compositions are still listed.

---

## 5. Sharing with course staff

Send the **link** and the **access code**, ideally in separate messages. Something like:

> Handoff: https://handoff-web.onrender.com, access code `handoff-fall-2026`.
> Use Chrome on a laptop/desktop. The first visit can take up to a minute while the free server
> wakes up. Loading a track downloads its stems, so give it a few seconds. Hand control needs a
> webcam (Camera button, top right); the mouse works for everything too. "Compose with AI"
> takes 1–3 minutes per transition.

Good to know:
- **Wake-up:** the free backend sleeps after 15 minutes without visitors, and the next visit
  waits about a minute while it wakes. The board shows a message while it does. If you know when
  grading happens, open the site a few minutes before to wake it.
- **Saved compositions:** they're kept in each visitor's own browser, not on the server (the free
  server keeps no files). They survive reloads on that browser.
- **Cost guard:** composing is limited to 10 an hour per visitor (`COMPOSE_PER_HOUR` in Render).
  For a hard ceiling on spend, set a monthly limit in the Anthropic Console.
- **Changing or revoking access:** edit `ACCESS_CODE` on handoff-api and save. The old code stops
  working immediately; visitors are asked for the new one.

---

## 6. Taking it down after grading

- **Render:** each service → **Settings** → **Suspend service**, or **Delete service**.
- **Cloudflare:** R2 → `handoff-library` → delete the objects, then the bucket. Also delete both
  API tokens.
- **Anthropic:** if you made a separate key for this, delete it in the Console.

---

## Troubleshooting

| What you see | Likely cause | Fix |
|---|---|---|
| The board opens with no code prompt | `VITE_API_URL` wasn't set when handoff-web was built | Set it (2c), then Manual Deploy |
| Stuck on "Starting the server…" for over 2 minutes | handoff-api isn't running | Check its **Logs** and the `/api/health` URL |
| "That code didn't work" | Typo, or a space in `ACCESS_CODE` | Compare with handoff-api → Environment |
| "Couldn't reach the library (4xx/5xx)" | Wrong `R2_*` values or bucket name | Fix them in handoff-api → Environment |
| Library opens but tracks never load; console says *blocked by CORS policy* | The bucket's CORS rule doesn't name the board's exact URL | Redo step 3 with the exact URL (https, no trailing slash) |
| Compose says "Use the Handoff board to compose" | `ALLOWED_ORIGINS` doesn't match the board's URL | Fix it (2c) |
| Compose says "The server has no Anthropic API key" | `ANTHROPIC_API_KEY` missing | Set it on handoff-api |
| Compose says "That's the limit of 10 compositions an hour" | The rate limit, working as intended | Wait, or raise `COMPOSE_PER_HOUR` |
| handoff-web build fails at `prebuild` | The hand-model download failed | Manual Deploy again (it's a download from Google's storage) |

Not verified until it's live: Render's docs don't state a maximum request duration. A
composition takes 1–3 minutes; if compositions fail after a long wait while everything else
works, check the handoff-api logs.

## Local development is unchanged

Without `VITE_API_URL` and the `R2_*` variables, everything runs as before: `npm run dev` serves
`web/public/library`, and the local backend saves compositions into it. To test the hosted
setup locally (a local S3 server stands in for R2): `pipeline/.venv/bin/python web/e2e/hosted.py`.
Add `--compose` to also make one real (paid) composition.
