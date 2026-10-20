# Analog (nearest-neighbour) forecasting of short-horizon index returns: methodology and honest evaluation

Scope note for the report writer: the target system is the deterministic "Selloff Monitor" analog engine in `backend/app/services/selloff_monitor_service.py` (`index_features`, `find_analogs`, `_wilson`, `compare_to_baseline`). I read that code so the recommendations match what it does today. Observations about the code are marked "(code)". Numbers marked "(computed)" are my own scipy calculations from this session, not taken from a paper. Some sources were confirmed only through abstracts, repository records or DOIs, and the bullets say so. Where I relied on standard textbook results that I did not re-fetch, I put them under Inferences, not Cited Findings.

---

## 1. Nearest-neighbour / analog forecasting in finance and meteorology: evidence and pitfalls (sample size, scaling, k, kernel vs hard k-NN, dimensionality)

### Takeaway
Analog forecasting is old and well understood. Meteorology developed the theory: Lorenz (1969), van den Dool (1994) and the modern "analog ensemble". Finance offers only modest evidence. Nearest-neighbour predictors on stock indices usually lose to a random walk on point accuracy and at best show some directional or sign skill. Analog output can be honest when four conditions hold: low dimension, sensible scaling, enough independent analogs, and skill shown out-of-sample against climatology (the base rate). In the weather literature, feature weights and k come from optimizing an out-of-sample score, not from intuition.

