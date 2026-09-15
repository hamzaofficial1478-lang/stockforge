# Running a web-to-API bridge, and pointing stockforge at it

A bridge is a small server on your own machine that speaks OpenAI's
`/v1/chat/completions` on one side and a chat website's own protocol on the
other. stockforge already talks to anything that speaks `/v1`, so from its side
a bridge is just another model.

Two things are true at once and both matter:

- **The program side is done.** Any `/v1` server works, with no code changes.
  Verified end to end against a stand-in server: saved, tested, made live, and
  a real design sent through it with its images attached.
- **The bridge is not part of this program.** It is a separate project you run
  yourself. There is a working one for Gemini. There is not one here for
  ChatGPT, and ChatGPT is a much harder target — its web endpoint sits behind
  Cloudflare plus a per-request proof-of-work, where Gemini's takes cookies.

## Before you start

Using a chat subscription through an unofficial bridge is against those
services' terms of use, and the account at risk is the one you pay for. It is
your account and your decision; this page assumes you have made it.

Practically, a bridge is also **one session with a human-scale rate limit**. It
is a reasonable way to write a handful of designs from a brief. It is not a way
to read twenty-four designs in parallel — for that, several API keys across
several lanes will beat it every time.

## Gemini, on Windows

In PowerShell:

```powershell
git clone https://github.com/Sophomoresty/gemini-web2api.git
cd gemini-web2api
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Then the cookies. Sign in at `gemini.google.com` in Chrome, open DevTools
(`F12`) → **Application** → **Cookies** → `https://gemini.google.com`, and copy
the values of `SID`, `HSID`, `SSID`, `APISID`, `SAPISID` and `__Secure-1PSID`
into the project's `cookie.txt` as its README describes. Anonymous mode needs
none of this but does not reach the Pro models.

Start it, and note the port it prints:

```powershell
python gemini_web2api.py
```

Check it from a second PowerShell window before touching stockforge — if this
does not answer, nothing in the panel will work either:

```powershell
curl.exe http://127.0.0.1:8000/v1/models
```

## Pointing stockforge at it

Setup → Models → **Add a connection**:

| field | value |
|---|---|
| Name it | `Gemini via bridge` |
| Used for | Reading designs, or Writing — see below |
| Backend | `openai` |
| Base URL | `http://127.0.0.1:8000/v1` (whatever port it printed, plus `/v1`) |
| API key | leave blank |
| Model | whatever `/v1/models` listed |

**Test it**, then **Make live**. The Test button sends one real request, so a
green result means the whole path works — not that the address looks plausible.

Which role to give it: *Writing designs from a brief* sends no images and is
the cheapest thing to put behind a bridge. *Reading designs* sends two images
per call and several calls per design, which is exactly the traffic a bridge
gets throttled for. Start with the first.

## When it stops working

Bridges break when the site changes, which it does without notice. The symptom
in stockforge is the Test button going red, or designs failing with a message
from that address. That is the bridge, not the program: check it with the
`curl.exe` line above, and if it is down, stand the connection down in Setup so
your other models carry on. A dead connection no longer kills a design — it
hands the work to the next live model — but a chain of one has nothing to hand
to, so keep a second model live if you rely on this.
