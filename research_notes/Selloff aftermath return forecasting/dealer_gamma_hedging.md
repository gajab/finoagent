# Dealer Gamma Exposure, Hedging Flows and US Equity Index Volatility, Autocorrelation and Pinning — Evidence for a SPY GEX "Selloff Monitor"

Scope note: the primary sources below were read in full text where marked "(full text read)". Abstract-only or press-coverage sources are marked as such. Industry and vendor sources are flagged **[INDUSTRY]**.

## Q1. What do peer-reviewed and serious working papers find?

### Takeaway
The academic evidence agrees on the mechanism: when hedgers are net short gamma, their delta-hedging trades go the same way as the market and amplify moves (momentum); when they are net long gamma, hedging leans against moves (dampening and reversal). The cleanest S&P 500 studies, which use signed Cboe trade data with real market-maker positions, find that SPX market makers are net **long** gamma most of the time, so their hedging usually **dampens** index volatility. Episodes of negative gamma do happen and raise volatility, but by a modest amount.

### Cited Findings
**Baltussen, Da, Lammers & Martens (2021, JFE 142:377–403), "Hedging demand and market intraday momentum"** (full text read)
- Sample: intraday data on more than 60 futures (equity, bond, commodity, FX), Dec 1974–May 2020. The return in the last 30 minutes (rLH) is positively predicted by the rest-of-day return (rROD). For pooled equity-index futures the slope on rROD is 0.0418 (reported ×100 as 4.18, t = 7.29), in-sample R² is 2.45% and out-of-sample R² is 2.88%. The effect reverts over the next days. Timing strategies have Sharpe ratios of 0.87–1.73 — [Baltussen et al., author PDF](https://www3.nd.edu/~zda/intramom.pdf); [EUR repository](https://pure.eur.nl/en/publications/hedging-demand-and-market-intraday-momentum/)
- The gamma proxy (NGE) uses **the same convention as the Selloff Monitor**. It assumes (i) delta-hedgers take the other side of every option, (ii) investors sell calls and market makers buy them, (iii) investors buy puts and market makers sell them, and (iv) market makers hedge exactly to delta. For each strike it computes Γ·OI·(+100 for calls, −100 for puts)·P_t, sums over all SPX strikes and expiries (OptionMetrics 1996–2017, SqueezeMetrics data to May 2020), then **divides by S&P 500 market value**. The authors report that SqueezeMetrics' numbers matched their own OptionMetrics computation 2002–2017 almost exactly — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Conditional result (S&P 500 futures, Jan 1996–May 2020, Table 7). On days when the prior day's NGE < 0, the slope on rROD is **0.0663 (t = 4.78), R² 3.58%**. When NGE ≥ 0 it is **0.0082 (t = 1.03), R² 0.05%**, i.e., no significant intraday momentum. Interaction term NGE×rROD = −1.23 (t = −3.42). Results survive a difference-in-differences test against time trends, and also hold when only puts are counted. Over the sample, 2,930 days had negative NGE and 3,158 positive — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Leveraged-ETF rebalancing demand is a second, independent driver of the same pattern — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)

