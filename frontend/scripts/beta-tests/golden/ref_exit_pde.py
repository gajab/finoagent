# Independent reference: P(exit through TOP barrier by tau) for  dX = m dtau + dB  on (-L, U), X0 = 0,
# solved by a fine Crank-Nicolson finite-difference of the backward equation (NOT the series used in TS).
import numpy as np, json, sys
from scipy.sparse import diags
from scipy.sparse.linalg import splu

def pde_top(L, U, m, tau, nx=4000, nt=4000):
    W = L + U
    x = np.linspace(-L, U, nx + 1); h = x[1] - x[0]
    xi = x[1:-1]
    # v_t = 0.5 v_xx + m v_x ; v(-L)=0, v(U)=1 ; v(x,0)=0 (interior)
    a = 0.5 / h**2; b = m / (2 * h)
    lo = np.full(nx - 2, a - b); di = np.full(nx - 1, -2 * a); up = np.full(nx - 2, a + b)
    A = diags([lo, di, up], [-1, 0, 1], format='csc')
    I = diags([np.ones(nx - 1)], [0], format='csc')
    dt = tau / nt
    M1 = (I - 0.5 * dt * A).tocsc(); M2 = (I + 0.5 * dt * A).tocsc()
    lu = splu(M1)
    # boundary source: the upper boundary value 1 enters the last interior row
    s = np.zeros(nx - 1); s[-1] = (a + b) * 1.0
    v = np.zeros(nx - 1)
    # Rannacher start: 4 implicit half-steps to damp the discontinuity at the corner
    Mi = splu((I - 0.25 * dt * A).tocsc())
    for _ in range(4):
        v = Mi.solve(v + 0.25 * dt * s * 2 * 0.5 + 0.25 * dt * s * 0 + 0.25 * dt * s)
    for _ in range(nt - 2):
        v = lu.solve(M2 @ v + dt * s)
    # evaluate at x=0 by linear interpolation
    return float(np.interp(0.0, xi, v))

def mc_top(L, U, m, tau, n=40000, steps=2000, seed=7):
    rng = np.random.default_rng(seed)
    dt = tau / steps; x = np.zeros(n); top = np.zeros(n, bool); bot = np.zeros(n, bool)
    alive = np.ones(n, bool)
    for _ in range(steps):
        x[alive] += m * dt + np.sqrt(dt) * rng.standard_normal(alive.sum())
        t = alive & (x >= U); b = alive & (x <= -L)
        top |= t; bot |= b; alive &= ~(t | b)
    return float(top.mean()), float(bot.mean())

cases = []
# (L, U, sigma, T_days, r)
spec = [
 (0.0164, 0.1040, 0.30, 30, 0.045),   # tight stop, far target (the NVDA-style plan)
 (0.0400, 0.0800, 0.30, 30, 0.045),
 (0.0500, 0.0500, 0.40, 15, 0.045),
 (0.0250, 0.0600, 0.25, 10, 0.045),
 (0.0800, 0.0300, 0.35, 45, 0.045),
 (0.0300, 0.0300, 0.20, 5,  0.045),
 (0.1000, 0.2500, 0.50, 90, 0.045),
 (0.0150, 0.0200, 0.45, 20, 0.0),
]
for L, U, sg, d, r in spec:
    T = d / 365.0; tau = sg * sg * T; m = (r - 0.5 * sg * sg) / (sg * sg)
    top = pde_top(L, U, m, tau)
    # bottom prob via mirror
    bot = pde_top(U, L, -m, tau)
    cases.append(dict(L=L, U=U, sigma=sg, days=d, r=r, top=top, bot=bot, open=1 - top - bot))
mc = mc_top(0.0164, 0.1040, (0.045 - 0.045) / 0.09, 0.09 * 30 / 365)
print(json.dumps(cases, indent=1))
print("MC check case0 (top,bot):", mc, " PDE:", cases[0]['top'], cases[0]['bot'])
json.dump(cases, open('fp_golden.json', 'w'))
