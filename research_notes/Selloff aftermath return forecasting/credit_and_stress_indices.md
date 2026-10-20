# Credit Spreads, Financial-Stress Indices and Cross-Asset Stress Measurement as Indicators of US Equity Drawdowns and Short-Horizon (1–20 day) Returns

Scope note: this covers credit and stress *indicators*, credit–equity lead-lag, cross-asset contagion and flight-to-safety (FTS), carry unwinds, and how to build a composite. Monitor-specific code pointers refer to `/Users/rahul/ClaudeCode/stock-research-standalone/backend/app/services/selloff_monitor_service.py`. Horizon flags: **[MACRO: months–quarters, too slow for a 1–20d monitor]**, **[SHORT: days–weeks]**.

---

## Q1. Excess bond premium (Gilchrist–Zakrajšek 2012) and successors: what horizon do they forecast, and is there any days-to-weeks evidence for equities?

### Takeaway
The GZ spread and EBP are **monthly macro predictors with a 12-month horizon** (recession or activity). The predictive content sits almost entirely in the EBP, the part of credit spreads that default risk inferred from equity does not explain. I found **no peer-reviewed evidence that the EBP, or bank-credit-conditions measures, forecast equity returns at 1–20 day horizons**. Their equity-return evidence runs at business-cycle frequency or up to 1 year. For a 1–20 day monitor, the useful idea is the *decomposition*: separate the part of credit moves that equity already explains from the residual.

