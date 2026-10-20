"""What the evidence says — the Selloff Monitor's single source of research grades.

Every grade, finding and source here is taken from the literature review shipped with the app
(``app/research/selloff_evidence_report.md``, "Selloffs Forecast Volatility Far Better Than Direction").
The evidence panel AND the grade chip on every tell read from this registry, so the two can never disagree.

Grades (the report's definitions):
  strong      — peer-reviewed, long samples, replicated or validated out of sample
  moderate    — peer-reviewed but a single study, no clean out-of-sample test, or a mismatched horizon
  weak        — practitioner backtests, small or in-sample evidence, or construction practice without
                predictive validation
  folklore    — no independent test found (or the evidence contradicts the claim)
  descriptive — a price level or description, not a forecast (tells only; not listed in the panel)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

GRADES = ("strong", "moderate", "weak", "folklore", "descriptive")
GRADE_DEFINITIONS = {
    "strong": "Peer-reviewed, long samples, replicated or validated out of sample.",
    "moderate": "Peer-reviewed but a single study, no clean out-of-sample test, or a horizon that doesn't match 1–20 sessions.",
    "weak": "Practitioner backtests, small or in-sample evidence, or construction practice with no predictive validation.",
    "folklore": "No independent test was found — or the evidence contradicts the claim.",
}

REPORT_TITLE = "Selloffs Forecast Volatility Far Better Than Direction"
REPORT_FILE = Path(__file__).resolve().parent.parent / "research" / "selloff_evidence_report.md"
BOTTOM_LINE = ("After a sharp selloff, research reliably predicts that volatility stays high and the range of outcomes "
               "widens. The direction of the next move is only weakly and conditionally predictable.")


def _src(label: str, url: str) -> dict:
    return {"label": label, "url": url}


# Ordered strongest first within each grade. ``on_page`` says what this page does with it.
EVIDENCE: list[dict] = [
    # ---------------- strong ----------------
    {"id": "post_selloff_volatility", "grade": "strong", "signal": "Volatility after a selloff",
     "finding": "Declines raise future volatility more than equal rallies, and that forecast holds out of sample from "
                "1 day to 3 months. Expected returns do not rise in step, and turning it into a timing rule failed "
                "out of sample across 103 strategies.",
     "on_page": "The fan's width and both tails are the most reliable output on this page.",
     "sources": [_src("Patton & Sheppard", "https://public.econ.duke.edu/~ap172/Patton_Sheppard_Realized_Semivariance_7oct11.pdf"),
                 _src("Moreira & Muir", "https://www.nber.org/papers/22208"),
                 _src("Cederburg et al.", "https://www.lehigh.edu/~xuy219/research/COWY.pdf")]},
    {"id": "era_reversal", "grade": "strong", "signal": "Short-term reversal since ~2000",
     "finding": "The S&P 500's 5-day autocorrelation measure flipped from +0.050 to −0.141 after 1999, so partial bounces "
                "over 1–5 sessions became modestly more likely — strongest when volatility and volume are high.",
     "on_page": "Analogs are split before / since 2000 in 'What happened after similar setups'.",
     "sources": [_src("Baltussen, van Bekkum & Da", "https://academicweb.nd.edu/~zda/Indexing.pdf"),
                 _src("Sentana & Wadhwani", "https://ideas.repec.org/a/ecj/econjl/v102y1992i411p415-25.html"),
                 _src("Campbell, Grossman & Wang", "https://dash.harvard.edu/bitstream/1/3128710/2/campbell_trading.pdf")]},
    {"id": "honest_statistics", "grade": "strong", "signal": "Most return predictors fail out of sample",
     "finding": "Predictors usually fail to beat the historical average out of sample; shrinkage recovers a little. "
                "Overlapping windows overstate significance, and with 40 analogs a 10-point edge is detected only 28% "
                "of the time.",
     "on_page": "Walk-forward track record, episode-robust ranges, Holm-adjusted badges and a best estimate shrunk to the base rate.",
     "sources": [_src("Welch & Goyal", "https://nber.org/papers/w10483"),
                 _src("Campbell & Thompson", "https://nber.org/papers/w11468"),
                 _src("Harvey, Liu & Zhu", "https://www.nber.org/papers/w20592"),
                 _src("Boudoukh, Richardson & Whitelaw", "https://www.nber.org/papers/w11841")]},
    {"id": "momentum_crash", "grade": "strong", "signal": "Momentum crashes (the factor, not the index)",
     "finding": "Crashes of past winners are forecastable from a bear-market, high-variance state — and they happen on "
                "index REBOUNDS, when past losers rally hardest. There is no evidence on what the index does after "
                "crowded-long selling days.",
     "on_page": "Momentum panic-state line; 'Crowded-long selling' vs 'Short squeeze / junk rally' labels.",
     "sources": [_src("Daniel & Moskowitz", "https://www.nber.org/system/files/working_papers/w20439/w20439.pdf"),
                 _src("Lou & Polk", "https://personal.lse.ac.uk/polk/research/hhab117.pdf"),
                 _src("Khandani & Lo", "https://www.newyorkfed.org/medialibrary/media/research/conference/2007/liquidity/Khandani_Lo.pdf")]},
    # ---------------- moderate ----------------
    {"id": "credit_lead_lag", "grade": "moderate", "signal": "Credit spreads",
     "finding": "Equities lead credit by a day or more (strong evidence), so spreads confirm rather than predict. "
                "'Credit not confirming means all clear' is contradicted. The page's excess-widening rule is moderate.",
     "on_page": "Credit counts only when it widens beyond what the equity move implies; FRED's one-day lag is flagged.",
     "sources": [_src("Kwan", "https://ideas.repec.org/a/eee/jfinec/v40y1996i1p63-80.html"),
                 _src("Hilscher, Pollet & Wilson", "https://resolve.cambridge.org/core/journals/journal-of-financial-and-quantitative-analysis/article/abs/are-credit-default-swaps-a-sideshow-evidence-that-information-flows-from-equity-to-cds-markets/F7CCD2FFB20AAEACE1E093D12EEF7A31")]},
    {"id": "trend_state", "grade": "moderate", "signal": "Trend (200-day, bull/bear state)",
     "finding": "Moving-average signals forecast monthly returns out of sample, mostly in recessions; bull/correction/"
                "bear/rebound states carry information. The best rules were insignificant out of sample in 1987–96.",
     "on_page": "The 200-day / 50-day tell and the trend chip.",
     "sources": [_src("Neely, Rapach, Tu & Zhou", "https://files.stlouisfed.org/files/htdocs/wp/2010/2010-008.pdf"),
                 _src("Goulding, Harvey & Mazzoleni", "https://people.duke.edu/~charvey/Research/Published_Papers/P167_Breaking_bad_trends.pdf"),
                 _src("Sullivan, Timmermann & White", "https://researchonline.lse.ac.uk/id/eprint/119144")]},
    {"id": "options_range", "grade": "moderate", "signal": "Options-implied range",
     "finding": "It is a risk-neutral price of insurance: too wide in calm markets and too narrow at 1 day after crashes. "
                "Risk-adjusted option densities pass calibration tests; raw ones don't.",
     "on_page": "The amber lines on the fan; coverage of a VIX-implied cone is shown from this page's own replay.",
     "sources": [_src("Bliss & Panigirtzoglou", "https://www.chicagofed.org/~/media/publications/working-papers/2001/wp2001-15r3-pdf.pdf"),
                 _src("Jackwerth & Menner", "https://www.aeaweb.org/conference/2018/preliminary/paper/yNn4GdY9")]},
    {"id": "dealer_gamma", "grade": "moderate", "signal": "Dealer gamma (volatility only)",
     "finding": "The public sign rule is validated only for end-of-day continuation that reverts over the next days; "
                "signed S&P data show dealers net long gamma 88% of the time, so the sign is uncertain.",
     "on_page": "A volatility-regime note — not a tell, not a direction call. Dealer levels are off by default.",
     "sources": [_src("Baltussen, Da, Lammers & Martens", "https://www3.nd.edu/~zda/intramom.pdf"),
                 _src("Adams et al.", "https://www.jean-sebastienfontaine.com/papers/0dte-options-volatility.pdf"),
                 _src("Amaya et al.", "https://cdn.cboe.com/resources/education/research_publications/gammasqueezes.pdf")]},
    {"id": "flight_to_safety", "grade": "moderate", "signal": "Flight to safety",
     "finding": "Flights to safety occur on under 2% of days; 62% last one day and none more than 10. Positive "
                "stock–bond correlation goes with inflation regimes, when bonds stop hedging.",
     "on_page": "The joint-tail flight-to-safety test and the stock–bond correlation line in the stress diagnosis.",
     "sources": [_src("Baele, Bekaert, Inghelbrecht & Wei", "https://business.columbia.edu/sites/default/files-efs/citation_file_upload/Flights%20to%20Safety.pdf"),
                 _src("Molenaar et al.", "https://rpc.cfainstitute.org/research/financial-analysts-journal/2024/empirical-evidence-on-the-stock-bond-correlation")]},
    {"id": "volume_shock", "grade": "moderate", "signal": "Selloff volume",
     "finding": "High-volume declines are more likely to be followed by higher expected returns; no effect size exists "
                "for 1–20-day S&P analogs.",
     "on_page": "Not used yet — a candidate feature that must pass the walk-forward test first.",
     "sources": [_src("Campbell, Grossman & Wang", "https://dash.harvard.edu/bitstream/1/3128710/2/campbell_trading.pdf")]},
    # ---------------- weak ----------------
    {"id": "vix_curve", "grade": "weak", "signal": "VIX curve (VIX vs VIX3M)",
     "finding": "A stress gauge: inversion marks higher realized volatility, but mainly predicts VIX products, not the "
                "index. 'Backwardation marks the bottom' is folklore — above 1.10 the outcomes were two-sided.",
     "on_page": "The VIX-curve tells and stress-grid row, graded as a regime gauge.",
     "sources": [_src("Johnson", "https://www.bauer.uh.edu/departments/finance/documents/seminars/Johnson_020712.pdf"),
                 _src("Bekaert & Hoerova", "https://www.nber.org/papers/w18995.pdf")]},
    {"id": "breadth_participation", "grade": "weak", "signal": "Breadth and participation",
     "finding": "Broad participation predicts continuation at MONTHLY horizons across 64 markets, with little "
                "short-term power. 'Narrow breadth means a drawdown is coming' rests on industry research only.",
     "on_page": "'Who is participating' panel, new-highs/new-lows and equal-weight tells — descriptive context.",
     "sources": [_src("Zaremba et al.", "https://ideas.repec.org/a/eee/ecmode/v97y2021icp348-364.html"),
                 _src("Brown & Cliff", "https://www.cxoadvisory.com/sentiment-indicators/does-investor-sentiment-give-trading-signals")]},
    {"id": "carry_unwind", "grade": "weak", "signal": "Yen carry unwinds",
     "finding": "Unwinds cluster with a rising VIX, but there is roughly one recent episode and no study of forward "
                "returns; a yen rally on its own is an ordinary safe-haven flow.",
     "on_page": "The carry-unwind tell needs a yen surge AND a VIX jump.",
     "sources": [_src("Brunnermeier, Nagel & Pedersen", "https://www.nber.org/papers/w14473"),
                 _src("BIS Bulletin 90", "https://www.bis.org/publ/bisbull90.htm")]},
    {"id": "stress_thresholds", "grade": "weak", "signal": "Cross-asset stress thresholds",
     "finding": "The grid's cut-offs are conventions; composite stress indices are well built but have no "
                "peer-reviewed 1–20-day S&P forecasting record.",
     "on_page": "The stress grid and diagnosis describe how broad the stress is — not what comes next.",
     "sources": [_src("Holló, Kremer & Lo Duca (CISS)", "https://www.ecb.europa.eu/pub/pdf/scpwps/ecbwp1426.pdf")]},
    # ---------------- folklore ----------------
    {"id": "vix_buy_signal", "grade": "folklore", "signal": "'VIX above 30 or 40 means buy'",
     "finding": "In-sample and descriptive (13 independent episodes at VIX ≥ 40), with the spread widening too; rigorous "
                "tests find no 1–3-month power for the VIX level.",
     "on_page": "Not used as a signal.",
     "sources": [_src("Johnson", "https://www.bauer.uh.edu/departments/finance/documents/seminars/Johnson_020712.pdf"),
                 _src("Bekaert & Hoerova", "https://www.nber.org/papers/w18995.pdf")]},
    {"id": "gamma_levels", "grade": "folklore", "signal": "Gamma flip / walls as support; post-opex 'unclench'",
     "finding": "Vendor claims only; the S&P index basket shows no pinning from SPX expirations.",
     "on_page": "Dealer levels are labelled estimates and hidden by default; expiry notes are neutral.",
     "sources": [_src("Golez & Jackwerth", "https://d-nb.info/1112655492/34")]},
    {"id": "breadth_triggers", "grade": "folklore", "signal": "Zweig thrust, Lowry 90% days, washout levels",
     "finding": "No peer-reviewed out-of-sample test; calibrated on NYSE data, not S&P 500 members (about 20 Zweig "
                "signals since 1945).",
     "on_page": "Shown as practitioner tells with this grade.",
     "sources": [_src("thetrading.tools (practitioner)", "https://www.thetrading.tools/concepts/zweig-breadth-thrust")]},
    {"id": "skew_timer", "grade": "folklore", "signal": "'SKEW times crashes'",
     "finding": "SKEW prices tails and its level has drifted; no peer-reviewed timing evidence was found.",
     "on_page": "Not used.",
     "sources": [_src("Bevilacqua & Tunaru (downside SKEW, a different measure)", "https://researchonline.lse.ac.uk/id/eprint/108198")]},
    {"id": "n_sigma_buy", "grade": "folklore", "signal": "'Buy after an N-σ 3-day drop'",
     "finding": "No peer-reviewed out-of-sample test, and no significant predictability was found during crashes.",
     "on_page": "This page's own walk-forward test is the check (see the track record).",
     "sources": [_src("Kim, Shamsuddin & Lim", "https://ideas.repec.org/a/eee/empfin/v18y2011i5p868-879.html")]},
    # ---------------- descriptive (tells only) ----------------
    {"id": "price_levels", "grade": "descriptive", "signal": "Price levels",
     "finding": "A price level, not a forecast: it tells you whether the selloff is still finding sellers.",
     "on_page": "Break / reclaim tells.", "sources": []},
]

BY_ID = {e["id"]: e for e in EVIDENCE}

# Every tell points at ONE registry entry, so its chip and the panel always agree.
TELL_TO_EVIDENCE = {
    "break_low": "price_levels", "retake_high": "price_levels", "reclaim": "price_levels",
    "trend": "trend_state",
    "vix_curve": "vix_curve", "vix_calm": "vix_curve",
    "credit_wide": "credit_lead_lag", "credit_calm": "credit_lead_lag",
    "yen": "carry_unwind",
    "breadth": "breadth_participation", "nl_gt_nh": "breadth_participation",
    "down90": "breadth_triggers", "up90": "breadth_triggers", "washout": "breadth_triggers", "zweig": "breadth_triggers",
}


def tell_evidence(tell_id: str) -> dict:
    """Grade + note for a tell, straight from the registry entry it maps to."""
    e = BY_ID[TELL_TO_EVIDENCE.get(tell_id, "price_levels")]
    return {"grade": e["grade"], "note": e["finding"], "ref": e["id"]}


def evidence_summary(calibration: dict | None = None, horizon_key: str = "h5") -> dict:
    """The panel payload: bottom line, this page's own walk-forward result, and the graded registry."""
    own = None
    cal = calibration or {}
    if cal.get("available"):
        sk = ((cal.get("skill") or {}).get(horizon_key) or {})
        st = sk.get("stress") or {}
        cov = ((cal.get("coverage") or {}).get(horizon_key) or {}).get("stress") or {}
        if st.get("bss_raw") is not None:
            own = {"badge": sk.get("badge"), "bss": st["bss_raw"], "lo": st.get("lo"), "hi": st.get("hi"),
                   "n": st.get("n"), "evaluated_on": cal.get("evaluated_on"),
                   "analog_fan_coverage": cov.get("analog_p10_p90"), "vix_cone_coverage": cov.get("vix_cone_p10_p90")}
    return {
        "bottom_line": BOTTOM_LINE,
        "own_test": own,
        "grades": GRADE_DEFINITIONS,
        "items": [{k: e[k] for k in ("id", "grade", "signal", "finding", "on_page", "sources")}
                  for e in EVIDENCE if e["grade"] != "descriptive"],
        "report": {"title": REPORT_TITLE, "available": REPORT_FILE.exists(),
                   "sources_note": "Peer-reviewed papers and Fed / NBER / ECB / BIS working papers; practitioner sources flagged."},
    }


@lru_cache(maxsize=1)
def report_markdown() -> str | None:
    try:
        return REPORT_FILE.read_text(encoding="utf-8")
    except OSError:
        return None
