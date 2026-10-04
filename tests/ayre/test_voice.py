"""Speech gate: no spam, no stale callouts, safety always gets through."""
import asyncio, importlib.util, threading, time, types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ayre_voice", ROOT / "skills/ayre_eyes/ayre_voice.py")
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

loop = asyncio.new_event_loop()
threading.Thread(target=loop.run_forever, daemon=True).start()

class Clock:
    t = 100.0
    def __call__(self): return self.t

def make():
    heard = []
    async def play(text, no_interrupt=False): heard.append((text, no_interrupt))
    w = types.SimpleNamespace(audio_player=types.SimpleNamespace(is_playing=False), play_to_user=play)
    clock = Clock()
    gate = v.SpeechGate(w, loop, clock=clock, retry_s=0.01)
    return gate, w, clock, heard

def settle(): time.sleep(0.08)
failures = 0
def check(name, cond):
    global failures
    print(("PASS " if cond else "FAIL ") + name); failures += not cond

# 1. a burst of ten callouts in one second while she is talking: only the newest survives
gate, w, clock, heard = make()
w.audio_player.is_playing = True
for i in range(10):
    gate.say(f"callout {i}", v.CALLOUT); clock.t += 0.1
w.audio_player.is_playing = False; gate.playback_finished(); settle()
check("burst while talking -> only newest callout spoken", [h[0] for h in heard] == ["callout 9"])

# 2. a callout that waited too long is dropped, not said late
gate, w, clock, heard = make()
w.audio_player.is_playing = True
gate.say("fifty", v.CALLOUT); clock.t += 3.0
w.audio_player.is_playing = False; gate.playback_finished(); settle()
check("stale callout dropped", heard == [])

# 3. chatter never interrupts, never queues, and is rate limited
gate, w, clock, heard = make()
w.audio_player.is_playing = True
check("chatter dropped while talking", gate.say("quantum spooling", v.CHATTER) is False)
w.audio_player.is_playing = False; gate.playback_finished(); settle()
check("dropped chatter is not said later", heard == [])
clock.t += 20
check("chatter allowed when quiet", gate.say("arrived", v.CHATTER))
clock.t += 5
check("second chatter within 10 s refused", gate.say("location changed", v.CHATTER) is False)

# 4. safety always speaks and interrupts, and clears a waiting callout
gate, w, clock, heard = make()
w.audio_player.is_playing = True
gate.say("thirty", v.CALLOUT)
check("safety spoken while talking", gate.say("Assist off: stick input.", v.SAFETY))
w.audio_player.is_playing = False; gate.playback_finished(); clock.t += 0.2; settle()
check("safety interrupts (no_interrupt False)", heard and heard[0] == ("Assist off: stick input.", False))
check("waiting callout cleared by safety", [h[0] for h in heard] == ["Assist off: stick input."])

# 5. callouts never closer than the gap; chatter waits for a quiet spell after callouts
gate, w, clock, heard = make()
gate.say("one hundred", v.CALLOUT); gate.playback_finished()
clock.t += 0.5; gate.say("fifty", v.CALLOUT)
check("second callout held inside the gap", [h[0] for h in heard] == ["one hundred"]) if not settle() else None
clock.t += 1.0; settle()
check("held callout spoken once the gap passes", [h[0] for h in heard] == ["one hundred", "fifty"])
check("chatter refused right after callouts", gate.say("nice landing", v.CHATTER) is False)

# 6. one gate per wingman, shared by every skill
w = types.SimpleNamespace(audio_player=types.SimpleNamespace(is_playing=False), play_to_user=None)
check("skills share one gate", v.SpeechGate.get(w, loop) is v.SpeechGate.get(w, loop))

print(f"\n{'all passed' if not failures else str(failures) + ' FAILED'}")
