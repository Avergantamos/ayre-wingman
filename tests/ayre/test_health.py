"""Self-check: she says Online when fine, names the problem when not, and never sends a black frame."""
import asyncio, importlib.util, sys, types
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path[:0] = [str(HERE / "stubs"), str(ROOT)]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m

ship = load("ship", ROOT / "skills/ayre_ship/main.py")
eyes = load("eyes", ROOT / "skills/ayre_eyes/main.py")
failures = 0
def check(name, cond):
    global failures
    print(("PASS " if cond else "FAIL ") + name); failures += not cond

check("black capture detected", ship.screen_is_black([(0, 0, 0)] * 100 + [(3, 3, 3)] * 10))
check("dark space scene with HUD is not black", not ship.screen_is_black([(2, 2, 4)] * 90 + [(120, 220, 255)] * 10))

ok = {"brain": (True, "x"), "screen": (True, "x"), "keys": (True, "x"), "game_log": (False, "path"), "music_ducking": (False, "pycaw")}
check("non-critical problems don't block Online", ship.health_summary(ok)[0] is None)
bad = dict(ok, screen=(False, ship.PROBLEM_LINES["screen"]), brain=(False, ship.PROBLEM_LINES["brain"]))
check("brain problem reported first", ship.health_summary(bad)[0] == ship.PROBLEM_LINES["brain"])
check("report lists every check", all(k in ship.health_summary(bad)[1] for k in ok))

# startup self-check speaks exactly one line: Online when fine, the problem when not
async def run(brain_ok, screen_ok):
    said = []
    s = ship.AyreShip.__new__(ship.AyreShip)
    s.gate = types.SimpleNamespace(say=lambda text, prio: said.append((text, prio)))
    s.ducker = None
    s.printr = types.SimpleNamespace(print=lambda *a, **k: None)
    s._log_path = lambda: "/nonexistent/Game.log"
    async def brain(): return (brain_ok, "model answered" if brain_ok else ship.PROBLEM_LINES["brain"])
    s._check_brain = brain
    s._check_screen = lambda: (screen_ok, "ok" if screen_ok else ship.PROBLEM_LINES["screen"])
    orig = ship.asyncio.sleep
    async def nosleep(_): return None
    ship.asyncio.sleep = nosleep
    try:
        await s._self_check(startup=True)
    finally:
        ship.asyncio.sleep = orig
    return said

said = asyncio.run(run(True, True))
check("all good -> one Online line as chatter", len(said) == 1 and said[0][1] == ship.VOICE.CHATTER)
said = asyncio.run(run(True, False))
check("black screen -> borderless warning as safety", said == [(ship.PROBLEM_LINES["screen"], ship.VOICE.SAFETY)])
said = asyncio.run(run(False, True))
check("Mac unreachable -> brain warning", said == [(ship.PROBLEM_LINES["brain"], ship.VOICE.SAFETY)])

# eyes never sends a black frame to the model
e = eyes.AyreEyes(None, None, types.SimpleNamespace())
called = []
async def llm(m): called.append(1)
e.llm_call = llm
answer = asyncio.run(e._ask(Image.new("RGB", (3440, 1440)), "s", "who is that", "target"))
check("black frame answered locally, model not called", "borderless" in answer and not called)

print(f"\n{'all passed' if not failures else str(failures) + ' FAILED'}")