**Ni, Pearson, Poteshman & White (2021, RFS 34(4):1952–1986), "Does Option Trading Have a Pervasive Impact on Underlying Stock Prices?"** (full text read for the 2006 working draft by Pearson, Poteshman & White; the published version was not accessible)
- Data: CBOE single-stock options, 1990–2001. Market-maker net positions are recovered from public-customer and firm-proprietary open interest using the OCC origin codes, since all positions net to zero. This gives **actual signed positions, not an assumed sign** — [2006 draft](https://ou.edu/dam/price/Finance/CFS/paper/pdf/pearsonPoteshmanWhite.pdf); [RFS listing](https://ideas.repec.org/a/oup/rfinst/v34y2021i4p1952-1986..html)
- Effect size: average daily absolute return is 310 bp. A one-standard-deviation shock to net market-maker gamma moves next-day absolute return by **37 bp (≈12%)**. The lowest and highest market-maker-gamma deciles differ by about **100 bp** in average daily absolute return. The result is robust to controls for volatility persistence and informed volatility trading, to large and small stocks, to both sample halves, to adding firm proprietary traders, and to **excluding expiration week** — [2006 draft](https://ou.edu/dam/price/Finance/CFS/paper/pdf/pearsonPoteshmanWhite.pdf)
- On average, market makers' normalized net gamma in single stocks is **positive** (mean 3.106, SD 6.772), i.e., they are net long gamma in stocks — [2006 draft](https://ou.edu/dam/price/Finance/CFS/paper/pdf/pearsonPoteshmanWhite.pdf). Golez & Jackwerth, citing Garleanu et al., also state that market makers are typically short SPX options but long single-stock options — [Golez & Jackwerth 2012](https://d-nb.info/1112655492/34)

**Adams, Dim, Eraker, Fontaine, Ornthanalai & Vilkov (July 2026 draft), "Do S&P 500 Options Increase Market Volatility? Evidence from 0DTEs"** (full text read; merges SSRN 4692190 and 4881008)
- Data: Cboe Open-Close volume by trading capacity (market maker, customer, professional customer, firm proprietary, broker-dealer), intraday at 10-minute frequency for 2018–2024 and daily for 2012–2017. Market-maker positions are signed volumes summed from each series' first trade. Gamma comes from Cboe 1-minute quotes. Main volatility measure is E-mini futures, with SPY and cash-index robustness checks — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf); [SSRN 5641974](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5641974)
- On days with 0DTE expirations, 10-minute S&P 500 realized volatility is **61 bp (annualized) lower, about 7% of the 9.10% mean**. Identification comes from exogenous Tuesday/Thursday expirations before May 2022. In a two-stage estimate, a 1 bp increase in market makers' counter-trend hedging need lowers realized volatility by about **4 bp** — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- A one-standard-deviation increase in 10-minute 0DTE net gamma lowers next-interval realized volatility by about **23 bp (annualized)**, and the effect lasts about an hour. Higher market-maker gamma also predicts stronger order-flow reversal in E-minis (for about 30 minutes) and **lower or negative hour-to-hour momentum returns** — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- **SPX market-maker net gamma is positive in 88% of 10-minute intervals, and its monthly average was positive in every month** (0DTE plus non-0DTE combined). Hedging need, expressed as % of S&P 500 shares to trade per 1% move, averaged 0.88% before May 2022 and 1.42% after — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)

**Amaya, Garcia-Ares, Pearson & Vasquez (Jan 2025), "0DTE Index Options and Market Volatility: How Large Is Their Impact?"** (full text read; Cboe-hosted)
- Data: proprietary Cboe SPX/SPXW trade records with buyer and seller capacities, used to build 1-minute market-maker positions and gamma, July 2020–June 2023. They estimate GARCH and linear models of 1-minute variance on lagged market-maker gamma — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- Market-maker gamma is usually positive (mean of daily means 341×10⁹ in their scaling, SD 320). It turns **negative at some point on at least 25% of days**, and on at least 1% of days it is negative all day. After the May 2022 addition of Tuesday/Thursday expiries, it goes negative intraday on **at least half** of days — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- Variance is negatively related to lagged market-maker gamma. Counterfactual effect on annualized daily volatility: median −0.08 pp, mean −0.19 pp (dampening). Hedging **raises** volatility on about 10% of days and about 10% of 30-minute windows. The **largest** increase is +3.3 pp (daily) or +6.4 pp (30-minute; 99th percentile only +2.0 pp). For scale, the SD of daily changes in realized volatility from all causes is 4.5 pp, so the authors judge even the worst gamma effect to be "not large" — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)

**Barbon & Buraschi (2020), "Gamma Fragility"** (working paper, abstract only)
- From a large panel of index and equity options they build an aggregate dealer gamma-imbalance proxy. Negative imbalance combined with illiquidity explains intraday **momentum**; positive imbalance combined with illiquidity explains intraday **reversal**. Effects are strongest in the least-liquid names, and imbalance is linked to the frequency and size of flash crashes — [Univ. St. Gallen repository](https://www.alexandria.unisg.ch/261487)

**Barbon, Beckmeyer, Buraschi & Moerke (2022), "Liquidity Provision to Leveraged ETFs and Equity Options Rebalancing Flows: Evidence from End-of-Day Stock Prices"** (SFI WP 22-40, abstract only)
- Both LETF rebalancing and option-hedging flows produce end-of-day momentum or mean reversion. The gamma effect persists across the sample, while the LETF effect fades. LETF flows draw more liquidity provision, and their effects are shorter-lived. **Intermediaries choose when to hedge, which makes their flows harder to predict** — [IDEAS/RePEc](https://ideas.repec.org/p/chf/rpseri/rp2240.html). A secondary summary says gamma imbalance is scaled by average dollar volume and that the effects revert at the next open — [Harbourfront Quant summary](https://harbourfrontquant.substack.com/p/how-options-imbalances-affect-price)

**Other**
- Brogaard, Han & Won (working paper, press coverage only): a one-standard-deviation rise in 0DTE volume is associated with about **14% higher** S&P 500 daily volatility — [BNN Bloomberg](https://www.bnnbloomberg.ca/zero-day-options-are-reordering-the-way-the-stock-market-behaves-1.1921930). Adams et al. challenge the identification, which depends on staggered SPXW introduction dates being unrelated to volatility — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Dim, Eraker & Vilkov (SSRN 4692190): high 0DTE open-interest gamma does not propagate past volatility, and 0DTE volume shocks do not amplify recent index returns — [SSRN](https://ssrn.com/abstract=4692190)
- Anderegg, Sornette & Ulmann (SNB WP 2019-03; J. Int'l Money & Finance 2022), FX not equities: dealer gamma rebuilt from public DTCC repository data is negative. About −$1,000bn of dealer gamma raises volatility by 0.7% (EURUSD) and 0.9% (USDJPY) — [SNB WP](https://www.snb.ch/en/publications/research/working-papers/2019/working_paper_2019_03); [JIMF listing](https://ideas.repec.org/a/eee/jimfin/v124y2022ics0261560622000304.html)
- Related work cited in Adams et al.: O'Donovan, Yu & Zhang (2024), where long-gamma market makers stabilize single stocks; Lipson, Tomio & Zhang (2023), where retail long-dated option demand leaves market makers short and raises stock volatility; Hu, Kirilova, Muravyev & Ryu (2024), where market makers **seldom delta-hedge long-dated** KOSPI 200 options — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Garleanu, Pedersen & Poteshman (RFS 2009): end users are net long S&P 500 index options, especially OTM puts, so dealers are net short index options — [NBER w11843](https://www.nber.org/papers/w11843)

### Inferences
- Baltussen et al. is the one peer-reviewed study that validates a **public-OI, assumed-sign GEX** like the monitor's. Its validated use is narrow: it predicts the **last-30-minute continuation** of the day's move, and that continuation **reverts over the following days**. It does not validate a gamma-flip level, walls, or direction.
- Signed-data studies (Adams et al., Amaya et al.) agree on direction (positive market-maker gamma dampens volatility) but find SPX market makers net long far more often than the convention-based measure implies. Baltussen's NGE is negative on about 48% of days; Adams et al.'s signed measure is positive in 88% of intervals, though over a different sample period.

### Gaps
- Coefficients for Barbon & Buraschi "Gamma Fragility" and for Barbon et al. (2022) could not be obtained (SSRN returned 403; only abstracts were read).
- The published RFS 2021 Ni et al. numbers could not be checked against the 2006 draft quoted above.
- Brogaard, Han & Won: specification and standard errors were not checked; only press coverage was read.

## Q2. Effect sizes: does negative dealer gamma raise next-day volatility or intraday momentum, and does positive gamma dampen volatility or produce mean reversion?

### Takeaway
Intraday momentum is clearly conditional on gamma sign. Under negative convention-GEX, the slope of the last 30 minutes on the rest of the day is about 0.066; under positive GEX it is about 0.008 and insignificant. Volatility effects at the **index** level are real but small: typically a few tenths of a vol point of dampening, and at worst about +3 vol points on a daily basis. Single-stock effects are larger, at about 12% of daily absolute return per standard deviation. Positive gamma clearly dampens volatility and strengthens short-horizon reversal.

### Cited Findings
- Last-30-minute momentum, S&P 500 futures 1996–2020: slope 0.0663 (t 4.78) when the prior day's NGE < 0, versus 0.0082 (t 1.03) when NGE ≥ 0 — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Intraday momentum reverts over the next days — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf). Barbon et al. find gamma- and LETF-driven end-of-day effects revert at the next open (secondary summary) — [Harbourfront Quant](https://harbourfrontquant.substack.com/p/how-options-imbalances-affect-price)
- Index volatility, signed market-maker gamma: median −0.08 pp, mean −0.19 pp in annualized daily volatility; maximum +3.3 pp daily and +6.4 pp in a 30-minute window; volatility is raised on about 10% of days — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- Index volatility at 10-minute horizon: one-standard-deviation higher 0DTE market-maker gamma gives −23 bp annualized volatility over the next 10 minutes, with the effect lasting about an hour. 0DTE expiration days have 61 bp (7%) lower volatility. Each 1 bp of hedging need maps to about −4 bp of volatility — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Positive gamma and reversal: high market-maker hedging needs predict **negative** hour-to-hour momentum (reversal) and stronger E-mini order-flow reversal — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf). Positive imbalance combined with illiquidity predicts intraday reversal — [Barbon & Buraschi](https://www.alexandria.unisg.ch/261487)
- Single stocks, next-day absolute return: +37 bp per standard deviation of lower market-maker gamma (12% of the 310 bp mean); 100 bp spread between the lowest and highest deciles — [Ni et al. draft](https://ou.edu/dam/price/Finance/CFS/paper/pdf/pearsonPoteshmanWhite.pdf)
- **[INDUSTRY]** SqueezeMetrics: the 1-day SD of SPX returns is 0.55% after the highest GEX quartile versus 0.85% after the second-highest. Their scatter shows volatility rising "exponentially" as GEX falls below zero. No sample period, test statistics or out-of-sample check are given; the document is a marketing white paper (2016, revised 2017) — [SqueezeMetrics GEX white paper](https://slopeofhope.com/wp-content/uploads/2020/11/GEX-white_paper.pdf)
- **[INDUSTRY]** SpotGamma claims tighter ranges, lower realized volatility and mean reversion above the zero-gamma line, and destabilization below it, with no published test — [SpotGamma](https://spotgamma.com/gamma-exposure-explained/)
- **[INDUSTRY]** A Cboe analysis estimated 0DTE market-maker hedging flows at 0.04%–0.17% of S&P futures liquidity (secondary summary; Cboe is an interested party) — [Cboe Volatility Insights](https://www.cboe.com/insights/posts/volatility-insights-evaluating-the-market-impact-of-spx-0-dte-options)
- Low-weight: a 2026 Princeton senior thesis found the hedging-feedback channel adds little to intraday variance forecasts (search summary only) — [Princeton thesis](https://theses-dissertations.princeton.edu/entities/publication/61115cd1-cb09-4a8b-9230-ff488fc2a386)

### Inferences
- Worked example from Baltussen's slopes: under negative NGE, a −2.0% move by 3:30pm predicts about **−13 bp** more into the close (2.0% × 0.0663). Under positive NGE the prediction is about −1.6 bp and not statistically different from zero. That is economically meaningful for close-timing, but small next to daily volatility.
- For a selloff-aftermath monitor this has three uses:
  - Negative GEX during a selloff supports "the trend is more likely to extend into the close".
  - Because the effect reverts over the following days, negative GEX on a selloff day is weak evidence **for** partial next-days mean reversion, not continuation.
  - A next-day **volatility** claim should stay modest, roughly on the order of 0–3 annualized vol points at index level per the signed-data studies.
- The roughly 0.30-point gap in daily SD between GEX quartiles reported by SqueezeMetrics is larger than the signed-data counterfactuals. Part of it is likely confounding: positive convention-GEX coincides with rising, low-volatility markets because call gamma grows and put gamma shrinks as markets rally (Baltussen et al. note this in their footnote 8). So GEX partly re-labels the existing volatility regime rather than causing it.

### Gaps
- No peer-reviewed estimate was found of the **next-day realized-volatility** effect of convention-based SPY or SPX GEX specifically, as opposed to intraday or signed-data effects.
- No study was found that measures the out-of-sample value of GEX **beyond** lagged realized volatility and VIX for next-day SPX volatility, using public OI data.

## Q3. Measurement problems: sign convention, OI vs volume, 0DTE, SPX vs SPY, and reliability of OI-based GEX

### Takeaway
The "dealers long calls, short puts" convention is roughly right for **longer-dated SPX calls**: customers overwrite, so dealers end up long calls. It is demonstrably wrong for the gamma that matters most, which is **short-dated near-the-money options**: customers are net short ATM 0DTE straddles and iron condors, so dealers are long gamma on **both** puts and calls. That flips the near-spot sign of the convention's put leg. It is also unverified for SPY, which has a heavy retail presence. An OI-based GEX with no trade direction is a **model**, not a position report. Its sign, and especially the location of its zero-crossing, should carry stated uncertainty.

### Cited Findings
- Clarifying the premise: customer **call overwriting** (selling calls) is exactly why the convention assumes dealers are **long** calls. SqueezeMetrics justifies the call leg by overwriting and collaring, and the put leg by protective-put buying — [SqueezeMetrics](https://slopeofhope.com/wp-content/uploads/2020/11/GEX-white_paper.pdf). Baltussen et al. cite Goyenko & Zhang (2019) for positive end-user demand for SPX puts and negative end-user demand for SPX calls, and Cici & Palacios (2015) for mutual funds mostly writing calls and buying puts — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Where the convention breaks: in 0DTE SPX, customers begin the day **short ATM and slightly-OTM** options and long far-OTM options (an iron-butterfly/condor shape). Their 0DTE net gamma is "very negative" all day, so market makers are long gamma. The customers' aggregate short ATM straddle explains market makers' large positive 0DTE gamma — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Signed market-maker gamma in SPX was positive in 88% of 10-minute intervals and its monthly average was always positive (2018–2024) — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf). Convention-based NGE was negative on 2,930 of 6,088 days (1996–2020) — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf). Signed market-maker gamma does go negative intraday on at least 25% of days (at least half after May 2022) — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf)
- Baltussen et al. themselves say their convention misses OTC positions (institutional long puts, short calls) and exact end-user positions. They note NGE is positive on most days, possibly because market drift raises call gamma and lowers put gamma, and possibly because of the data used — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Market makers hedge with "hedging bands" rather than exactly to delta; SqueezeMetrics acknowledges this and assumes exact delta hedging anyway — [SqueezeMetrics](https://slopeofhope.com/wp-content/uploads/2020/11/GEX-white_paper.pdf). Intermediaries choose when to hedge, which makes flows less predictable — [Barbon et al. 2022](https://ideas.repec.org/p/chf/rpseri/rp2240.html). Market makers seldom delta-hedge long-dated KOSPI options (Hu et al. 2024, cited in) — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- 0DTE and OI: most of the hedging need in any interval comes from positions that already existed. Positions opened earlier in longer-dated options that **become** 0DTE add about 25 bp of hedging need on expiration day; new same-day 0DTE trades add about 3 bp. Market makers net much of the intraday 0DTE flow — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- SPX vs futures vs index basket: SPX options are hedged with **futures**, not the index basket. The S&P 500 index shows no pinning from SPX expirations, but futures do show "cross anti-pinning" — [Golez & Jackwerth 2012](https://d-nb.info/1112655492/34). E-mini futures are the main hedging instrument; Adams et al. use ES and check SPY and cash-index results as robustness — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Retail: retail accounts for more than 60% of US options volume, favors cheap weekly options, and loses money on average (no net-direction figure for SPY is given) — [Bryzgalova, Pavlova & Sikorskaya, JF 2023](https://ideas.repec.org/p/cpr/ceprdp/17688.html)
- **[INDUSTRY/commentary]** OI cannot show which side a dealer is on. Open-Close data is aggregate capacity-classified flow, not a match-level reconstruction. Dealers can offset gamma in SPY, CME futures options and OTC, so a published GEX is a model, not a position report — [Substack: "Your GEX chart is a model"](https://dn12448583.substack.com/p/your-gex-chart-is-a-model-not-a-position); [Finelo](https://finelo.com/blog/gamma-exposure-gex)

### Inferences
- **The largest single sign error in the convention is the near-ATM short-dated put leg.** The convention books dealers as short puts, so near spot it puts large negative gamma on ATM puts. Signed data shows dealers are long ATM 0DTE puts and calls. The convention therefore biases GEX **downward near spot** and pushes the **gamma-flip upward** toward or above spot. That produces "dealers short gamma" labels when signed data would usually say long.
- OI captures the **stock** of positions, which is the component that dominates hedging needs (stale positions count for 25 bp versus 3 bp from new trades). So the problem with OI-based GEX is mainly **sign**, not missing 0DTE flow. Yesterday's OI does include the contracts that become 0DTE today.
- SPY options are American-style and physically settled (a standard contract feature, not sourced here), with heavy retail and weekly use. Retail call **buying** would make dealers short calls, the opposite of the convention. No public signed SPY dealer-position data was found, so the SPY sign is less certain than SPX.
- SPY GEX alone covers only part of S&P 500 option gamma (SPX/SPXW and ES options are large). A SPY-only number can mis-state both the level and sign of total dealer gamma.
- Reliability, overall: as a **regime proxy** (higher vs lower), convention-GEX has one peer-reviewed validation (Baltussen). As a **signed dealer position**, and especially as a precise **flip price**, it is unreliable.

### Gaps
- No public study was found that compares an OI-based, convention-signed SPX GEX directly with signed Cboe market-maker gamma (correlation, sign-agreement rate, or flip-level error). This is the key missing validation.
- No public data was found on dealer-vs-customer positioning in **SPY** options, or on SPY's share of total S&P option gamma.

## Q4. Option expiration effects: pinning, post-opex "gamma unclench", VIX expiration and vanna/charm — evidence vs folklore

### Takeaway
Pinning is **real and peer-reviewed** for physically settled single-stock options (Ni et al. 2005) and for **S&P 500 futures** on serial futures-option expirations (Golez & Jackwerth 2012). It is **not found in the S&P 500 index basket** from SPX expirations; futures show anti-pinning on SPX expirations instead. Expiration-week return effects exist for heavily optioned single stocks. The post-opex "unclench" and the VIX-expiration vanna/charm stories have **no peer-reviewed support** found; they are practitioner narratives.

### Cited Findings
- Ni, Pearson & Poteshman (2005, JFE 78:49–87): optionable stocks' closes cluster at strikes on expiration dates. Returns are altered by at least 16.5 bp on average per expiration, about $9bn of market-cap shift. The paper attributes this to market-maker hedge rebalancing plus manipulation by proprietary traders (abstract-level figures) — [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=519044)
- Golez & Jackwerth (2012, JFE 106:566–585): S&P 500 futures are pulled to the ATM strike on serial (non-quarterly) futures-option expirations, Nov 1992–Nov 2009. The notional shift is at least $115m per expiration, and at least $240m in Oct 1998–Nov 2009. Pinning is stronger **from below**, because market makers hold more short ATM puts than calls. There is **no pinning in the index basket** from SPX expirations, and futures show **cross anti-pinning** before SPX expirations, consistent with Avellaneda & Lipkin. Drivers are time decay of delta hedges and resale or early exercise of ITM options by individuals — [Golez & Jackwerth](https://d-nb.info/1112655492/34) (full text read); [RePEc](https://ideas.repec.org/a/eee/jfinec/v106y2012i3p566-585.html)
- Ni et al. found their volatility result also holds when **expiration week is excluded**, so hedging effects are not only an expiry phenomenon — [Ni et al. draft](https://ou.edu/dam/price/Finance/CFS/paper/pdf/pearsonPoteshmanWhite.pdf)
- Stivers & Sun ("Returns and Option Activity over the Option-Expiration Week for S&P 100 Stocks", SSRN 1571786): high-option-activity S&P 100 stocks earn weekly return spreads of about 68–71 bp in expiration week. This is attributed to market makers unwinding short-stock hedges on expiring net-long call positions; the following week underperforms modestly (secondary summary) — [Quantpedia](https://quantpedia.com/strategies/option-expiration-week-effect)
- 0DTE expiration days have **lower** intraday index volatility (−61 bp annualized), driven by positions maturing into 0DTE — [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- **[INDUSTRY]** Post-opex "unclench", the idea that pinning fades after expiry and realized volatility rises, appears only in practitioner commentary — [Investing.com analysis](https://www.investing.com/analysis/options-expiration-could-unleash-sp-500-volatility-200685896); [SpotGamma](https://spotgamma.com/vix-20-negative-gamma-october-opex/)
- **[INDUSTRY]** Vanna and charm (delta sensitivity to IV and to time) flows are said to support prices into expiration and to outweigh gamma. These are vendor claims with no systematic test — [FlashAlpha](https://flashalpha.com/articles/why-gex-isnt-enough-vanna-charm-exposure-predict-market-moves)

### Inferences
- For **SPY** (physically settled, American-style), the single-stock pinning literature is the closer analogue, so modest strike clustering at monthly opex is plausible. Expect an effect on the order of the single-stock ~16.5 bp average, and only for the **expiring** expiry's large-OI strikes. Treat it as a weak, expiry-day-only tendency, not support or resistance.
- For the **index/SPX**, a "wall" or "pin" label has no peer-reviewed support. The evidence is anti-pinning in futures around SPX expiries.
- "Gamma roll-off": a large share of GEX expiring at the next expiry is a mechanical fact a monitor can report. Any claim that volatility rises afterward should be labeled hypothesis.

### Gaps
- No peer-reviewed study was found testing post-monthly-opex changes in S&P 500 realized volatility (the "unclench").
- No peer-reviewed study was found on VIX-expiration or vanna/charm-driven index flows.
- The exact strike-clustering frequencies in Ni et al. (2005) were not retrieved; only abstract-level magnitudes were read.
- No academic evidence was found on SPY-specific pinning.

## Q5. What can the monitor responsibly say, and concrete implementable upgrades

### Takeaway
Present dealer gamma as a **volatility and autocorrelation regime indicator with explicit sign uncertainty**, never as a direction call. The defensible statement is conditional: *"If dealers are positioned as the standard convention assumes, hedging would amplify moves. Historically, negative convention-GEX days show stronger close-into-trend continuation that tends to revert over subsequent days. Signed-data studies find SPX dealers are usually net long gamma, so this label is uncertain."*

### Cited Findings
- The only peer-reviewed validation of convention-based OI GEX is conditional intraday (last-30-minute) momentum that reverts later — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Signed-data studies find dealer gamma usually **positive** and dampening; negative episodes raise volatility modestly, at most +3.3 pp daily — [Amaya et al.](https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf); [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf)
- Normalization practice: Baltussen et al. divide NGE by S&P 500 market value; Adams et al. express hedging need as % of S&P 500 shares to trade per 1% move; Barbon et al. scale imbalance by average dollar volume — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf); [Adams et al.](https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf); [Harbourfront summary of Barbon et al.](https://harbourfrontquant.substack.com/p/how-options-imbalances-affect-price)
- Baltussen's result holds when only puts are counted, so a puts-only variant is a legitimate robustness check — [Baltussen et al.](https://www3.nd.edu/~zda/intramom.pdf)
- Practitioner critics recommend treating GEX levels as context zones to test against price behavior, not precise lines — [Finelo](https://finelo.com/blog/gamma-exposure-gex)

### Inferences — implementable upgrades for a deterministic yfinance-based Selloff Monitor
1. **Re-word the label.**
   - Replace "dealers short gamma → moves amplify" with "Model GEX negative (convention: dealers long calls / short puts). Historically associated with stronger end-of-day trend continuation, which tends to revert over the next days. Dealer sign is assumed, not observed."
   - Always show a **"sign confidence" badge** (Low/Medium/High) driven by item 3.
2. **Normalize to hedge-flow units.**
   - Report $-gamma per 1% move: Σ Γ·OI·100·S²·0.01 × (+1 for calls, −1 for puts).
   - Also report it as a **% of SPY (or SPY+ES) average daily dollar volume**, and optionally as % of S&P 500 market cap, following Baltussen and Adams.
   - Use percentile ranks against the monitor's own stored history instead of raw dollars, because raw GEX grows with market size and option volume.
3. **Sign-robustness ensemble (deterministic).** Compute GEX under 3–4 conventions and publish the regime only when they agree:
   - (a) Classic: calls +, puts −.
   - (b) Puts-only, following Baltussen's robustness check.
   - (c) "Short-dated ATM customer-short" variant: for expiries ≤ 2–5 DTE and |moneyness| ≤ ~1–2%, set dealer sign = + for **both** calls and puts, following Adams et al.'s customer short-straddle finding.
   - (d) Down-weight or exclude long-dated options (e.g., > 90–180 DTE), since market makers seldom hedge long-dated options and gamma there is small.
   - If (a) is negative but (c) is positive, label "Ambiguous: sign depends on short-dated ATM positioning".
4. **Flip as a zone, not a line.**
   - Recompute total GEX on a spot grid (±5–8%, sticky-strike IVs) and report the zero-crossing for each convention variant.
   - Show the **range** of flip levels and the distance from spot in σ units (spot ATM IV × √(1/252)).
   - If the flip is within about 0.5σ of spot, or differs across variants by more than 1σ, say "near the flip; regime unstable" instead of a hard label.
5. **Expiry decomposition.**
   - Show the share of |GEX| from 0–1 DTE, 2–7 DTE, the next monthly, and later expiries.
   - Show the **"gamma roll-off"** at the next expiry, i.e., the % of GEX that disappears. Label any post-expiry volatility implication as hypothesis.
   - Note that yesterday's OI already contains the stale positions that dominate 0DTE hedging needs, so missing intraday 0DTE flow is a secondary problem; the sign is the main one.
6. **Walls: report, don't predict.**
   - Report the top call-gamma and put-gamma strikes as "largest OI-gamma concentrations".
   - Show a "possible pin" note only (i) on the expiry date, (ii) for the **expiring** series, and (iii) when spot is within about 0.25–0.5% of a high-OI strike. Cite single-stock and futures pinning evidence, and note that no index-basket pinning was found for SPX.
7. **Add SPX.**
   - yfinance exposes ^SPX chains, and the codebase already aliases SPXW→^SPX. Compute SPX GEX in the same $-per-1% units and show SPY, SPX and their combined total.
   - If SPY and SPX disagree on sign, downgrade confidence.
8. **Data hygiene for yfinance.**
   - Use only strikes with OI > 0 and a valid two-sided quote.
   - Recompute IV from the bid/ask mid with Black-Scholes, using dividend yield and rate, rather than trusting yfinance `impliedVolatility`, which is often garbage on weekends and illiquid strikes.
   - Cap IV and DTE floors (e.g., T ≥ 1 hour) to prevent gamma blow-ups at expiry.
   - Timestamp the OI as **previous-session** settlement. OCC OI updates overnight, so intraday GEX uses stale OI.
9. **Self-calibration (deterministic, no LLM).**
   - Snapshot GEX daily, since yfinance has no historical chains. After ≥ 250 observations, show the monitor's own conditional statistics:
     - next-day |return| and realized volatility by GEX quintile, controlling for prior realized volatility and VIX;
     - Baltussen-style slope of the last-30-minute SPY return on the rest-of-day return, split by GEX sign;
     - 1–5-day forward returns after selloff days, split by GEX sign.
   - Until then, show the literature effect sizes:
     - slope 0.066 vs 0.008 (Baltussen);
     - index volatility effect typically −0.1 to −0.2 vol points, worst case about +3 vol points (Amaya);
     - single stocks about 12% of daily |return| per standard deviation (Ni et al.).
10. **Selloff-specific framing.** On a selloff day with negative and robust GEX:
    - "Into-close extension more likely; historically this intraday continuation **reverts over subsequent days**."
    - Do **not** forecast direction for the next day from GEX.
    - On positive GEX: "Hedging likely dampens; intraday reversals more likely."
11. **Evidence-quality tags in the UI.**
    - Peer-reviewed: intraday momentum (Baltussen); single-stock volatility (Ni et al.); pinning in stocks and futures (Ni et al. 2005; Golez & Jackwerth).
    - Working papers using signed data: Adams et al.; Amaya et al.
    - Industry or folklore: flip-level support/resistance, walls as barriers, the post-opex unclench, vanna/charm and VIX-expiry rallies.

### Gaps
- No public benchmark exists to calibrate how often a yfinance-OI SPY GEX sign matches true dealer gamma. Building one would require Cboe Open-Close data, which is a paid product.
- The best DTE and moneyness cut-offs for the "customer-short ATM" variant in item 3(c) are not published. They are inferred from Adams et al.'s descriptive figures and should be treated as tunable.
- Results from Zarattini, Aziz & Barbon (2024) were not available. That paper tests whether estimated dealer gamma predicts a SPY intraday momentum strategy (2007–2024, Sharpe 1.33); only the abstract was read — [IDEAS/RePEc](https://ideas.repec.org/p/chf/rpseri/rp2497.html)
