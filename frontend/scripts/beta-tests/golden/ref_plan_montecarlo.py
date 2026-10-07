# Independent Monte-Carlo reference for the FULL trade plan (fill first, then target-vs-stop), simulated directly
# on GBM PRICE paths so the long/short sign handling in the TS is checked, not assumed.
import numpy as np, json
BETA = 0.5826
def sim(direction, spot, entry, stop, target, iv, days, r=0.045, n=200000, steps=2000, seed=11):
    rng = np.random.default_rng(seed)
    T = days / 365.0; dt = T / steps; sd = iv * np.sqrt(dt); mu = (r - 0.5 * iv * iv) * dt
    sh = np.exp(BETA * sd)
    long = direction == 'long'
    # continuity-corrected simulation levels: move every barrier TOWARD the path by beta*sigma*sqrt(dt)
    def shift(level, above):   # above=True: level is above the path
        return level / sh if above else level * sh
    spot_above_entry = entry < spot          # entry below spot -> price must fall to fill
    e_lvl = shift(entry, above=not spot_above_entry)
    t_lvl = shift(target, above=long)
    s_lvl = shift(stop, above=not long)
    market = abs(np.log(entry / spot)) <= np.log(1.0015)
    lnS = np.full(n, np.log(spot)); filled = np.full(n, market); fill_step = np.full(n, 0 if market else -1)
    win = np.zeros(n, bool); loss = np.zeros(n, bool); done = np.zeros(n, bool)
    for k in range(steps):
        lnS += mu + sd * rng.standard_normal(n)
        S = np.exp(lnS)
        newfill = ~filled & ~done & ((S <= e_lvl) if spot_above_entry else (S >= e_lvl))
        filled |= newfill
        live = filled & ~done
        hit_t = live & ((S >= t_lvl) if long else (S <= t_lvl))
        hit_s = live & ((S <= s_lvl) if long else (S >= s_lvl))
        # a simultaneous hit within one step is counted as a loss (conservative; negligible at this resolution)
        loss |= hit_s; win |= hit_t & ~hit_s
        done |= (hit_t | hit_s)
    f = filled.mean()
    return dict(fill=float(f), win=float(win.sum() / max(1, filled.sum())), loss=float(loss.sum() / max(1, filled.sum())),
                inside=float(1 - (win.sum() + loss.sum()) / max(1, filled.sum())))
cases = [
  dict(name='long pullback (NVDA-style)', direction='long',  spot=238.90, entry=233.67, stop=229.87, target=259.31, iv=0.30, days=30),
  dict(name='long breakout',              direction='long',  spot=100.0,  entry=103.0,  stop=98.0,   target=112.0,  iv=0.35, days=20),
  dict(name='short pullback (rally fill)',direction='short', spot=50.0,   entry=52.0,   stop=54.5,   target=45.0,   iv=0.40, days=25),
  dict(name='long at market',             direction='long',  spot=60.0,   entry=60.05,  stop=57.5,   target=66.0,   iv=0.28, days=15),
  dict(name='short at market',            direction='short', spot=80.0,   entry=79.9,   stop=84.0,   target=72.0,   iv=0.33, days=12),
]
out = []
for c in cases:
    r = sim(c['direction'], c['spot'], c['entry'], c['stop'], c['target'], c['iv'], c['days'])
    out.append({**c, **r}); print(c['name'], {k: round(v, 4) for k, v in r.items()})
json.dump(out, open('joint_golden.json', 'w'))
