"""The thing `run.bat` runs.

Almost everything lives here rather than in the batch file, on purpose. A batch
file that rewrites itself while cmd.exe is part-way through reading it does
strange things, so `run.bat` is a stub that changes about once a year, and this
— which updates with every `git pull` — does the work.

Standard library only, and no import of stockforge until after the install
step, because on a first run there is nothing to import yet.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
STAMP = VENV / ".stockforge-installed"

MENU = """
  stockforge
  ----------------------------------------------------------------
   1   Open the control panel            everything, in a browser
   2   Check the setup                   what is and is not ready

   3   Pull designs from a folder        images already on disk
   4   Pull from your Etsy shop          walks the whole catalogue
   5   Count a shop                      how big is the job, first

   6   Run the queue                     analyse, rebuild, export
   7   Where everything is up to

   8   Read what it understood           judge one design's spec
   9   What to draw next                 the motif work list
  10   What is waiting on you            the review queue

  11   Deliver — dry run                 writes the CSVs, sends nothing
  12   Deliver — send                    uploads to the agencies

  13   Update to the latest code
   0   Quit
  ----------------------------------------------------------------"""


def run(*args: str) -> int:
    return subprocess.run([str(PYTHON), "-m", "stockforge.cli", *args]).returncode


def say(text: str = "") -> None:
    print(text, flush=True)


# --------------------------------------------------------------------------
# keeping the checkout and the environment current
# --------------------------------------------------------------------------

def head() -> str:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def update() -> None:
    """Pull, quietly, and say only what changed.

    A failure here is never fatal: no network, no git, or local edits you want
    to keep are all reasons to carry on with the code already there.
    """
    before = head()
    try:
        proc = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"],
                              capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        say(f"  (could not check for updates: {exc})")
        return

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        say(f"  (could not update: {detail[-1] if detail else 'unknown'})")
        say("   carrying on with the code you have.")
        return

    after = head()
    if before and after and before != after:
        say(f"  updated {before[:8]} -> {after[:8]}")
    else:
        say("  already up to date")


def venv_runs() -> bool:
    """Does the interpreter execute at all — not merely exist."""
    if not PYTHON.exists():
        return False
    try:
        return subprocess.run([str(PYTHON), "-c", ""],
                              capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def imports_this_copy() -> bool:
    """Is the environment running *this* folder's code, or another one's?

    An editable install writes the source folder's absolute path into
    site-packages, so once the folder is moved the environment keeps importing
    stockforge from wherever it used to be. If the old folder is gone the
    program will not start; if it is still there — a copy rather than a move —
    it starts and runs the old code, so every update pulled into the new folder
    is quietly ignored. The silent one is the reason this is checked at all.
    """
    try:
        done = subprocess.run(
            [str(PYTHON), "-c",
             "import stockforge,sys; sys.stdout.write(stockforge.__file__)"],
            capture_output=True, text=True, timeout=120,
            # From inside .venv, never from ROOT: the launcher has already
            # chdir'd to ROOT, and a probe run from there finds ROOT/stockforge
            # on sys.path whatever the environment was installed against, so it
            # would be testing the working directory and not the install.
            cwd=str(VENV) if VENV.is_dir() else None)
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode != 0:
        return False
    try:
        Path(done.stdout.strip()).resolve().relative_to(ROOT)
    except (ValueError, OSError):
        return False
    return True


def install_if_needed() -> bool:
    """Install the package into .venv, but only when the code has moved."""
    if not venv_runs():
        if VENV.exists():
            say("  the environment stopped working — rebuilding it.")
            say("  (normal after moving the folder; nothing of yours is in there)")
            shutil.rmtree(VENV, ignore_errors=True)
        else:
            say("  creating a private Python environment...")
        made = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
        if made.returncode != 0 or not PYTHON.exists():
            say("  could not create it. On Windows this usually means Python was")
            say("  installed for one user only — reinstall it for all users.")
            return False

    current = head() or "unknown"
    if (STAMP.exists() and STAMP.read_text().strip() == current
            and imports_this_copy()):
        return True

    say("  installing dependencies (only needed when the code changes)...")
    proc = subprocess.run([str(PYTHON), "-m", "pip", "install", "-q", "-e", str(ROOT)])
    if proc.returncode != 0:
        say("  install failed. Check your internet connection and try again.")
        return False
    STAMP.write_text(current)
    return True


# --------------------------------------------------------------------------

def ask(prompt: str, default: str = "") -> str:
    shown = f"{prompt} [{default}]: " if default else f"{prompt}: "
    try:
        return input(f"  {shown}").strip() or default
    except (EOFError, KeyboardInterrupt):
        return ""


def limit_args() -> list[str]:
    n = ask("How many at most (blank = all)")
    return ["--limit", n] if n.isdigit() else []


def choose(option: str) -> bool:
    """Returns False when it is time to stop."""
    if option == "0":
        return False
    if option == "1":
        say("\n  Opening the control panel. Close this window to stop it.\n")
        run("ui")
    elif option == "2":
        run("check")
    elif option == "3":
        folder = ask("Folder of images")
        if folder:
            run("pull", "folder", folder, *limit_args())
    elif option == "4":
        shop = ask("Etsy shop name or URL")
        if shop:
            run("pull", "shop", shop, *limit_args())
    elif option == "5":
        shop = ask("Etsy shop name or URL")
        if shop:
            run("count", "shop", shop)
    elif option == "6":
        run("run", *limit_args())
    elif option == "7":
        run("status")
    elif option == "8":
        run("spec")
        which = ask("Which design (the start of an id is enough, blank to skip)")
        if which:
            run("spec", which)
    elif option == "9":
        run("motifs", "todo")
    elif option == "10":
        run("review")
    elif option == "11":
        run("publish", "--dry-run")
    elif option == "12":
        say("\n  This uploads to the agencies. Ctrl-C now if that is not what you meant.")
        if ask("Type SEND to confirm") == "SEND":
            run("publish")
    elif option == "13":
        update()
        install_if_needed()
    else:
        say("  Not one of the options.")
    return True


def main(argv: list[str]) -> int:
    os.chdir(ROOT)
    say(f"\n  stockforge — {ROOT}")

    if "--no-update" not in argv:
        update()
    argv = [a for a in argv if a != "--no-update"]

    if not install_if_needed():
        return 1

    # Anything passed through goes straight to the command line, so
    # `run.bat status` works as well as the menu does.
    if argv:
        return run(*argv)

    while True:
        say(MENU)
        try:
            option = input("  Choose: ").strip()
        except (EOFError, KeyboardInterrupt):
            say()
            return 0
        say()
        if not choose(option):
            return 0
        say("\n  ---")
        try:
            input("  Press Enter to go back to the menu.")
        except (EOFError, KeyboardInterrupt):
            return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
