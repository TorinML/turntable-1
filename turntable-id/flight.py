"""Flight: fuel and altitude as one system, driven only by the music.

  Fuel      Listening fills the tank; silence slowly drains it (life support).
  Burns     When a side plays through, Beepnik fires a burn on its own: it spends `burn_cost`
            fuel to climb `burn_gain` altitude, but only from surplus above `reserve`, so
            climbing never puts it in danger. Listening to records with no tracklist counts
            a burn for every `burn_minutes` of continuous listening.
  Holding   With fuel above `hold_level`, small corrections keep its altitude (costing
            `station_keeping_per_day` fuel a day).
  Drag      Below `hold_level` it can't correct its orbit, and drag pulls it down: it loses
            the same fraction of its altitude each day, so higher probes have more to lose
            and a longer way to fall.
  Re-entry  Once launched, falling below `reentry_altitude` means re-entry: it burns up and
            splashes down at Point Nemo. A probe still on the launch pad can't re-enter.

Altitude is in the same units as the orbit stages (`[orbit] stages`), capped at `ceiling`
(1.5 times the last stage unless set). Drag is worked out from `survive_days`: how long a
fully developed probe (at the ceiling, full tank) survives silence.

The model has closed forms for silence: fuel falls linearly, and under drag altitude decays
exponentially (alt = alt0 * e^(-k t)). That makes it exact and cheap to replay a probe's
whole life every tick.
"""
import math

SPIN_GAP = 30 * 60


def params(cfg: dict) -> dict:
    o, f = cfg["orbit"], cfg["needs"]["fuel"]
    stages = o["stages"]
    launch = stages[1][0] if len(stages) > 1 else 1.0
    ceiling = o.get("ceiling") or round(stages[-1][0] * 1.5, 2)
    p = {
        "fill": 100.0 / max(60, f["full_after_minutes"] * 60),        # fuel per second of listening
        "drain": 100.0 / max(3600, f["empty_after_hours"] * 3600),    # fuel per second of silence
        "keep": o["station_keeping_per_day"] / 86400.0,
        "hold": float(o["hold_level"]),
        "reserve": float(o["reserve"]),
        "burn_cost": float(o["burn_cost"]),
        "burn_gain": float(o["burn_gain"]),
        "burn_seconds": o["burn_minutes"] * 60,
        "reentry": float(o["reentry_altitude"]),
        "launch": float(launch),
        "ceiling": float(ceiling),
    }
    # Drag rate k (per second) from survive_days: a maxed probe with a full tank first spends
    # (100 - hold) fuel holding, then falls from the ceiling to the re-entry line.
    hold_secs = (100.0 - p["hold"]) / (p["drain"] + p["keep"])
    fall_secs = max(3600.0, o["survive_days"] * 86400 - hold_secs)
    p["k"] = math.log(p["ceiling"] / p["reentry"]) / fall_secs
    return p


def _silence(p, st, t0, t1):
    """Advance through quiet time [t0, t1]. Returns the re-entry time, if it happens."""
    dt = t1 - t0
    if dt <= 0:
        return None
    orbiting = st["launched"] and st["alt"] > p["reentry"]
    if not orbiting:
        st["fuel"] = max(0.0, st["fuel"] - p["drain"] * dt)
        return None
    # holding: fuel falls a little faster (corrections) until it reaches the hold level
    if st["fuel"] > p["hold"]:
        rate = p["drain"] + p["keep"]
        reach = (st["fuel"] - p["hold"]) / rate
        if dt <= reach:
            st["fuel"] -= rate * dt
            return None
        st["fuel"] = p["hold"]
        t0 += reach
        dt -= reach
    # slipping: life support keeps draining, and drag lowers the orbit
    st["fuel"] = max(0.0, st["fuel"] - p["drain"] * dt)
    t_hit = math.log(st["alt"] / p["reentry"]) / p["k"]
    if dt >= t_hit:
        st["alt"] = p["reentry"]
        return t0 + t_hit
    st["alt"] *= math.exp(-p["k"] * dt)
    return None


def _burn(p, st, t):
    if st["fuel"] - p["burn_cost"] < p["reserve"] or st["alt"] >= p["ceiling"]:
        st["skipped"] += 1
        return False
    st["fuel"] -= p["burn_cost"]
    st["alt"] = min(p["ceiling"], st["alt"] + p["burn_gain"])
    st["burns"] += 1
    st["last_burn"] = t
    if st["alt"] >= p["launch"]:
        st["launched"] = True
    st["peak"] = max(st["peak"], st["alt"])
    return True


def simulate(cfg: dict, plays: list, sides: dict, start: float, now: float, alt0: float = 0.0,
             fuel0: float = 0.0, launched0: bool = None) -> dict:
    """Replay listening from `start` to `now`.

    `plays`: oldest first, each {start, end, release_id, position, side}.
    `sides`: release_id -> {side letter: [positions in order]} for records with tracklists."""
    p = params(cfg)
    st = {"fuel": min(100.0, max(0.0, fuel0)), "alt": min(p["ceiling"], max(0.0, alt0)), "burns": 0, "skipped": 0,
          "last_burn": None, "peak": alt0, "launched": bool(launched0) if launched0 is not None else alt0 >= p["launch"]}
    t = start
    reentered = None
    heard, spin_key, last_end, loose = {}, None, None, 0.0
    for pl in plays:
        s, e = max(pl["start"], t), pl["end"]
        if e <= s or e <= start:
            continue
        hit = _silence(p, st, t, s)
        if hit is not None:
            reentered = hit
            break
        # listening fills the tank
        st["fuel"] = min(100.0, st["fuel"] + p["fill"] * (e - s))
        # a burn when a side plays through
        rid, pos = pl.get("release_id"), pl.get("position") or ""
        side = pl.get("side") or (pos[:1].upper() if pos[:1].isalpha() else "")
        if last_end is None or s - last_end > SPIN_GAP:
            heard, loose = {}, 0.0
        order = sides.get(rid, {}).get(side) if rid and side else None
        if order:
            key = (rid, side)
            heard.setdefault(key, set()).add(pos)
            if pos == order[-1] and len(heard[key]) >= 0.6 * len(order):
                _burn(p, st, e)
                heard[key] = set()
        else:
            loose += e - s
            while loose >= p["burn_seconds"]:
                loose -= p["burn_seconds"]
                _burn(p, st, e)
        t = max(t, e)
        last_end = e
    if reentered is None:
        reentered = _silence(p, st, t, now)
        if reentered is None:
            t = now
    # what happens next if nothing plays
    eta = None
    if reentered is None and st["launched"] and st["alt"] > p["reentry"]:
        hold_left = max(0.0, st["fuel"] - p["hold"]) / (p["drain"] + p["keep"])
        fall = math.log(st["alt"] / p["reentry"]) / p["k"]
        eta = (hold_left + fall) / 86400
    return {"fuel": round(st["fuel"], 1), "altitude": round(st["alt"], 2), "peak": round(st["peak"], 2),
            "launched": st["launched"], "burns": st["burns"], "skipped": st["skipped"], "last_burn": st["last_burn"],
            "holding": st["launched"] and st["fuel"] > p["hold"], "slipping": st["launched"] and st["fuel"] <= p["hold"]
            and reentered is None, "reentered_at": reentered, "eta_days": round(eta, 1) if eta is not None else None,
            "ceiling": p["ceiling"], "reentry_altitude": p["reentry"], "hold_level": p["hold"], "reserve": p["reserve"]}
