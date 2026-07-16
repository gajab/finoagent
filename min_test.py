import numpy as np
from scipy.optimize import minimize

np.random.seed(42)
ret_orig = np.random.normal(0.0005, 0.015, 252)
asset_returns = np.random.normal(0.0005, 0.015, (252, 9))

def obj_var(w):
    diff = ret_orig - (asset_returns @ w)
    return np.var(diff)

def obj_std(w):
    diff = ret_orig - (asset_returns @ w)
    return np.std(diff) * np.sqrt(252)

init_w = np.zeros(9)
init_w[0] = 0.7
init_w[1:] = 0.3/8

bnds = tuple((0,1) for _ in range(9))
cons = {'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}

print("Running Var obj...")
r1 = minimize(obj_var, init_w, method='SLSQP', bounds=bnds, constraints=cons)
print("Success:", r1.success)
print("Result X equals init_w?", np.allclose(init_w, r1.x))
print("Message:", r1.message)

print("\nRunning Std(Ann) obj...")
r2 = minimize(obj_std, init_w, method='SLSQP', bounds=bnds, constraints=cons)
print("Success:", r2.success)
print("Result X equals init_w?", np.allclose(init_w, r2.x))
print("Message:", r2.message)