### Cited Findings
- **Finance, stock-index NN evidence.** Fernández-Rodríguez, Sosvilla-Rivero and García-Artiles (1999, *Japan and the World Economy* 11(3):395–413) applied nearest-neighbour local predictors to the Nikkei 225 over 1986–1997. On Theil's U (point accuracy) the predictors did worse than a random walk. Their directional (sign) forecasts beat the random walk, and used as a trading filter they beat buy-and-hold over the full period and in bear-market subperiods — [ULPGC record/abstract](https://accedacris.ulpgc.es/handle/10553/49193?mode=full); [UCM record](https://produccioncientifica.ucm.es/documentos/5df8a9402999525886b70df3?lang=en)
- The same group later tuned NN-based trading rules for the Madrid market with genetic algorithms (González-Martel, Fernández-Rodríguez and Sosvilla-Rivero, 2012, *Progress in Financial Markets Research* 8:137–166). Searching parameters this way is itself data snooping (see §3). I did not read the abstract or results — [ULPGC record](https://accedacris.ulpgc.es/handle/10553/49183?locale=en)
- **Conditional vs unconditional design.** Lo, Mamaysky and Wang (2000, *JF* 55(4):1705–1765) automated pattern recognition with nonparametric kernel regression. They tested patterns by comparing the conditional distribution of returns after a pattern with the unconditional distribution, which is the same "analogs vs all-days baseline" design the monitor uses. On US stocks from 1962 to 1996, several patterns added incremental information, especially for Nasdaq names — [NBER w7613](https://www.nber.org/papers/w7613); [MIT copy](https://web.mit.edu/~alo/www/Papers/techanal.html); [CFA digest summary](https://rpc.cfainstitute.org/-/media/documents/article/cfa-digest/2001/dig-v31-n1-811-pdf.pdf)
- **Sample size and dimensionality: the meteorology lesson.** Van den Dool (1994, "Searching for analogues, how long must we wait?", *Tellus* 46A:314–324) derived an empirical relation linking library size, analog distance and degrees of freedom. From it he estimated that finding hemispheric analogs accurate to observational error would take a library on the order of 10^30 years. Lorenz (1969) had earlier put the requirement at about 140 years of upper-air data. Both figures come from secondary sources; I did not read the primary text — [arXiv 2607.14885 (secondary)](https://arxiv.org/pdf/2607.14885); [arXiv 2103.04530 (secondary)](https://arxiv.org/pdf/2103.04530); [NCAR WISE documentation](https://ral.ucar.edu/sites/default/files/public/images/events/WISE_documentation_20170725_Final.pdf); [NOAA CPC constructed-analogue readme (citation)](https://cpc.ncep.noaa.gov/products/people/wd51hd/sst/readme.html)
- After van den Dool, standard practice is to cut the feature-space dimension as far as possible before searching for analogs. His own answer to the problem was the "constructed analogue", a linear combination of past states that sits closer to today than any single natural analog — [Ifremer review](https://archimer.ifremer.fr/doc/00720/83222/88216.pdf); [NOAA CPC](https://cpc.ncep.noaa.gov/products/people/wd51hd/sst/readme.html)
- **Curse of dimensionality.** Beyer, Goldstein, Ramakrishnan and Shaft (ICDT 1999) showed that under broad conditions nearest and farthest neighbour distances converge as dimension grows, so "nearest" stops carrying information. The effect can appear at 10–15 dimensions. Durrant and Kabán (2009) proved the converse, giving conditions under which NN stays meaningful — [UW–Madison record](https://minds.wisconsin.edu/handle/1793/60174); [PDF](https://eclass.upatras.gr/modules/document/file.php/CEID1168/Papers/Beyer-WhenIsNearestNeighborMeaningful.pdf); [Durrant & Kabán](https://research.birmingham.ac.uk/en/publications/when-is-nearest-neighbour-meaningful-a-converse-theorem-and-impli)
- **Analog ensembles can be well calibrated.** The analog ensemble (AnEn) of Delle Monache, Eckel, Rife, Nagarajan and Searight (2013, *MWR* 141:3498–3516) builds a probabilistic forecast from the observations that followed the best past analogs of the current forecast. It showed high statistical consistency and reliability, tracked flow-dependent errors, and matched or beat logistic regression at lower real-time cost than EMOS — [UCAR OpenSky](https://opensky.library.ucar.edu/islandora/object/articles%3A12922)
- Hamill and Whitaker's reforecast-analog method (2006, *MWR*) found analogs on a long, fixed-model reforecast archive and then calibrated the result into bias-adjusted probabilities. Its analog products beat raw ensemble probabilities, by 2–3 days of lead time in some cases. The point for us is that a long, homogeneous archive plus explicit calibration is what makes analog probabilities reliable — [NOAA WPC description](https://www.wpc.ncep.noaa.gov/research/model_qpf_files/Page643.htm); [NCEP seminar abstract](https://www.emc.ncep.noaa.gov/seminars/old/abstract.2005/Whitaker.html)
- **Effective sample size of a weighted analog set** (Kish, 1965): n_eff = (Σ w_i)² / Σ w_i². It equals n when weights are equal and falls as weight concentrates on fewer analogs. The design effect is deff = n / n_eff = E[w²]/E[w]² — [Q Research wiki](https://wiki.q-researchsoftware.com/wiki/Kish%27s_Effective_Sample_Size_Formula); [balance package docs](https://import-balance.org/api_reference/html/balance.stats_and_plots.weights_stats.html)

### Inferences
- **Five dimensions are safe from distance concentration but not from collinearity.** At d = 5 the Beyer effect is mild. However, three of the features (52w drawdown, distance to the 200-day MA, log VIX) mostly measure one "stress level" factor. With diagonal standardization that factor effectively gets about 3× the nominal weight of a single feature, and the two σ-scaled move features get relatively less. Two fixes: (a) a Mahalanobis or whitened distance, d² = (x−x₀)ᵀ W^{1/2} Σ⁻¹ W^{1/2} (x−x₀), with Σ the feature covariance estimated as-of the origin; or (b) keep diagonal scaling and pick the weights by walk-forward score (see §7).
- **(code) The weights act on squared differences**: `d = sqrt(Σ w_j ((x_j − x0_j)/s_j)²)`. A weight of 1.5 therefore scales the 3-session-move axis by √1.5 ≈ 1.22 in distance units, not 1.5. Document this or change it so the intent is explicit.
- **(code) The scale uses full-sample `np.nanstd` over all valid days.** That is fine for the live forecast, since all of history is in the past. In any backtest or walk-forward evaluation it leaks future information: the scale must be computed only from data up to the forecast origin (expanding window).
- **Choice of k is a bias–variance trade-off.** For Lipschitz-smooth regression functions, standard nonparametric rate theory (e.g., Györfi et al., 2002; not re-fetched) gives an optimal k ∝ N^{2/(d+2)} = N^{2/7} at d = 5. Here N is the number of roughly independent forward windows (about 9,000 days / 20 ≈ 450), which puts k in the tens; the constants are unknown. Theory cannot pin k = 40, so choose k (and the weights) by walk-forward CRPS/Brier and show a k = 20/40/80 sensitivity strip instead of a single number.
- **Kernel vs hard k-NN.** Hard k-NN gives the 40th analog the same weight as the 1st, and the estimate jumps as analogs enter or leave the set. An adaptive kernel (Nadaraya–Watson with bandwidth h = distance of the (k+1)-th neighbour) smooths this: tricube w_i = (1 − (d_i/h)³)³ or Epanechnikov w_i = 1 − (d_i/h)². If the 40 distances were evenly spread on (0, h], tricube gives n_eff ≈ 27.8 and Epanechnikov ≈ 32.9 (computed). Kernel weighting is therefore not free: every interval must then use n_eff.
- **Analog quality / novelty.** The engine always returns 40 analogs, even for a state that has never happened (van den Dool's point). Report the k-th-neighbour distance today as a percentile of the historical distribution of k-th-NN distances (computed as-of each day). Above about the 90th–95th percentile, show a warning such as "few genuine precedents; treat as indicative".
- **Boundary bias in extremes.** In a record-breaking selloff every neighbour is less extreme than today, so the analog average is pulled toward calmer states. Show the analog-mean feature vector next to today's (a "match gap" row). One optional fix is a local-linear regression adjustment: regress each analog's outcome on its feature offset (x_i − x₀), weighted, and shift outcomes to x₀. This is the approach of regression-adjusted rejection ABC (Beaumont, Zhang and Balding, 2002; not re-fetched). Adopt it only if walk-forward scores improve.
- **Time clustering.** Non-overlapping analogs still pile up in a few crises (2008–09, 2020, 2022). Report the number of distinct episodes (for example, analogs more than 60 sessions apart count as separate episodes) and a decade histogram, so users can see when "n = 40" is really about 8 crises.

### Gaps
- I found no peer-reviewed source for "Mulloy pattern matching"; Patrick Mulloy is known for DEMA/TEMA moving averages, not analog forecasting. The likely intended lineage is local nearest-neighbour predictors from nonlinear dynamics (Farmer–Sidorowich), which Fernández-Rodríguez et al. build on, but I did not verify that here.
- I did not read the full texts of Fernández-Rodríguez et al. or Hamill & Whitaker (2006), or van den Dool's primary derivation; the details above come from abstracts and secondary sources.
- I found no published out-of-sample skill estimate for an S&P 500 multi-feature analog engine like this one. The engine's own walk-forward test (§6–7) is the only way to know.

---

## 2. Overlapping observations and serial correlation: is enforcing non-overlap enough? Block bootstrap for path statistics?

### Takeaway
Overlapping h-period outcomes produce MA(h−1) errors and badly overstate significance. The literature (Hansen–Hodrick, Newey–West, Valkanov, Boudoukh–Richardson–Whitelaw, Britten-Jones et al.) shows that overlap adds little real information. Enforcing non-overlap among the k analogs removes this mechanical dependence, which makes a binomial (Wilson) interval for P(up) roughly valid. It does not remove three other problems: regime and volatility clustering among analogs, the nesting of the 1/5/20-session statistics, and overlap in the all-days baseline and in any walk-forward score series. Use an episode (cluster) bootstrap for analog path statistics, and a block or stationary bootstrap (block length ≥ h) for baselines and for walk-forward skill scores.

### Cited Findings
- **Hansen and Hodrick (1980, *JPE* 88(5):829–853)** introduced the standard correction for regressions on overlapping k-period returns, where errors are MA(k−1). It is a truncated (uniform-kernel) HAC covariance with k−1 lags — [DOI](https://doi.org/10.1086/260910) (bibliographic; not re-fetched)
- **Newey and West (1987, *Econometrica* 55(3):703–708)** proposed a positive-semidefinite HAC covariance with Bartlett weights: Ω̂ = Γ̂₀ + Σ_{l=1}^{L} (1 − l/(L+1)) (Γ̂_l + Γ̂_lᵀ). With overlapping h-period targets, use at least L = h−1 lags — [DOI](https://doi.org/10.2307/1913610) (bibliographic; not re-fetched)
- **Valkanov (2003, *JFE*)** showed that in long-horizon regressions the t-statistics do not converge to well-defined distributions, OLS can be inconsistent, and R² is a poor fit measure. This explains why long-horizon regressions tend to find "significant" results. He proposes a rescaled t/√T statistic with simulated critical values — [author PDF](https://rady.ucsd.edu/_files/faculty-research/valkanov/long-horizon.pdf); [abstract](https://escholarship.org/uc/item/67b2h2gb)
- Hjalmarsson notes that Valkanov's asymptotics assume the horizon grows with the sample. For fixed horizons he instead proposes dividing the standard t-statistic by √h — [Federal Reserve IFDP 853](https://www.federalreserve.gov/Pubs/ifdp/2006/853/ifdp853.htm)
- **Boudoukh, Richardson and Whitelaw (2008, *RFS*), "The Myth of Long-Horizon Predictability".** With a persistent predictor and overlapping returns, estimates at different horizons are almost perfectly correlated even with no predictability: 99% between 1- and 2-year, and 94% between 1- and 5-year, dividend-yield estimates. Under the null, coefficients and R² grow roughly in proportion to the horizon, so "significance at several horizons" is not independent confirmation — [NBER w11841](https://www.nber.org/papers/w11841); [SSRN](https://papers.ssrn.com/abstract=862285)
- Boudoukh, Israel and Richardson ("Long Horizon Predictability: A Cautionary Tale") argue that overlapping long-horizon returns add little effective sample size, and that standard overlap-adjusted t-statistics greatly overstate significance. Source is a secondary summary — [Quantpedia summary](https://quantpedia.com/problems-with-a-long-horizon-predictability/)
- **Britten-Jones, Neuberger and Nolte (2011, *JBFA* 38:657–683).** Aggregating the regressor matrix turns an overlapping regression into an equivalent non-overlapping one. On the transformed model, OLS, White and Newey–West standard errors are asymptotically valid, and finite-sample performance beats the standard corrections — [City Univ. repository](https://openaccess.city.ac.uk/id/eprint/15212/); [RePEc](https://ideas.repec.org/a/bla/jbfnac/v38y2011i5-6p657-683.html)
- **Serial correlation and verification scores.** Wilks (2010, *QJRMS*) found that serial correlation inflates the sampling variance of the Brier score and Brier skill score, so uncorrected confidence intervals are too narrow — [DOI 10.1002/qj.709](https://doi.org/10.1002/qj.709) (finding as summarized in search results; full text not read)
- **Block bootstrap.** Künsch (1989, *Ann. Stat.* 17(3):1217–1241) introduced the moving-block bootstrap, and Politis and Romano (1994, *JASA* 89:1303–1313) the stationary bootstrap with geometric block lengths — [Künsch DOI](https://doi.org/10.1214/aos/1176347265); [Politis–Romano DOI](https://doi.org/10.1080/01621459.1994.10476870) (bibliographic; not re-fetched)
- Politis and White (2004, *Econometric Reviews* 23(1):53–70) give a data-driven optimal block length from flat-top lag-window spectral estimates. Patton, Politis and White (2009) corrected the algorithm. The R package `blocklength` implements the corrected version — [RePEc](https://ideas.repec.org/a/taf/emetrv/v23y2004i1p53-70.html); [blocklength README](https://cran.r-project.org/web/packages/blocklength/readme/README.html)

### Inferences
- **What non-overlap buys.** (code) `find_analogs` keeps an analog only if |i − j| > 20 for every analog already kept. The k forward windows therefore share no daily returns, and the 1/5/20-session up/down indicators are approximately independent Bernoulli draws. Daily-index returns have near-zero sign autocorrelation across non-overlapping months, so Wilson on n = 40 is a reasonable first approximation for P(up).
- **What it does not buy.**
  1. Volatility clustering. Adjacent non-overlapping windows inside one crisis have dependent magnitudes, so the fan quantiles and worst-close statistics have fewer effective observations than 40.
  2. Nesting across horizons. The h = 1, 5 and 20 statistics come from the same 40 paths, and the 20-session return contains the 5-session one. Per BRW, agreement across horizons is close to mechanical.
  3. Neighbour-selection randomness. Which 40 days are chosen is itself noisy, and a binomial CI ignores this.
- **The baseline is built from overlapping windows.** (code) The all-days P(up) uses every day's overlapping 20-session forward return. Assume Gaussian random-walk increments, and use the orthant formula P(X>0, Y>0) = ¼ + arcsin(ρ)/(2π) with ρ = (h − l)/h. The variance of a mean of overlapping sign indicators is then inflated by VIF = 1 + 2 Σ_{l=1}^{h−1} (2/π) arcsin(1 − l/h) ≈ **14.6 for h = 20** and **3.8 for h = 5** (computed). The baseline's effective N is about N/14.6 ≈ 600 for roughly 9,000 days, giving a baseline SE of about 2 pts at p ≈ 0.62. That is small next to the analog SE of about 7.7 pts, but not zero. A fully honest comparison uses SE_diff = √(p̂(1−p̂)/n_eff + p₀(1−p₀)·VIF/N).
- **Recommended CI procedures.**
  - *Analog path statistics* (P(new low within 5), P(reclaim within 20), first-passage shares, worst-close quantiles): use an **episode cluster bootstrap**. Group analogs into episodes (gap ≥ 60 sessions starts a new cluster). Resample clusters with replacement B = 2,000 times, recompute each statistic, and report the 2.5–97.5% percentiles. This widens the CI exactly when analogs come from only a few crises.
  - *Baseline path statistics* (every 3rd day, overlapping): use a **stationary bootstrap** on the origin sequence, with mean block length = max(h, Politis–White estimate). Thinning to every 3rd day does not remove the overlap.
  - *Walk-forward skill scores* (daily origins, h-day targets): use a block bootstrap with block length ≥ h, or a Diebold–Mariano test with Newey–West using h−1 lags (§6). Alternatively, score only non-overlapping origins (every 20th day) for a clean headline number.
- **The full-procedure bootstrap is the gold standard but expensive.** Resample history in blocks, re-run the k-NN search and recompute the statistic. This captures neighbour-selection variability. A cheap substitute is the k-sensitivity strip (k = 20/40/80) from §1.

### Gaps
- I found no paper that specifically studies k-NN analog statistics under "greedy non-overlap" selection, or the bias that selection introduces. The episode-bootstrap recommendation is my inference by analogy to cluster-robust inference and declustering, not a cited result.
- I did not verify the exact Wilks (2010) effective-sample-size formulas.

---

## 3. Multiple testing and data snooping: how to show many conditional statistics honestly

### Takeaway
A monitor that flags about 7 "significant vs baseline" statistics every day, built on features, weights, k and horizons chosen after looking at 1990–2026 history, is exposed to three kinds of multiplicity: within-screen, across days, and design-stage snooping. The literature's answers are bootstrap reality checks (White; Hansen SPA; Romano–Wolf StepM) for choosing among configurations, higher t-hurdles (Harvey–Liu–Zhu, t > 3), and deflating performance metrics by the number of trials (Bailey & López de Prado). For display: pre-declare one primary statistic, apply Holm or Benjamini–Hochberg across the displayed family, show shrunk estimates by default, and let a walk-forward skill badge make the final call.

### Cited Findings
- **White's Reality Check (2000, *Econometrica* 68(5):1097–1126).** Tests whether the best of many models beats a benchmark, using a bootstrap of the maximum performance statistic to correct for searching among them — [DOI](https://doi.org/10.1111/1468-0262.00152) (bibliographic; not re-fetched)
- **Sullivan, Timmermann and White (1999, *JF* 54(5):1647–1691)** ran the Reality Check over a large universe of technical rules on 100 years of daily DJIA data, expanding Brock–Lakonishok–LeBaron's 26 rules, to quantify and adjust for data-snooping bias. In a later out-of-sample check, the best in-sample rule earned a statistically insignificant 8.63% per year before costs — [LSE record](https://researchonline.lse.ac.uk/id/eprint/119144); [FMG discussion paper](https://www.fmg.ac.uk/sites/default/files/2020-11/dp303.pdf); [RePEc follow-up](https://ideas.repec.org/p/sin/wpaper/04-a003.html)
- **Hansen (2005, *JBES* 23:365–380), test for Superior Predictive Ability.** Improves on the Reality Check by studentizing losses and recentring the bootstrap. The RC loses power, possibly all of it, when many poor or irrelevant alternatives are included — [RePEc](https://ideas.repec.org/p/wyi/wpaper/002018.html); [arch docs](https://arch.readthedocs.io/en/latest/multiple-comparison/multiple-comparison-reference.html)
- **Romano and Wolf (2005, *Econometrica* 73(4):1237–1282), StepM.** A stepwise multiple-testing procedure that asymptotically controls the family-wise error rate and rejects more false nulls than single-step methods. It returns the set of models that beat the benchmark — [UPF working paper](https://econ-papers.upf.edu/papers/712.pdf); [arch docs](https://arch.readthedocs.io/en/latest/multiple-comparison/multiple-comparison-reference.html)
- **Harvey, Liu and Zhu (2016, *RFS* 29(1):5–68).** Given extensive data mining across the factor literature, a new factor should clear t > 3.0, not the usual about 2.0. Their framework allows for correlated tests and unpublished trials — [NBER w20592](https://www.nber.org/papers/w20592); [SSRN](https://papers.ssrn.com/abstract=2513152)
- **Bailey and López de Prado (2014, *JPM* 40(5):94), Deflated Sharpe Ratio.** Corrects a backtest Sharpe ratio for selection bias across multiple trials and for non-normal returns. A known weakness is that it needs an honest count of trials, which researchers often do not record — [SSRN 2460551](https://papers.ssrn.com/abstract=2460551); [CXO critique](https://cxoadvisory.com/big-ideas/sharper-sharpe-ratio)
- **Benjamini and Hochberg (1995, *JRSS-B* 57(1):289–300).** False-discovery-rate control: sort the m p-values, find the largest i with p_(i) ≤ (i/m)·q, and reject all hypotheses up to that i — [DOI](https://doi.org/10.1111/j.2517-6161.1995.tb02031.x) (bibliographic; not re-fetched)

### Inferences
- **Within-screen multiplicity.** (code) Each refresh flags P(up) at h = 1, 5 and 20 against baseline, and the page also shows new-low, reclaim and first-passage up/down against baselines, so about 7 comparisons. If they were independent and every null were true, P(at least one false "significant") would be 1 − 0.95^m: **14% for m = 3, 23% for m = 5, 30% for m = 7** (computed). The statistics are positively correlated, so the true rate is lower but still material.
- **Across-day multiplicity.** The monitor is read every day. A 5% false-flag rate per statistic means a spurious "significant edge" shows up regularly. A daily product should use a stricter threshold or shrinkage by default.
- **Design-stage snooping.** The five features, the 1.5 weight, k = 40 and the 20-session horizon were chosen by someone who knows how 2008, 2020 and 2022 played out. No within-screen p-value can correct for that. Only a walk-forward evaluation can (§6–7), ideally with configurations fixed before evaluation, and with SPA/StepM against climatology if several configurations are compared.
- **Display protocol.**
  1. **One primary statistic**, e.g. the 20-session P(up) vs baseline, tested at α = 0.05. Treat all others as secondary or descriptive.
  2. Apply **Holm** (step-down Bonferroni: compare sorted p_(i) to α/(m−i+1)) or **BH** at q = 0.10 across the secondary family before showing any "significant" badge.
  3. **Two-tier wording.** |z| ≥ 1.96 (and surviving Holm/BH) is "suggestive". |z| ≥ 3.0 is "strong", borrowing the Harvey–Liu–Zhu hurdle as a heuristic for daily-repeated screens.
  4. **Lead with shrunk estimates (§4) and the baseline**, show raw analog rates second, and never show an edge without its interval.
  5. Show a "trials" footnote listing the configurations that were tried during design, in the spirit of the Deflated Sharpe Ratio.

### Gaps
- I found no source that sets a multiple-testing standard specifically for daily-refreshed dashboards. The daily-multiplicity reasoning is my inference.
- I did not fetch the full Bailey–Borwein–López de Prado–Zhu "Probability of Backtest Overfitting" paper. The method (combinatorially symmetric cross-validation over many configurations) is relevant if several engine variants are compared, but I have not verified its details here.

---

## 4. Small-sample interval estimation and shrinkage toward the base rate

### Takeaway
At n = 40 the Wald interval is unreliable. Wilson (or Jeffreys) is the recommended small-n interval, and Clopper–Pearson is conservative. The monitor's current rule ("significant if the baseline is outside the 95% Wilson CI") is exactly a two-sided score test of H₀: p = p₀, which is defensible. However, its power is low: with a baseline near 62%, roughly a 10-point true edge is detected only about 28% of the time. Quantile fans from 40 points have very wide CIs, with P10 anywhere between the sample minimum and about P22. Shrink conditional estimates toward the base rate with a beta-binomial prior whose strength is tuned out-of-sample.

### Cited Findings
- **Brown, Cai and DasGupta (2001, *Statistical Science* 16(2):101–133).** The Wald interval is unreliable even at moderate n, and textbook rules about when it is safe are misleading. They recommend **Wilson or equal-tailed Jeffreys for small n** and **Agresti–Coull for larger n**. Agresti–Coull's coverage is somewhat larger and more conservative than the others — [Project Euclid](https://projecteuclid.org/euclid.ss/1009213286); [DOI](https://www.doi.org/10.1214/SS/1009213286)
- **Agresti and Coull (1998, *Am. Stat.* 52(2):119–126).** At 95%, "add two successes and two failures" and then use the Wald formula — [DOI](https://doi.org/10.1080/00031305.1998.10480550) (bibliographic); [Agresti & Coull 2001 note](https://users.stat.ufl.edu/~aa/articles/agresti_coull_2001.pdf)
- **Efron and Morris (1975, *JASA* 70(350):311–319).** The classic demonstration (baseball averages) that James–Stein / empirical-Bayes shrinkage of many noisy proportions toward their grand mean cuts total squared prediction error relative to the raw proportions — [DOI](https://doi.org/10.1080/01621459.1975.10479864) (bibliographic; not re-fetched)
- **Kish n_eff** for weighted samples (see §1) — [Q Research wiki](https://wiki.q-researchsoftware.com/wiki/Kish%27s_Effective_Sample_Size_Formula)

### Inferences — formulas the monitor can adopt (z = 1.96)
- **Wilson** (current code): centre = (p̂ + z²/2n)/(1 + z²/n), half-width = z·√(p̂(1−p̂)/n + z²/4n²)/(1 + z²/n). Because Wilson inverts the score test, "p₀ outside the Wilson CI" is the same as |p̂ − p₀| / √(p₀(1−p₀)/n) > 1.96. The current `significant` flag is therefore a correct score test that treats the baseline as known.
- **Agresti–Coull:** ñ = n + z², p̃ = (k + z²/2)/ñ, CI = p̃ ± z√(p̃(1−p̃)/ñ).
- **Clopper–Pearson (exact, conservative):** [Beta⁻¹(α/2; k, n−k+1), Beta⁻¹(1−α/2; k+1, n−k)].
- **Jeffreys:** quantiles of Beta(k + ½, n − k + ½).
- **Worked numbers at n = 40** (computed): 25/40 up gives Wilson [47.0%, 75.8%] and Clopper–Pearson [45.8%, 77.3%]. 32/40 gives Wilson [65.2%, 89.5%].
- **Detectable edge and power** (computed). With baseline p₀ ≈ 0.62 (illustrative; use the actual value), "significant" requires k ≤ 18 (≤45%) or k ≥ 31 (≥77.5%). That is about −17/+15 points of edge. Power when the true analog rate is 0.67 is 11%; at 0.72 it is 28%; at 0.77 it is 56%; at 0.52 it is 23%. Detecting a 10-point edge with 80% power needs n ≈ 176, and a 15-point edge needs n ≈ 76 (normal approximation). **Implication:** most real conditional edges after selloffs, which are likely only a few points, can never be "significant" at n = 40. The UI should say "not distinguishable from the base rate", not "no edge".
- **Kernel-weighted version.** p̂_w = Σ w_i 1{r_i > 0} / Σ w_i, with n_eff = (Σw)²/Σw². Use Wilson with n → n_eff and the score test z = (p̂_w − p₀)/√(p₀(1−p₀)/n_eff).
- **Including baseline uncertainty:** z = (p̂ − p₀)/√(p̂(1−p̂)/n_eff + p₀(1−p₀)·VIF/N), with VIF ≈ 14.6 for h = 20 (§2).
- **Shrinkage toward the base rate (beta-binomial empirical Bayes).**
  - Prior: p ~ Beta(m·p₀, m·(1−p₀)), where p₀ is the all-days base rate as-of the origin and m is a pseudo-sample size.
  - Posterior mean: **p̃ = (k + m·p₀)/(n + m) = λ·p̂ + (1−λ)·p₀, with λ = n/(n+m)**. For a weighted set, replace (k, n) with (p̂_w·n_eff, n_eff).
  - Credible interval: quantiles of Beta(k + m·p₀, n − k + m·(1−p₀)).
  - Example (computed): 30/40 up with p₀ = 0.62 shrinks to 70.7% (m = 20), 68.5% (m = 40) or 66.3% (m = 80), against the raw 75%.
  - Choosing m:
    - (a) *Method of moments across states:* among many historical origins, τ̂² = Var(p̂_t) − mean(p̂_t(1−p̂_t)/n_t) estimates the true between-state variance of conditional rates, and m = p₀(1−p₀)/τ̂² − 1. If τ̂² ≤ 0, set λ = 0 and report the base rate.
    - (b) *Preferred:* pick m to minimise walk-forward Brier score. This is the most defensible "how much to trust the analogs" knob, and it is the probability analogue of the Campbell–Thompson restrictions (§5).
  - The normal-theory James–Stein equivalent is shrinkage factor B = σ²/(σ² + τ²) with σ² = p₀(1−p₀)/n.
- **Quantile fans: distribution-free CIs from order statistics.** For iid samples, a CI for the q-quantile is [X_(r), X_(s)] with P(r ≤ Bin(n, q) < s) ≥ 0.95. At **n = 40** (computed):
  - P10: X_(1)–X_(9), i.e. from the sample minimum to about the 22nd sample percentile (coverage 97%).
  - P25: X_(5)–X_(16).
  - Median: X_(13)–X_(26).
  - P75: X_(25)–X_(36).
  - P90: X_(32)–X_(40), i.e. from about P80 to the maximum.

  So the P10–P90 edges of a 40-analog fan are very uncertain. Options: show P20/P80, add shaded uncertainty for the outer band (episode bootstrap, §2), or σ-normalize the analog paths (§7) so the fan borrows strength from the common shape of the paths.

### Gaps
- I did not retrieve Brown–Cai–DasGupta's explicit verdict on Clopper–Pearson from the paper itself; that it is conservative is standard.
- I found no finance paper that tunes beta-binomial shrinkage for analog-conditional hit rates. The recommendation combines Efron–Morris and Campbell–Thompson by analogy.

---

## 5. Regime conditioning and structural breaks

### Takeaway
Out-of-sample, most equity-premium predictors fail to beat the historical mean (Welch–Goyal). Modest restrictions such as sign and non-negativity constraints, or shrinking toward the mean, recover small but economically meaningful skill (Campbell–Thompson). Under structural breaks, the best estimation window can include pre-break data, and it varies over time (Pesaran–Timmermann). For the monitor this means: treat the analog result as a tilt on the base rate, shrink it, test it walk-forward, and show how sensitive it is to era.

### Cited Findings
- **Welch and Goyal (2008, *RFS* 21(4):1455–1508).** The usual predictors did poorly both in- and out-of-sample for about 30 years, were unstable, and would not have helped an investor time the market. None beat the prevailing historical mean out-of-sample — [NBER w10483](https://nber.org/papers/w10483); [PDF](https://www.johnhcochrane.com/s/Goyal_Welch.pdf)
- **Campbell and Thompson (2008, *RFS* 21:1509–1531).** Many predictive regressions beat the historical average out-of-sample once weak restrictions are imposed on coefficient signs and on return forecasts (e.g., no negative equity-premium forecasts). The OOS R² is small but economically meaningful for mean-variance investors. A truly predictive variable can still underperform OOS for long stretches — [NBER w11468](https://nber.org/papers/w11468)
- A replication vignette for Welch–Goyal finds that, for the dividend–price ratio at annual frequency, every out-of-sample statistic says it does not significantly beat the historical mean — [CRAN forecastdom vignette](https://ftp.fau.de/cran/web/packages/forecastdom/vignettes/wg2008-dp.Rmd)
- **Pesaran and Timmermann (2007, *J. Econometrics* 137:134–161).** When a regression has breaks, it can be optimal to use pre-break data for out-of-sample forecasts (a bias–variance trade-off). The best window depends on break size, distance to the break and predictability. They propose cross-validation and reversed-CUSUM methods to choose it — [author PDF](https://rady.ucsd.edu/_files/faculty-research/timmermann/estimation-window.pdf); [MPRA](https://mpra.ub.uni-muenchen.de/75004/1/MPRA_paper_75004.pdf)

### Inferences
- **OOS R² for the analog engine** (the Campbell–Thompson metric, adapted): R²_OS = 1 − Σ_t (r_{t,h} − r̂_{t,h})² / Σ_t (r_{t,h} − r̄_{t,h})², where r̄ is the expanding-window historical mean as-of t and r̂ is the analog median or mean. For probabilities, the equivalent is the Brier skill score against the expanding base rate (§6). Expect values near zero, and report them honestly even if negative.
- **Campbell–Thompson-style restrictions for analogs:**
  - (a) shrink P(up) toward the base rate (§4);
  - (b) cap the displayed tilt, e.g. never move the shrunk P(up) more than about 15 points from the base rate unless walk-forward reliability supports it;
  - (c) when the analog mean return is used, shrink it toward the unconditional drift.
- **Era sensitivity, as an honesty display rather than a model choice.** The 1990–2026 sample spans different regimes: pre/post-2008 policy "put", QE/ZIRP, and post-2020 options-market structure. Show the analog P(up) and median computed separately on pre-2008 and post-2008 analogs. If they disagree beyond their CIs, say so. Do not drop eras after the fact; that is snooping (§3).
- **VIX backfill caveat.** As I understand it (not verified this session), Cboe's current VIX methodology dates from 2003 and was back-calculated to 1990. Pre-2003 log-VIX is therefore a reconstruction, which is acceptable for analog matching but should be documented.
- **Regime conditioning is the analog engine's whole premise.** "Predictability concentrates in volatile or bear states" is the hypothesis being tested. The Fernández-Rodríguez Nikkei result (better in bear subperiods, §1) points that way but is weak evidence. Any such claim should be confirmed by a walk-forward evaluation restricted to stress origins.

### Gaps
- I did not fetch the literature on time-varying predictability (e.g., predictability concentrated in recessions or high-volatility states), so I cannot cite it here. It would support or temper the premise that selloff states are more predictable.
- I did not verify the Cboe VIX back-calculation details.

---

## 6. Calibration and evaluation of probabilistic forecasts (Brier, reliability/resolution, PIT, CRPS, reliability diagrams, walk-forward)

### Takeaway
Score the engine with strictly proper scores against climatology, the all-days base rate computed as-of each origin. Use the Brier score with its Murphy decomposition for P(up) and path events, and CRPS for the whole fan. Check calibration with PIT histograms and reliability diagrams with consistency bars, and check fan coverage. The target is the Gneiting–Balabdaoui–Raftery paradigm: maximize sharpness subject to calibration. Evaluate strictly walk-forward. Expect wide sampling uncertainty: the Brier skill score is biased, rare events need hundreds of forecast–outcome pairs, and serial correlation narrows naive CIs.

### Cited Findings
- **Gneiting and Raftery (2007, *JASA* 102(477):359–378).** A scoring rule is (strictly) proper if issuing the true distribution (uniquely) optimizes expected score. The CRPS, CRPS(F, y) = ∫ (F(x) − 1{x ≥ y})² dx, is strictly proper for distributions with finite first moment. An equivalent kernel form is CRPS(F, y) = E|X − y| − ½E|X − X′| with X, X′ iid ~ F — [DOI 10.1198/016214506000001437](https://doi.org/10.1198/016214506000001437); [UW tech report](https://apps.dtic.mil/sti/tr/pdf/ADA459827.pdf); [loo docs (kernel form)](https://mc-stan.org/loo/reference/crps.html)
- **Gneiting, Balabdaoui and Raftery (2007, *JRSS-B* 69(2):243–268)** frame probabilistic forecasting as maximizing sharpness subject to calibration, with PIT histograms as the main diagnostic of calibration — [DOI](https://doi.org/10.1111/j.1467-9868.2007.00587.x) (bibliographic; not re-fetched)
- **Diebold, Gunther and Tay (1998, *IER* 39(4):863–883).** If a density forecast is correct, the PITs u_t = F̂_t(y_t) are iid U(0, 1). This allows evaluation without knowing the true density or the user's loss function, illustrated on asset returns with time-varying volatility — [DOI 10.2307/2527342](https://doi.org/10.2307/2527342); [NYU working paper](https://archive.nyu.edu/handle/2451/14779); [RBA summary](https://www.rba.gov.au/publications/rdp/2008/2008-02/eva-den-forecasts.html)
- **Murphy (1973, *J. Appl. Meteor.* 12:595–600)** split the Brier score into reliability (miscalibration), resolution (discrimination) and uncertainty: E[(P − X)²] = Var(X) − Var_P[E(X|P)] + E_P{[E(X|P) − P]²}. Siegert (2017) rewrote reliability and resolution as Brier differences against recalibrated and climatological forecasts — [arXiv 2106.14345 (restates)](https://arxiv.org/pdf/2106.14345); [Siegert, Exeter ORE](https://ore.exeter.ac.uk/rest/bitstreams/141507/retrieve); [SpecsVerification docs](https://www.rdocumentation.org/packages/SpecsVerification/versions/0.3-0/topics/BrierScoreDecomposition)
- **Bröcker and Smith (2007, *Weather and Forecasting* 22(3):651–661).** Consistency bars, built by resampling under the hypothesis of perfect reliability, show how far observed frequencies can stray from the diagonal through sampling noise alone. They also propose plotting on probability paper — [LSE record](https://researchonline.lse.ac.uk/id/eprint/22217); [preprint](https://www.lse.ac.uk/CATS/Assets/PDFs/Publications/Papers/2007/73-IncreasingReliabilityDiagrams-2006-Brocker-Smith.pdf)
- **Bradley, Schwartz and Hashino (2008, *Weather and Forecasting* 23:992–1006).** The Brier score has an exact sampling variance and is unbiased. The Brier skill score against climatology is **biased** and needs approximate bias and variance formulas. For rare events, a few hundred forecast–outcome pairs may be needed to demonstrate skill — [DOI 10.1175/2007WAF2007049.1](https://doi.org/10.1175/2007WAF2007049.1); [Univ. of Iowa record](https://iro.uiowa.edu/esploro/outputs/journalArticle/Sampling-Uncertainty-and-Confidence-Intervals-for/9983991990102771)
- Serial correlation inflates Brier/BSS variance, so naive CIs are too narrow (Wilks 2010). The BSS has no standard parametric CI, and the bootstrap is the usual tool — [DOI 10.1002/qj.709](https://doi.org/10.1002/qj.709); [MET help thread](https://mailman.ucar.edu/pipermail/met_help/2019-July/003474.html)
- **Diebold and Mariano (1995, *JBES* 13(3):253–263).** Test equal predictive accuracy using the loss differential d_t = L(model) − L(benchmark): DM = d̄ / √(LRV̂(d)/T), with a HAC long-run variance (h−1 lags for h-step forecasts) — [DOI](https://doi.org/10.1080/07350015.1995.10524599) (bibliographic; not re-fetched)
- The analog ensemble literature reports AnEn reliability and statistical consistency as headline verification results, showing that analog methods are routinely held to these standards in meteorology — [UCAR OpenSky](https://opensky.library.ucar.edu/islandora/object/articles%3A12922)

### Inferences — the scores and diagnostics, as formulas
- **Brier score** (event o_t ∈ {0,1}, forecast f_t): BS = (1/T) Σ (f_t − o_t)².
  - Murphy, with K forecast bins: BS = REL − RES + UNC.
  - REL = (1/T) Σ_k n_k (f̄_k − ō_k)², RES = (1/T) Σ_k n_k (ō_k − ō)², UNC = ō(1 − ō).
  - Binning makes this only approximate. A bias-corrected decomposition exists (Ferro & Fricker; not fetched).
- **Brier skill score vs climatology:** BSS = 1 − BS_model / BS_clim, with BS_clim built from f_t = expanding-window base rate as-of t. Using the full-sample base rate as the reference leaks future information.
- **CRPS for the k-analog fan** (ensemble form, members x_i, outcome y): CRPS = (1/k) Σ_i |x_i − y| − (1/(2k²)) Σ_i Σ_j |x_i − x_j|.
  - For kernel weights: CRPS = Σ_i w_i |x_i − y| − ½ Σ_i Σ_j w_i w_j |x_i − x_j| (weights normalized to sum to 1).
  - Climatological reference: the empirical distribution of all past h-day returns as-of t.
  - CRPSS = 1 − CRPS_model / CRPS_clim.
  - A "fair" CRPS with 1/(2k(k−1)) exists for comparing ensembles of different sizes (Ferro 2014; not fetched).
- **PIT histogram:** u_t = (#{x_i < y_t} + U·#{x_i = y_t})/k, with U ~ Unif(0,1) to break ties, or the weighted ECDF. The histogram should be flat. A U-shape means the fan is too narrow (overconfident), a hump means it is too wide, and a slope means bias.
- **Central-interval coverage:** the empirical rate at which y_t falls inside [P10, P90] should be about 80%. Test with a binomial/Kupiec-style test on non-overlapping origins.
- **Quantile (pinball) loss** for each fan quantile q: QS_q = (1{y < q̂} − q)(q̂ − y).
- **Reliability diagram** for P(up), P(new low within 5) and P(reclaim within 20): bin forecasts into 5–10 bins, plot ō_k against f̄_k, and add Bröcker–Smith consistency bars.
- **Rolling-origin (walk-forward) protocol:**
  - Warm-up: 1990–1999, which also needs about 252 days for the 52w features.
  - For every origin t from 2000 onward, use only data up to t: the feature scale (and covariance, if used) as-of t, and candidate analog days i with i + h ≤ t, so the forward window is fully observed by t.
  - Run the engine exactly as in production (same k, weights, non-overlap rule, shrinkage) and score after h sessions.
  - Benchmark: the expanding base rate, plus the expanding empirical return distribution.
- **Two evaluation populations.**
  - (a) All days. This gives many observations, but skill on calm days says little about the monitor's job.
  - (b) **Stress origins only**, e.g. 3-session move ≤ −2σ or VIX in its top quintile, which are the days the monitor gets consulted. Here N is small, roughly 100–300 non-overlapping episodes, so CIs are wide. Show them.
- **Sampling uncertainty of the skill scores:**
  - Either (i) score only non-overlapping origins (every h-th day; e.g. h = 20 gives about 330 origins over 26 years) and use iid bootstrap or Bradley et al.-style formulas;
  - or (ii) use all daily origins with a stationary/moving-block bootstrap of the score differences d_t, block length ≥ h (Politis–White), or a DM test with h−1 Newey–West lags.

### Gaps
- I did not fetch the bias-corrected Brier decomposition (Ferro & Fricker) or the fair CRPS (Ferro 2014). They are mentioned as known refinements, not verified.
- I found no published walk-forward Brier/CRPS skill numbers for S&P 500 analog or selloff-conditional forecasters to serve as an external yardstick.

---

## 7. Concrete formulas and procedures the monitor should adopt

### Takeaway
Keep the deterministic k-NN core. Add four things: (1) kernel weights with Kish n_eff; (2) beta-binomial shrinkage toward the as-of base rate, with the prior strength tuned walk-forward; (3) episode-bootstrap and order-statistic CIs for path statistics and fans, plus a multiplicity-aware significance rule; (4) a walk-forward calibration badge (BSS and CRPSS vs climatology with block-bootstrap CIs, reliability, PIT, coverage) that tells users whether the engine has historically beaten the base rate. Without that badge, users have no basis for trusting the fan over the base rate.

### Cited Findings
- Kernel weighting / effective sample size: n_eff = (Σw)²/Σw² — [Kish via Q Research](https://wiki.q-researchsoftware.com/wiki/Kish%27s_Effective_Sample_Size_Formula)
- Small-n proportion intervals: use Wilson or Jeffreys — [Brown, Cai & DasGupta 2001](https://projecteuclid.org/euclid.ss/1009213286)
- Shrinkage toward a common mean beats raw proportions out-of-sample — [Efron & Morris 1975 DOI](https://doi.org/10.1080/01621459.1975.10479864)
- Restricted or shrunk forecasts beat unrestricted ones OOS in equity-premium prediction — [Campbell & Thompson, NBER w11468](https://nber.org/papers/w11468)
- Proper scores (CRPS, Brier) and PIT/reliability as the evaluation standard — [Gneiting & Raftery 2007](https://doi.org/10.1198/016214506000001437); [Diebold, Gunther & Tay 1998](https://doi.org/10.2307/2527342); [Bröcker & Smith](https://researchonline.lse.ac.uk/id/eprint/22217)
- BSS sampling uncertainty and bias; serial-correlation inflation — [Bradley et al. 2008](https://doi.org/10.1175/2007WAF2007049.1); [Wilks 2010](https://doi.org/10.1002/qj.709)
- Block-length selection for dependent bootstraps — [Politis & White 2004](https://ideas.repec.org/a/taf/emetrv/v23y2004i1p53-70.html); [blocklength](https://cran.r-project.org/web/packages/blocklength/readme/README.html)
- Choosing among engine variants without snooping: SPA / StepM — [Hansen 2005](https://ideas.repec.org/p/wyi/wpaper/002018.html); [Romano & Wolf](https://econ-papers.upf.edu/papers/712.pdf)
- Analog ensembles in weather are tuned and verified by out-of-sample reliability — [Delle Monache et al. 2013](https://opensky.library.ucar.edu/islandora/object/articles%3A12922)

### Inferences — implementable spec (keyed to the existing code)
1. **Distance (as-of origin).**
   - s_j = expanding-window SD (or 1.4826·MAD) of feature j using data ≤ t.
   - d_i = √(Σ_j w_j ((x_ij − x_0j)/s_j)²).
   - Document that w_j multiplies the squared difference (√1.5 effective scale on move3).
   - Optional: whitened or Mahalanobis distance to stop the drawdown/200d/VIX stress block from triple-counting.
   - Pick weights and k by walk-forward mean CRPS on stress origins, fixed before the final evaluation period. If several variants are compared, use SPA/StepM against climatology.
2. **Neighbour weights.** Keep the greedy non-overlap rule (|i − j| > h). Over the selected set, use tricube weights w_i = (1 − (d_i/d_(k+1))³)³. Report n, n_eff = (Σw)²/Σw², the number of distinct episodes (≥60-session gaps), and a decade histogram.
3. **Analog quality flag.** q_t = percentile of today's d_(k) among historical as-of k-th-NN distances. If q_t > 0.95, show "few close precedents". Show the analog-mean feature vector next to today's.
4. **P(up) and path probabilities.**
   - Weighted raw rate: p̂_w = Σ w_i o_i / Σ w_i.
   - **Display p̃ = (p̂_w·n_eff + m·p₀)/(n_eff + m)**, where p₀ is the as-of base rate. m is set by minimizing walk-forward Brier, or by method of moments; m ≈ 40 (λ = 0.5) is a reasonable prior default until tuned.
   - Interval: Beta(p̂_w·n_eff + m·p₀, (1−p̂_w)·n_eff + m·(1−p₀)) quantiles.
   - Also show the raw Wilson interval on n_eff for transparency.
   - For path events (new low within 5, reclaim within 20, first passage), add an **episode cluster bootstrap** CI (B = 2,000).
5. **Significance badge.**
   - z = (p̂_w − p₀)/√(p̂_w(1−p̂_w)/n_eff + p₀(1−p₀)·VIF_h/N_base), with VIF_20 ≈ 14.6 and VIF_5 ≈ 3.8 (Gaussian orthant approximation), or a block-bootstrap SE for the baseline.
   - Primary statistic: the 20-session P(up).
   - Secondary family: Holm or BH (q = 0.10).
   - Labels: "suggestive" for |z| ≥ 1.96 after correction, "strong" for |z| ≥ 3.
   - Default copy when not significant: "consistent with the base rate (this test can only detect ≥ ~15-point edges at n = 40)".
6. **Fans.**
   - Weighted quantiles of the analog paths. Either drop the outer band to P20/P80 or shade P10/P90 with their order-statistic or episode-bootstrap uncertainty (at n = 40, the P10 CI spans X_(1)–X_(9)).
   - Optional variant to test walk-forward: **σ-normalized paths**, i.e. r_i,h / (σ_pre,i √h) × σ_pre,now √h. This rescales each analog's path to today's pre-move volatility so 1990s low-vol and 2008 high-vol analogs are comparable. Keep it only if CRPS improves.
7. **Baselines.**
   - Keep the all-days baseline, and add a **simple conditional baseline**: all days with move3_sigma ≤ today's value (or ≤ −2σ). This shows what the five-feature analog search adds beyond a one-line filter.
   - (code) The reclaim baseline already conditions on down-3-session days, which is good practice.
   - Compute baselines as-of t in backtests.
8. **Walk-forward calibration badge (the headline honesty device).**
   - Build a nightly or monthly cached job that replays the engine from 2000 onward with no look-ahead.
   - Report per horizon (1/5/20), on all origins and on stress origins:
     - BSS for P(up) vs the expanding base rate, with a 95% block-bootstrap CI (block ≥ h);
     - Murphy REL and RES;
     - CRPSS for the fan vs the expanding climatological distribution, with CI;
     - P10–P90 coverage (target 80%);
     - a PIT histogram;
     - a reliability diagram with Bröcker–Smith consistency bars.
   - Badge logic:
     - green, "historically beat the base rate", only if the lower CI of BSS (or CRPSS) on stress origins is > 0;
     - grey, "no demonstrated skill vs base rate", if the CI straddles 0;
     - red, "historically worse than base rate; shrink fully", if the upper CI is < 0.
   - Feed the tuned shrinkage m back from this job. When skill is not demonstrated, m grows and the display converges to the base rate automatically.
9. **Options-implied cone overlay.** Treat it as a second forecaster and score it on the same walk-forward footing. A historical proxy is VIX: σ_h = VIX/100·√(h/252), lognormal with risk-neutral drift. Implied volatility includes a variance risk premium and the drift is risk-neutral, not physical, so the implied cone is expected to be systematically wide relative to realized outcomes. Its PIT should look humped. Compare CRPS for analog vs implied vs climatology, and consider showing the analog fan as "physical" next to the "market-implied" cone, not as a competing prediction of the same quantity. (The variance-risk-premium literature was not fetched here.)
10. **Copy and disclosure.** Methods footnote should cover: k, weights, non-overlap rule, n_eff and episode count, shrinkage m and its source, the multiplicity rule, the evaluation period and badge definition, the era composition of the analogs, and a list of design variants tried.

### Gaps
- No source gives a specific default for the prior strength m, the tricube choice or the 60-session episode gap for equity-index analogs. These are reasonable defaults to tune walk-forward, not literature constants.
- The VIF figure assumes Gaussian iid increments. Real fat tails and volatility clustering change it somewhat, and a block-bootstrap baseline SE is the empirical check.
- The claim that the options-implied cone runs wide relative to realized outcomes rests on the variance-risk-premium literature, which I did not fetch in this session.