### Cited Findings
- **GZ (2012, AER 102(4)).** The authors build a micro-level credit-spread index and split it into a default component and a residual, the EBP. EBP shocks that are unrelated to the current state of the economy are followed by weaker economic activity and lower asset prices. They read a rising EBP as a fall in financial-sector risk-bearing capacity, which tightens credit supply. **[MACRO]** — [AER abstract](https://www.aeaweb.org/articles?id=10.1257/aer.102.4.1692)
- **Construction (Fed note).** The GZ spread is an unweighted average of spreads on senior unsecured bonds of US non-financial firms. Spreads are regressed on distance-to-default (from equity value and leverage), ratings and call features. These explain about **70% of bond-level spread variation**, and the EBP is the residual. The series is monthly, Jan 1973–Mar 2016 (T=519). — [Favara, Gilchrist, Lewis & Zakrajšek, FEDS Note, Apr 2016](https://www.federalreserve.gov/econresdata/notes/feds-notes/2016/recession-risk-and-the-excess-bond-premium-20160408.html)
- **Effect size and horizon.** The outcome is an NBER recession at any point in months t..t+12, estimated by probit with Newey-West standard errors.
  - A **+50 bp rise in the EBP raises the 12-month recession probability by about 15 pp**. The same rise in the total GZ spread adds only about 7 pp.
  - The GZ spread's predictive content is "due entirely to the EBP"; the default component is statistically zero.
  - The unconditional 12-month recession probability is about 30%.
  - The **largest false positive was 2002**: spreads and the EBP rose after the accounting scandals, but no recession followed.
  - **[MACRO]** — [Favara et al., Apr 2016](https://www.federalreserve.gov/econresdata/notes/feds-notes/2016/recession-risk-and-the-excess-bond-premium-20160408.html)
- **Out-of-sample status.** Since Oct 2016 the Fed has published a monthly EBP and model recession probability. This is a real-time track record, but a monthly macro one. Releases can slip past the FOMC blackout. — [Favara et al., Oct 2016 update](https://www.federalreserve.gov/econres/notes/feds-notes/updating-the-recession-risk-and-the-excess-bond-premium-20161006.htm)
- **Bank credit conditions and stocks.** Tighter Senior Loan Officer Survey credit standards predict **lower subsequent stock returns**. The effect is strongest at **business-cycle frequency** and post-1990. It holds **in- and out-of-sample**, works through cash-flow news, and runs up to about 1 year in the working paper. The survey is quarterly. **[MACRO]** — [Chava, Gallmeyer & Park, JME 2015](https://ideas.repec.org/a/eee/moneco/v74y2015icp117-132.html); [working paper](https://finance.darden.virginia.edu/wp-content/uploads/2018/01/CGP-Credit-Conditions.pdf)

### Inferences
- The EBP and SLOOS cannot drive a daily trigger: they update monthly or quarterly, are published with a lag, and forecast 6–12 months ahead. At most, use them as a slow regime backdrop, e.g. "EBP above its historical 80th percentile" as a context chip.
- The *method* transfers to daily data. GZ show that the default part of spreads (inferred from equity) carries no extra macro information, and the residual does all the work. A daily analog is to regress HY spread changes on same-day and lagged equity returns, then call credit "confirming" only when the **residual** is large. Credit that widens just in line with the equity selloff is mostly an echo of equity (see Q2).
- The 2002 false positive shows that idiosyncratic credit events (fraud, sector blowups) can lift spreads without systemic stress. A single-market credit spike should be down-weighted unless other segments confirm (see Q3/Q6 on correlation weighting).

### Gaps
- I found no peer-reviewed study testing the EBP, GZ spread, or HY OAS *levels* or *changes* as out-of-sample predictors of S&P 500 returns over 1–20 days.
- The GZ AER paper's own forecasting horizons and the equity-price impulse response were not verified from full text; only the abstract was checked.

---

## Q2. Do HY spread changes lead equity moves at daily/weekly frequency? Implications for using credit as "confirmation"

### Takeaway
The weight of evidence (Kwan 1996; Norden & Weber 2009; Downing–Underwood–Xing 2009; Hilscher–Pollet–Wilson 2015; Tolikas 2018) is that **equity leads credit** at hourly, daily and weekly frequency. This holds especially for HY and lower-rated credit, and cash bonds are the slowest. The main exception is **HY index CDS (CDX.NA.HY)**: there the lead runs **both ways**, and CDS gains an edge **under adverse conditions**. So cash-bond HY OAS (FRED/ICE, evaluated prices, 1-day publication lag) is a **lagging, partly mechanical confirmer**. It is valuable as evidence that stress has broadened, not as a leading signal. Credit "not yet confirming" on days 1–2 of a selloff is weak evidence of calm.

### Cited Findings
- **Kwan (1996, JFE 40(1)).** Firm-level, weekly data. Stock returns and same-firm bond yield changes are **negatively correlated contemporaneously**. **Lagged stock returns explain current bond-yield changes, but not the reverse.** The working paper used 702 corporate bonds. No comovement for AAA bonds. — [IDEAS/RePEc](https://ideas.repec.org/a/eee/jfinec/v40y1996i1p63-80.html); [FRBSF Economic Letter Jul 1996](https://www.frbsf.org/research-and-insights/publications/economic-letter/1996/07/on-the-relation-between-stocks-and-bonds-part-ii/)
- **Norden & Weber (2009, European Financial Management).** 2000–2002, daily and weekly, VAR plus cointegration.
  - **Stock returns lead CDS and bond-spread changes.**
  - CDS changes Granger-cause bond-spread changes for more firms than the reverse.
  - **CDS is more sensitive to stocks than bonds are, and the sensitivity rises as credit quality worsens.**
  - Price-discovery effects are stronger for US firms than European ones.
  - Sources: [Mannheim repository](https://madoc.bib.uni-mannheim.de/1879/); [CEPR DP4674](https://cepr.org/publications/DP4674)
- **Downing, Underwood & Xing (2009, JFQA 44(5)).** Transaction data. **Hourly stock returns predict bond returns for HY and BBB non-convertible bonds**, and for convertibles in all rating classes. Bonds are less informationally efficient than stocks. — [IDEAS/RePEc](https://ideas.repec.org/a/cup/jfinqa/v44y2009i05p1081-1102_99.html)
  - **Conflict:** Hotchkiss & Ronen (2002) found HY bonds about as efficient as the underlying stocks, with no stock lead on firm-specific information. — [abstract listing](https://academicnewsletter.sufe.edu.cn/info/355670)
- **Hilscher, Pollet & Wilson (2015, JFQA 50(3), 543–567).** **Equity returns lead CDS (credit-protection) returns at daily and weekly frequency; CDS returns do not lead equity.** Information flows from equity to CDS. — [JFQA abstract](https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/are-credit-default-swaps-a-sideshow-evidence-that-information-flows-from-equity-to-cds-markets/F7CCD2FFB20AAEACE1E093D12EEF7A31); [working paper](https://ideas.repec.org/p/brd/wpaper/35.html)
- **Tolikas (2018, European Journal of Finance).** Daily data. Stock returns lead **HY bond returns but not IG bond returns.** — [IDEAS/RePEc](https://ideas.repec.org/a/taf/eurjfi/v24y2018i10p849-866.html)
- **Fung, Sierra, Yau & Zhang (2008, Journal of Alternative Investments).** Market level, 2001–2007. **Stocks lead the CDX IG index in pricing.** There is **two-way feedback between stocks and HY CDS**, and CDS indices lead equity *volatility*. — [UMSL profile/abstract](https://profiles.umsl.edu/en/publications/are-the-us-stock-market-and-credit-default-swap-market-related/)
- **Procasky (2021, Journal of Financial Markets 54).** CDX IG/HY versus matched equity portfolios, using VAR, asymmetric Granger and VARMA. For **IG, neither market leads**. For **HY, there is a strong two-way effect, and the CDS advantage is more pronounced under adverse market conditions and in CDX.NA.HY.** — [IDEAS/RePEc](https://ideas.repec.org/a/eee/finmar/v54y2021ics1386418120300501.html)
- **Bond-ETF price discovery in stress.** In March 2020 some IG and HY bond ETFs traded at **6–10% discounts to NAV** in extreme cases, for short periods. — [IOSCO report](https://www.iosco.org/library/pubdocs/pdf/IOSCOPD682.pdf)
  - BIS: the discounts reflected slower NAV updating in illiquid corporate-bond markets and reduced dealer support. — [BIS Bulletin 6](https://www.bis.org/publ/bisbull06.pdf)
  - ICMA/MSCI: the discounts largely showed where the underlying market was actually trading, so ETFs served as a price-discovery venue. — [ICMA](https://www.icmagroup.org/assets/documents/Regulatory/AMIC/ICMA-response-ETF-survey-IOSCO-010321.pdf); [MSCI](https://support.msci.com/www/blog-posts/bond-etfs-and-underlying-price/01769924776)
  - These are industry and regulatory sources, not peer-reviewed.

### Inferences
- **Credit as confirmation must be lag-aware.** The monitor reads HY OAS over the same 3-session window as SPY (`diagnose_stress`, lines ~699–712: `oas_chg >= 15 bp or HYG z <= -1.5`). Because equity leads cash credit by roughly one or more days, and FRED OAS adds a 1-day publication lag, a fast equity selloff can sit in "Equity-only de-risking" on days 1–2 just from timing.
  - Options: compute credit over a window ending one session later than equity, or label it "credit pending" rather than "calm" when the OAS print is stale (`obs_date` < last session).
- **The informative credit signal is the *excess* move, not the raw move.** Regress daily ΔHY-OAS (or HYG excess return) on SPY returns at lags 0..2 over a trailing ~250-day window, and z-score the cumulative residual over the window.
  - A residual ≥ +1σ means credit is worse than equity implies. This is the daily analog of GZ's EBP-vs-default split and the closest thing to "independent" confirmation.
  - Raw OAS widening proportional to the equity drop adds little new information.
- **Use HYG price (timelier) over FRED OAS when they disagree.** ETF prices update intraday and led NAV/evaluated prices in March 2020. HYG's premium/discount to NAV is itself a stress flag; a deep discount means cash HY is illiquid.
- **HYG contains duration.** In a rates-led selloff (2022-style), HYG can fall on Treasury moves alone. Use HYG minus β×IEF (or a duration-matched Treasury ETF) so rate moves are not mistaken for credit stress.
- **Spread changes are level-dependent.** Norden & Weber find sensitivity rising as quality worsens, so a fixed +15 bp threshold means different things at OAS 280 bp and at 600 bp. A log-change z, or a threshold proportional to the OAS level, is more consistent.

### Gaps
- No study found specifically on daily lead-lag between **HYG/JNK ETF returns** (or the FRED ICE OAS series) and SPY. Evidence is firm-level or CDS/CDX-index level.
- No peer-reviewed evidence that a bond-ETF NAV discount predicts subsequent equity returns. March 2020 evidence is descriptive.
- Hotchkiss & Ronen (2002) and Downing et al. (2009) disagree on HY bond efficiency (daily vs hourly, different samples). Not resolved here.

---

## Q3. Financial-conditions and stress indices (NFCI/ANFCI, STLFSI, OFR FSI, KC FSI, ECB CISS): how are they built, and what do they predict?

### Takeaway
All five combine many market indicators into one number, but they differ in frequency (daily OFR; weekly NFCI/STLFSI/CISS) and in weighting (PCA/factor loadings vs CISS's portfolio-theoretic **correlation weighting**). Their documented predictive power is for **macro activity two to four quarters ahead** or for downside GDP tails. It is not for 1–20 day equity returns. The CISS design is the most transferable to a daily monitor:
- empirical-CDF transform of each indicator;
- five market segments;
- an EWMA cross-correlation matrix so that **stress counts more when it is simultaneous across segments**;
- endogenous (Markov-switching or threshold-VAR) regime thresholds instead of arbitrary σ cut-offs.

### Cited Findings
**ECB CISS (Holló, Kremer & Lo Duca 2012, ECB WP 1426)** — [PDF](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf)
- **Structure.** 15 mostly market-based measures, 3 per segment, across 5 segments: money market, bond market, equity market, financial intermediaries, FX.
  - Equity measures: realized volatility (weekly average of absolute daily log returns); **CMAX**, the maximum cumulated loss over a moving 2-year window (T=104 weeks); and a **stock–bond correlation stress measure**.
  - The correlation measure is the 4-year (1040 business days) minus the 4-week (20 business days) stock–bond return correlation, **floored at zero**. It registers stress when short-run correlation falls below long-run, i.e. a flight to quality.
  - Bond measures include realized volatility of 10y yields, the A-rated corporate minus government spread, and the 10y swap spread. FX measures are realized volatility of three major FX pairs.
- **Transform.** Each raw indicator becomes an **order statistic from its recursive (expanding-sample) empirical CDF**, so it lies in (0,1]. Inside each segment, the three indicators are simply averaged. The goal is robustness to new data and avoiding the "event reclassification" that hits FSIs relying on stable distributions in small samples.
- **Aggregation.** **CISS_t = (w∘s_t) C_t (w∘s_t)'**, where C_t is the matrix of time-varying cross-correlations between the 5 subindices. Correlations are **EWMA with λ = 0.93 (weekly)**, applied to subindices demeaned by their theoretical median of 0.5. The robustness check with λ = 0.89 (spikier) and λ = 0.97 (smoother) left the broad regime classification unchanged.
  - "The CISS puts relatively more weight on situations in which stress prevails in several market segments at the same time."
- **Weights.** "Real-impact" weights come from VAR impulse responses of industrial production: money 15%, bond 15%, equity 25%, intermediaries 30%, FX 15%. The authors note the CISS with these weights and with **equal weights "are not very large"** apart (footnote 10).
- **Design notes.**
  - Applying correlation weights *inside* a subindex was rejected, because then changes in correlation would dominate (footnote 9).
  - The "variance-equivalent" (unsquare-rooted) CISS is preferred for monitoring because it separates stress from calm more sharply (footnote 11).
- **Robustness of the recursive build.** The recursive and full-sample proxy-CISS differ by an average absolute 0.015 (SD 0.022). The largest gap was 0.076, in Feb 2008.
- **Regimes.** A 3-regime Markov-switching AR model gives average monthly log industrial-production growth of **+0.23% (low), +0.17% (intermediate), −0.38% (crisis)**.
  - A **threshold VAR** estimates a CISS threshold of **0.3233** (second lag). Below it, CISS shocks have **no significant output effect**; above it, industrial production "truly collapses."
  - The authors contrast this with the common practice of an arbitrary "2 SD above mean" threshold. **[MACRO]**
  - The successor SovCISS uses the same correlation-weighted aggregation. — [ECB WP 2185](https://ecb.europa.eu/pub/pdf/scpwps/ecb.wp2185.nl.pdf)

**Chicago Fed NFCI / ANFCI (Brave & Butters)**
- **Construction.** A weighted average of **105 measures** of financial activity, each standardized. Weights come from a factor model's explanatory power for the common variation. Mean 0 and SD 1 since 1971.
- **Release.** **Weekly**, Wednesdays 8:30 ET, covering data through the prior Friday.
- **ANFCI** strips out variation in the indicators attributable to economic activity (3-month CFNAI) and inflation. That makes it conditions *relative to the economy*.
- **Forecasting.** Brave & Butters (2011) show the indexes help forecast **GDP and business-investment growth 2–4 quarters ahead**. **[MACRO]**
- Sources: [Chicago Fed NFCI FAQ](https://www.chicagofed.org/-/media/publications/nfci/nfci-faqs-pdf.pdf); [NFCI page](https://www.chicagofed.org/research/data/nfci/current-data)

**Adrian, Boyarchenko & Giannone (2019, AER 109(4), "Vulnerable Growth")**
- Worse financial conditions (NFCI) **lower the conditional mean and raise the volatility of future GDP growth**: lower quantiles move a lot, upper quantiles stay stable.
- Financial conditions forecast **downside** risk; economic conditions only forecast the median.
- The result holds **in- and out-of-sample** at roughly a 1-year horizon. **[MACRO]**
- Sources: [AER](https://www.aeaweb.org/doi/10.1257/aer.20161923); [Liberty Street Economics](https://libertystreeteconomics.newyorkfed.org/2018/04/vulnerable-growth)

**St. Louis Fed FSI (Kliesen & Smith 2010; current STLFSI4)**
- **First principal component of 18 weekly series**: 7 interest rates, 6 yield spreads, 5 others. Zero means normal stress.
- Version 2.0 was revised in 2020 because the original missed some stresses. STLFSI4 replaced a backward-looking SOFR average with the 90-day forward-looking SOFR.
- Sources: [Kliesen & Smith appendix](https://files.stlouisfed.org/files/htdocs/publications/net/NETJan2010Appendix.pdf); [St. Louis Fed 2020](https://www.stlouisfed.org/on-the-economy/2020/march/financial-stress-index-version-2); [FRED STLFSI4](https://fred.stlouisfed.org/series/STLFSI4)

**OFR FSI (Monin 2017 WP; Risks 2019)**
- **Construction.** **33 market variables** in 5 categories (credit, equity valuation, funding, safe assets, volatility) across 3 regions (US, other advanced, EM).
- **Weights.** An algorithm estimates co-movement weights; each variable enters as its level relative to its own history. The index "respects the arrow of time" (past values do not revise).
- **Release.** Computed **daily**, published with about a **2-business-day lag**. Zero means average stress.
- **Validation.** 2000–2018 data. A logistic regression treats government interventions as stress events, and the FSI identifies systemic stress well. It **Granger-leads the Chicago Fed National Activity Index**. **[MACRO validation]**
- Sources: [OFR FSI page](https://www.financialresearch.gov/financial-stress-index/); [Monin, Risks 2019](https://ideas.repec.org/a/gam/jrisks/v7y2019i1p25-d209064.html); [OFR WP 17-04](https://www.financialresearch.gov/working-papers/files/OFRwp-17-04_The-OFR-Financial-Stress-Index.pdf)

**Short-horizon equity evidence for FSIs (weak)**
- A non-peer-reviewed practitioner analysis found the **weekly change** in STLFSI had **no predictive power for next-week S&P returns**. The **level** was more informative: the worst and most variable returns came at the highest stress readings. Low-quality source. — [Seeking Alpha](https://seekingalpha.com/article/427561-financial-stress-indicator-a-guide-to-market-returns)
- Peer-reviewed predictability studies mostly test **monthly** returns. One example is a China FSI that predicts lower subsequent monthly returns, including out of sample. — [Pacific-Basin Finance Journal 2023](https://www.sciencedirect.com/science/article/abs/pii/S0927538X2300046X)

### Inferences
- Use NFCI, ANFCI and STLFSI only as **weekly regime context** (e.g. "financial conditions tighter than X% of history"). Use the OFR FSI as the closest-to-daily official cross-check, accepting its 2-day lag. None should be a 1–20 day trigger.
- The CISS stock–bond indicator (4-year minus 4-week correlation, floored at 0) is one-sided by construction. It flags flight-to-quality decorrelation but reads zero when stocks and bonds fall **together** (2022-style). A monitor needs both directions as separate signals (see Q4).
- CISS uses **levels** of stress measures (realized vol, CMAX drawdown, spreads), while the monitor uses **daily-move z-scores**. Levels capture the *state*; moves capture the *shock*. Both are needed: a 2σ move from a calm base is a different event from the same move inside a CISS-like high-stress regime, where the TVAR shows the real-economy link turns on.
- **Daily translation of CISS λ = 0.93 (weekly).** The weekly half-life is ln0.5/ln0.93 ≈ 9.6 weeks ≈ 48 trading days, giving a daily λ ≈ 0.9856. This is my arithmetic, not from the paper.

### Gaps
- **Kansas City Fed FSI** (Hakkio & Keeton 2009: monthly, PCA of about 11 variables, from memory) — construction **not verified this session**; treat as unconfirmed.
- No peer-reviewed out-of-sample test was found of NFCI/STLFSI/OFR/CISS predicting **S&P 500 returns at 1–20 day horizons**.
- I could not read OFR WP 17-04 in full (exact factor method and weights unverified beyond the OFR's own description).

---

## Q4. Cross-asset contagion, correlation spikes and flights to safety: what does a stock–bond correlation sign switch imply?

### Takeaway
A true **flight to safety (FTS)** has three parts: a large equity loss, a large Treasury gain, and sharply **negative** high-frequency stock–bond correlation (about −0.58), in a high-volatility state. FTS spells are **rare (<2% of days) and short-lived** (62% last 1 day, none over 10 days), come with yen and CHF strength, and coincide with a **spike in expected equity returns**. The stress is acute but transient, which is relevant to selloff-aftermath forecasting. When stocks and bonds fall **together**, the episode is an inflation/real-rate regime (positive stock–bond correlation), not an FTS. Bonds then do not hedge, and 60/40 volatility is higher. That is a different, more persistent kind of selloff.

### Cited Findings
**Baele, Bekaert, Inghelbrecht & Wei (2020, RFS 33(2), "Flights to Safety")** — [PDF](https://business.columbia.edu/sites/default/files-efs/citation_file_upload/Flights%20to%20Safety.pdf); [NBER w19095](https://www.nber.org/system/files/working_papers/w19095/w19095.pdf)
- **Definition.** FTS requires all three of:
  1. a large positive bond return with a large negative equity return;
  2. negative high-frequency stock–bond correlation;
  3. high equity volatility.
  - Identified for 23 countries from stock and bond returns only, by averaging a regime-switching model, a **threshold model** (exceedance-correlation style), and an **ordinal model** based on CISS.
- **Incidence and size.** FTS days are **<2% of the sample**. The **bond-minus-equity return differential averages 2.7%** (2.72%). On the first day of a spell it is 2.79%, a 2.3 SD move.
  - The regime model's population incidence is 4.66%, used to calibrate the other models.
- **Persistence.** **62% of spells last 1 day, 23% 2 days, 9% 3 days, 6% 4 days or more; none longer than 10 days.** About 94% last 3 days or fewer.
- **Correlation.** Stock–bond correlation on FTS days averages **−58.5%** (IQR −57.0% to −62.5%).
- **Around the event.** Outside spells, equity and bond returns are statistically indistinguishable from zero. On the **day before** a spell, equity returns are slightly but significantly **positive** and bonds slightly negative. FTS onset carries an equity jump of −5.58%; subsequent FTS days are much smaller (−0.13%).
- **Expected returns.** In the high-volatility FTS regime, the expected equity return is **15.24% above the country mean** (units as in the paper's Table 2, likely annualized; not verified). The authors: "only FTS events generate steep, though short lived, spikes in expected equity returns."
- **Cross-asset signature on FTS days.**
  - Safe-haven FX appreciates: **yen +0.44%, CHF +0.33%, USD +0.07%**. Yen and CHF effects are significant in 20+ countries; the USD effect is mixed.
  - **US TED spread +5 bp**, but only +1 bp on average across countries and significant in 5 of 19. Conclusion: "FTS episodes … are not always a credit event."
  - Gold is about +3 bp raw, but **+0.20%/day after controlling for equity beta**.
  - Implied volatility rises and consumer confidence falls.
- **Real-time threshold model (implementable).**
  - FTS on day t requires r_bond > 0, r_equity < 0, and a joint bivariate-normal tail probability P_obs below a threshold set by κ·σ.
  - **κ = 1.26** reproduces the 4.66% target incidence.
  - Volatilities and long-run correlation come from a **backward kernel with a 360-day bandwidth excluding the last 5 days**.
  - Results hold with fat-tailed t-distributions.
- **Fund flows.** US FTS days bring flows out of equity funds into government-bond and money-market funds. Hedge funds appear to shift exposures *before* FTS.

**Connolly, Stivers & Sun (2005, JFQA 40(1))**
- After high implied volatility (VIX-type) or abnormally high turnover, the **probability of a negative daily stock–bond correlation over the next month is several times greater**.
- Bond returns are relatively high on days when implied vol rises. Data: 1986–2000. **[SHORT: ~1 month]**
- Sources: [IDEAS/RePEc](https://ideas.repec.org/a/cup/jfinqa/v40y2005i01p161-194_00.html); [Atlanta Fed WP](https://www.atlantafed.org/-/media/documents/research/publications/wp/2002/wp0203a.pdf)

**Molenaar, Sénéchal, Swinkels & Wang (2024, FAJ 80(3))**
- Data back to 1875 across several countries. **Inflation, real short-rate (T-bill) returns, inflation uncertainty and government creditworthiness** drive the sign and size of stock–bond correlation.
- **Positive** correlation is most common under high inflation and high real T-bill returns.
- Positive-correlation regimes come with **higher 60/40 volatility and lower Sharpe ratios**.
- Source: [CFA Institute / FAJ](https://rpc.cfainstitute.org/research/financial-analysts-journal/2024/empirical-evidence-on-the-stock-bond-correlation)

**2022 episode (secondary source)**
- S&P 500 −18.1% and long Treasuries −26.1% in the same year, both double-digit. — [Alpha Architect summary](https://alphaarchitect.com/stock-bond-correlation/) (secondary; not independently verified)

### Inferences
- The monitor's rates lens (`diagnose_stress` ~729–738: TLT > +0.3% means "flight to quality", TLT < −0.3% means "rates/inflation-driven") points the right way, but uses a raw-percentage threshold. Upgrades:
  1. Replace the raw ±0.3% with a **Baele-style joint-tail FTS test**: SPY down, TLT up, joint probability below the κ=1.26 threshold, σ and ρ from roughly a 1-year window ending 5 sessions before the event.
  2. Add a **stock–bond correlation regime chip**: the 1-year rolling SPY/TLT daily correlation sign (Molenaar) plus the CISS-style "long-run minus 20-day correlation" decorrelation measure.
- **Interpretation rules suggested by the evidence:**
  - **FTS signature** (bonds up, ρ_20d strongly negative, yen/CHF up, VIX up) → acute, typically 1–3 day stress; expected-return spike; consistent with mean reversion in the aftermath.
  - **Equities and bonds both down while the 1-year correlation is positive** → inflation/real-rate selloff. Bonds will not cushion it, it is more regime-like and persistent, and the "Broad risk-off" credit/VIX rules may under-call it because spreads can stay calm.
- FTS is often **not a credit event** (TED barely moves outside the US). Requiring credit confirmation for "Broad risk-off" will miss many acute FTS days. The monitor could carry a separate "Flight to safety" label alongside "Broad risk-off (credit-confirmed)".
- **Yen strength is a generic FTS marker** (+0.44% on average FTS days), so yen up alone does not imply a carry unwind (see Q5).

### Gaps
- The FTS paper's **post-spell (t+1..t+5) equity returns** appear only in Figure 4 (cross-country averages). I did not extract a US-specific number, and the expected-return spike is model-implied, not an out-of-sample trading test.
- No study found that directly measures S&P 1–20 day forward returns conditional on a stock–bond correlation **sign switch**.

---

## Q5. Carry-trade unwinds (yen) and equity crashes

### Takeaway
Carry trades carry **negatively skewed crash risk**: unwinds cluster when risk appetite and funding liquidity fall, signalled by a **rising VIX**. Losses force further unwinding, so moves are fast and correlated across funding currencies. The August 2024 yen episode (TOPIX −12% in one day, VIX at Covid-era highs) was amplified by leveraged equity and FX positions, then **stabilized quickly**. That is consistent with acute, short-lived stress rather than a lasting regime.

### Cited Findings
- **Brunnermeier, Nagel & Pedersen (2008; NBER Macroeconomics Annual 2008, vol. 23)** — [NBER w14473](https://www.nber.org/papers/w14473)
  - Carry-trade currency returns are **negatively skewed** (crash risk), driven by sudden unwinds when **risk appetite and funding liquidity decrease**.
  - Skewness worsens as the **VIX rises**. Higher VIX goes with speculators cutting carry positions and with carry losses.
  - **Carry losses reduce future crash risk but raise its price.**
  - Currencies with similar interest rates show excess co-movement.
  - Consistent with funding-liquidity-constraint ("liquidity spiral") models.
- **BIS Bulletin No. 90 (Aquilina, Lombardi, Schrimpf & Sushko, 27 Aug 2024)** — [BIS](https://www.bis.org/publ/bisbull90.htm)
  - Peak stress came on **5 Aug 2024: TOPIX −12% in a single day; VIX briefly at levels not seen since Covid**.
  - **Unwinding of leveraged trades in equity and currency markets amplified** the initial reaction to a weak US macro release.
  - The yen carry trade was roughly **¥40 trillion (~$250bn)** before the event, "biased down due to data gaps."
  - **"Markets then stabilised quickly, and volatility receded."**
- Secondary coverage: the yen rose about 6% against the USD by 5 Aug, CHF about 3.5%, and the VIX briefly topped 60 in off-hours trading. — [globaltrading.net](https://globaltrading.net/?p=36090) (secondary; less authoritative)
- Yen and CHF appreciate on generic FTS days too (+0.44% and +0.33% on average). — [Baele et al. 2020](https://business.columbia.edu/sites/default/files-efs/citation_file_upload/Flights%20to%20Safety.pdf)

### Inferences
- The monitor's yen lens (~740–744: USD/JPY ≤ −1.5% over the window means "carry-unwind risk") uses a raw percentage. Two changes:
  - Use the grid's **z-score** instead.
  - Require a **conjunction** before using the carry-unwind label: USD/JPY z ≤ −2, VIX z ≥ +2 or VIX/VIX3M ≥ 1, and ideally Japanese equities (EWJ/Nikkei) down sharply. A yen-only rally is just an FTS marker.
- BNP's "losses reduce future crash risk" and BIS's "stabilised quickly" both suggest that **after** a carry-unwind spike, positioning is cleaner. That supports treating these as acute, mean-reverting stress for 1–20 day aftermath forecasting. This is an inference; no out-of-sample return test was found.

### Gaps
- No peer-reviewed study found quantifying S&P 500 1–20 day returns after yen-carry unwind events (Aug 2024, Aug 2007, Oct 2008, etc.).
- CFTC positioning (non-commercial JPY shorts) as a pre-condition for unwind severity was not researched here.

---

## Q6. How should a monitor combine cross-asset z-scores, which components carry the most information, and how can false alarms be avoided?

### Takeaway
The best-documented practice (CISS) is:
- transform each indicator **robustly** (empirical CDF, recursive/expanding);
- average within a few **market segments**;
- aggregate segments with a **time-varying correlation matrix**, so the composite rises sharply only when stress is **simultaneous across segments**;
- set regime thresholds **endogenously** (Markov-switching or threshold-VAR) rather than with ad hoc σ cut-offs.

Weights matter far less than correlation structure: real-impact and equal weights gave similar CISS paths.

For a 1–20 day equity monitor:
- equity measures (realized vol, drawdown/CMAX, breadth), the **VIX term structure**, and the **stock–bond correlation** are the timely components;
- credit adds information mainly through its **residual beyond equity**;
- slow official FSIs and the EBP are context only.

### Cited Findings
- **Correlation weighting** emphasizes simultaneous multi-segment stress. The aggregation is (w∘s_t)C_t(w∘s_t)'. Correlations are EWMA with λ=0.93 weekly, robust to 0.89/0.97. — [Holló, Kremer & Lo Duca 2012](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf)
- **Real-impact vs equal weights**: "not very large" differences. Equity carries 25% and intermediaries 30% in the real-impact scheme. — [same](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf)
- **Empirical-CDF / order-statistic transform plus recursive computation** are used for robustness and to avoid event reclassification. Recursive and full-sample versions differ by an average 0.015 on a 0–1 scale. — [same](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf)
- **Endogenous thresholds.** Threshold-VAR critical level 0.3233; below it, stress shocks have no significant real effect. Three Markov regimes with sharp classification (RCM 19.4). — [same](https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf)
- **Joint-tail flight-to-safety test**: κ=1.26, 360-day backward window excluding the last 5 days. FTS days are rare (<2%) and mostly 1-day. — [Baele et al. 2020](https://business.columbia.edu/sites/default/files-efs/citation_file_upload/Flights%20to%20Safety.pdf)
- **Equity leads credit** at daily/weekly frequency, except HY CDS index under adverse conditions. — [Hilscher, Pollet & Wilson 2015](https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/are-credit-default-swaps-a-sideshow-evidence-that-information-flows-from-equity-to-cds-markets/F7CCD2FFB20AAEACE1E093D12EEF7A31); [Procasky 2021](https://ideas.repec.org/a/eee/finmar/v54y2021ics1386418120300501.html)
- **Residual (non-default) credit component** carries the predictive content (EBP); largest false positive in 2002, an idiosyncratic credit shock. — [Favara et al. 2016](https://www.federalreserve.gov/econresdata/notes/feds-notes/2016/recession-risk-and-the-excess-bond-premium-20160408.html)
- **Adjusting financial indicators for the state of the economy** is the ANFCI idea: remove variation attributable to activity and inflation. — [Chicago Fed NFCI FAQ](https://www.chicagofed.org/-/media/publications/nfci/nfci-faqs-pdf.pdf)
- **Implied volatility predicts stock–bond decorrelation** over the next month. — [Connolly, Stivers & Sun 2005](https://ideas.repec.org/a/cup/jfinqa/v40y2005i01p161-194_00.html)

### Inferences — concrete, implementable upgrades for the Selloff Monitor
Ordered by expected value. All are deterministic, so they fit the project's "no LLM-invented facts" rule.

1. **Two layers: shock and state.**
   - Keep the current **shock** layer: window move ÷ σ of the prior 60 daily changes, as in `stress_grid`.
   - Add a **state** layer: the **empirical-CDF percentile of levels** over a long expanding window (≥10y where data allows). Candidates: HY OAS level, VIX level, 20-day realized SPY vol, SPY **CMAX** (drawdown from its 2-year max; the CISS equity indicator), MOVE level, and the stock–bond decorrelation measure.
   - Reason: a z against the prior 60 days is regime-relative. After a calm stretch, small σ inflates z and causes false alarms; inside a high-vol regime, big moves get muted z. CISS uses levels on an empirical CDF for exactly this robustness.
2. **CISS-style correlation-weighted composite (daily).**
   - Five daily subindices: **Equity** (SPY realized vol, CMAX, RSP–SPY breadth gap), **Credit** (HY OAS percentile, HY residual z from step 3), **Rates/funding** (MOVE, 10y realized vol), **Volatility** (VIX, VIX/VIX3M, VVIX), **FX/safe haven** (USD/JPY realized vol, DXY realized vol).
   - Each subindex is the average of its components' CDF values in (0,1]; demean by 0.5.
   - EWMA correlations with **daily λ ≈ 0.986**, about a 48-session half-life, the daily equivalent of CISS's weekly 0.93 (my conversion). Show the 0.97/0.995 sensitivity if desired.
   - Composite = (w∘s)C(w∘s)'. Use **equal weights**, since CISS found weights matter little.
   - Report both the composite and the "**correlation-share**": composite ÷ the same quantity with C = I. A high correlation-share means the stress is systemic.
   - BTC, oil and copper are better shown as **context rows outside the composite**: their idiosyncratic shocks would otherwise add noise. CISS has no crypto or commodity segment. This is an inference.
3. **Credit as excess-over-equity, lag-aware (replaces `oas_chg >= 15 bp`).**
   - Regress ΔOAS (or the HYG excess return over a duration-matched Treasury ETF) on SPY returns at lags 0..2 over a trailing ~250 days.
   - Credit "confirming" = cumulative window residual ≥ +1σ, or raw ΔOAS in **log/relative terms** above about the 90th percentile.
   - When the FRED print is older than the last session, mark credit "**pending**", not "calm".
   - Evidence: equity leads credit; the residual (EBP-like) part carries the information; sensitivity rises with lower quality.
4. **Use HYG ahead of OAS for timeliness. Strip duration from HYG.**
   - HYG minus β×IEF, so a 2022-style rates selloff isn't read as credit stress.
   - Optional: an HYG **discount to NAV** below about −1% as a cash-credit illiquidity flag (March 2020 discounts were 6–10%). The −1% threshold is untested and my suggestion.
5. **Flight-to-safety detector (replaces TLT ±0.3%).**
   - Baele threshold model: SPY<0, TLT>0, joint-normal tail probability below the threshold at κ≈1.26; σ and ρ from roughly 250–360 sessions ending 5 sessions before the event.
   - Supporting tells: yen and CHF up, gold up after equity-beta adjustment.
   - Label separately: "**Flight to safety** (acute; historically 1–3 day spells)" vs "**Stocks and bonds falling together** (positive-correlation / inflation regime; bonds don't hedge)". Show the 1-year rolling SPY–TLT correlation sign as a regime chip.
6. **Carry-unwind label as a conjunction**: USD/JPY z ≤ −2, VIX z ≥ +2 or VIX/VIX3M ≥ 1, and Japan equities sharply down. A yen rally alone is only an FTS marker.
7. **False-alarm controls.**
   1. **Breadth across segments, not depth in one.** Use the correlation-weighted composite or require ≥3 of 5 segments above their 80th percentile. This screens out single-market shocks like the 2002 credit false positive or crypto-only or oil-only moves.
   2. **Persistence.** Require the condition on ≥2 of the last 3 sessions for the "Broad risk-off" label, or use the EWMA composite. Report 1-day FTS spikes as "acute" rather than "building", since 62% of FTS spells last one day.
   3. **Endogenous thresholds.** Calibrate cut-offs on history to a target incidence (as Baele calibrated κ to 4.66%), or fit a 2–3 state Markov switch on the composite, instead of fixed +15 bp / −1.5% / ±0.3%.
   4. **Recursive and real-time.** Compute percentiles and correlations from expanding or trailing windows only, as CISS does recursively and Baele's kernel excludes the last 5 days, so backtests aren't look-ahead-biased. The monitor already excludes the window from σ, which matches.
   5. **Validate before trusting.** For any new label, backtest SPY forward returns and first-passage odds over 1/5/10/20 days, conditional on label versus unconditional, on a held-out period (e.g. pre-2015 fit, post-2015 test). Report Wilson intervals, as `find_analogs` already does.
8. **Keep slow indices as context only**: NFCI/ANFCI (weekly), STLFSI4 (weekly), OFR FSI (daily, 2-day lag), EBP (monthly). Show them as "financial-conditions backdrop" chips. Their evidence is for 2–4 quarter macro and downside-GDP horizons, not 1–20 day equity returns.

### Gaps
- **No peer-reviewed out-of-sample evidence** was found that any composite stress index (equal- or correlation-weighted) improves **1–20 day S&P 500 return or drawdown forecasts**. Upgrades 1–7 rest on construction best practice and lead-lag evidence, and must be validated in-house (step 7.5).
- No head-to-head study was found comparing equal-weighted vs correlation-weighted composites for **short-horizon equity prediction**. CISS compares weightings only for stress identification and macro impact.
- Which individual components carry the most short-horizon information (e.g. MOVE vs VVIX vs VIX/VIX3M vs HY residual) was not established by any source found. Ranking must come from the monitor's own backtest.
