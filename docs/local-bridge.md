# Running the Gemini bridge, and pointing stockforge at it

A bridge is a small server on your own machine that speaks OpenAI's
`/v1/chat/completions` on one side and a chat website's own protocol on the
other. stockforge already talks to anything that speaks `/v1`, so from its side
a bridge is just another model.

Everything on this page was run end to end before it was written: the bridge
started, stockforge's own Test button went green against it, and three designs
were written through it by real Gemini and came out `ready`.

## Before you start

Using a chat subscription through an unofficial bridge is against those
services' terms of use. It is your account and your decision; this page assumes
you have made it.

A bridge is also **one session with a human-scale rate limit**. Twenty-two
seconds per design is what was measured, single file. Several API keys across
several lanes will beat it for bulk reading every time.

There is no equivalent bridge here for ChatGPT, and it is a much harder target:
Gemini's endpoint takes cookies, ChatGPT's sits behind Cloudflare plus a
per-request proof-of-work.

## Install it

In PowerShell. **Start with the `cd`** — a PowerShell window opened from the
Start menu, and every window opened as Administrator, starts in
`C:\WINDOWS\System32`, where Windows refuses to let you create anything:

    fatal: could not create work tree dir 'gemini-web2api': Permission denied

```powershell
cd "$env:USERPROFILE\Desktop"
git clone https://github.com/Sophomoresty/gemini-web2api.git
cd gemini-web2api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install httpx
```

Nothing here wants Administrator. If the title bar says so, close it and open
an ordinary window — the bridge is a program you run as yourself, and it needs
to write its config beside itself.

If `Activate.ps1` is refused with an execution-policy error, allow it for that
window only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

`httpx` is the only dependency, and it is optional — without it the bridge
falls back to buffered `urllib`, which works but loses true streaming.

## Run it

```powershell
python -m gemini_web2api --port 8081
```

It prints what it is doing, and the two lines worth reading are the base URL
and whether it found a cookie:

```text
gemini-web2api v1.1.0
  Listening: http://0.0.0.0:8081
  Base URL:  http://localhost:8081/v1
  Models:    gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash, ...
  Cookie:    none (anonymous)
```

**Anonymous works.** No cookies, no config file, no sign-in — it answered and
wrote finished designs. Start here, because if this works you can skip the
whole next section. What anonymous does not get you is the Pro models: ask for
`gemini-3.1-pro` without a subscription cookie and it quietly serves Flash
instead.

Check it from a second PowerShell window before touching stockforge. If this
does not answer, nothing in the panel will work either:

```powershell
curl.exe http://127.0.0.1:8081/v1/models
```

## Signing in, only if you need Pro

The repo ships a Chrome extension that does this properly — do not copy cookies
by hand out of DevTools.

1. `chrome://extensions` → turn on **Developer mode** → **Load unpacked** →
   choose the `gemini-cookie-sync-extension` folder.
2. Open <https://gemini.google.com/app>, sign in, and refresh.
3. Open the extension, click **Inspect session**, then **Export
   gemini-auth.json**. It lands in Downloads.

Then, in the project folder:

```powershell
Copy-Item config.example.json config.json
Move-Item "$env:USERPROFILE\Downloads\gemini-auth.json" .\gemini-auth.json -Force

$auth = Get-Content .\gemini-auth.json -Raw | ConvertFrom-Json
$cfg  = Get-Content .\config.json      -Raw | ConvertFrom-Json
$cfg.cookie_file = (Resolve-Path .\gemini-auth.json).Path
$cfg.auth_user   = $auth.auth_user
$cfg.xsrf_token  = $auth.xsrf_token
if ($auth.gemini_bl) { $cfg.gemini_bl = $auth.gemini_bl }
$cfg | ConvertTo-Json -Depth 5 | Set-Content .\config.json -Encoding utf8
```

(The project's own instructions for this step use `jq` and assume WSL. The
above is the same thing in PowerShell.)

Restart the bridge. It should now say `Cookie: yes`.

**Note the API key.** `config.example.json` sets `"api_keys": ["sk-gemini"]`,
so the moment you create `config.json` the bridge starts requiring a key. With
no `config.json` at all it requires none. Whatever is in that list is what goes
in stockforge's API key box — `sk-gemini` unless you change it.

## Pointing stockforge at it

Setup → Models → **Add a connection**:

| field | value |
|---|---|
| Name it | `Gemini via bridge` |
| Used for | Writing designs from a brief, or Reading designs — see below |
| Backend | `openai` |
| Base URL | `http://127.0.0.1:8081/v1` |
| API key | blank for anonymous; `sk-gemini` if you made a `config.json` |
| Model | `gemini-3.6-flash` — or **Fetch models from that server** |

**Test it**, then **Make live**. Test sends one real request, so green means
the whole path works rather than that the address looks plausible. It took 12
seconds when measured.

**Which role — and this one is measured, not guessed.** Give it a role that
sends no images: **writing designs from a brief**, or the easy questions. Do
not give it reading, and do not use it with `invent --like`.

The bridge does not answer image calls. It accepts the picture, uploads it to
Google, returns `200` — and then never sends a body. Measured three ways, all
identical: the full 1588px listing photo, the same photo shrunk to 640px, and
again with `httpx` installed for true streaming. Every one ended
`sent nothing for 180s`, twice per design, 0 of 1 written.

The control that makes it conclusive rather than a guess: a text-only call to
the same bridge, in the same session, moments after an image call failed,
answered in **13 seconds**. It is not throttling or an exhausted quota. It is
image calls.

Reading the bridge's source suggests otherwise — it really does decode the
base64 that stockforge sends, and the log really does say `Image uploaded`. But
decoding and uploading is not answering, and the source was where an earlier
version of this page got it wrong.

So:

| job | through the bridge |
|---|---|
| `invent` from a brief | yes — 22s a design, three of three `ready` |
| the easy questions (quick role) | yes |
| `invent --like <picture>` | no — use an API key |
| reading designs (`run`) | no — use an API key |

## When it stops working

Bridges break when the site changes, and it changes without notice. The symptom
in stockforge is the Test button going red, or designs failing with a message
naming that address. Check it with the `curl.exe` line above: if that is dead,
it is the bridge and not the program.

Keep a second model live if you rely on this. A dead connection no longer kills
a design — the chain hands the work to the next live model — but a chain of one
has nothing to hand it to.
