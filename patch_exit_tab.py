import re

with open("frontend/src/components/ExitTab.tsx", "r") as f:
    content = f.read()

# Replace imports
content = content.replace('PillarPortfolio', 'PillarSentiment')

# Replace interface and props
content = re.sub(
    r'interface ExitTabProps \{.*?\}',
    'interface ExitTabProps {\n  ticker: string;\n}',
    content,
    flags=re.DOTALL
)

content = content.replace(
    'export function ExitTab({ ticker, holding, currentPrice }: ExitTabProps) {',
    'export function ExitTab({ ticker }: ExitTabProps) {\n  const [isInitialLoad, setIsInitialLoad] = React.useState(true);'
)

# Remove manual states
content = re.sub(r'  const \[manualShares.*?\n', '', content)
content = re.sub(r'  const \[manualCostBasis.*?\n', '', content)
content = re.sub(r'  const \[manualDate.*?\n', '', content)
content = re.sub(r'  const effectiveShares.*?\n', '', content)
content = re.sub(r'  const effectiveCostBasis.*?\n', '', content)
content = re.sub(r'  const effectiveDate.*?\n', '', content)

# Update loadAnalysis
load_analysis_replacement = '''  const loadAnalysis = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetchExitAnalysis(ticker);
      if (result.error) throw new Error(result.error);
      setData(result);
      setActiveSection('overview');
    } catch (err: any) {
      setError(err.message || 'Failed to fetch exit analysis.');
    } finally {
      setLoading(false);
      setIsInitialLoad(false);
    }
  }, [ticker]);

  React.useEffect(() => {
    if (isInitialLoad && !data && !loading) {
      loadAnalysis();
    }
  }, [isInitialLoad, data, loading, loadAnalysis]);'''

content = re.sub(r'  const loadAnalysis = useCallback.*?\[ticker, holding, manualShares, manualCostBasis, manualDate\]\);', load_analysis_replacement, content, flags=re.DOTALL)

# Remove Pre-analysis landing page block entirely.
# We will just replace it with `if (loading && !data) return <Loading />`
landing_page_regex = r'  /\* ---- Pre-analysis: show input form ---- \*/\n  if \(\!data && \!loading\) \{.*?\n  \}'
content = re.sub(landing_page_regex, '', content, flags=re.DOTALL)

# In the header, remove "effectiveShares @ effectiveCostBasis", and remove "Edit Position" button
content = content.replace(
    '''              <p className="text-xs text-base-content/40 mt-0.5">
                {effectiveShares} shares @ ${effectiveCostBasis.toFixed(2)}
                {holding && <span className="badge badge-xs badge-success ml-2 font-medium">Portfolio</span>}
              </p>''',
    ''
)
content = content.replace(
    '''            <button className="btn btn-ghost btn-sm rounded-xl gap-1.5 border border-white/[0.06]" onClick={() => setData(null)}>
              Edit Position
            </button>''',
    ''
)

# In pillarList, change portfolio to sentiment
content = content.replace(
    "{ key: 'portfolio', name: 'Portfolio', icon: <Settings className=\"w-4 h-4 text-info\" />, score: data.pillars.portfolio.score },",
    "{ key: 'sentiment', name: 'Sentiment', icon: <Settings className=\"w-4 h-4 text-info\" />, score: data.pillars.sentiment.score },"
)

# In the Pillars section, change PillarPortfolio
content = content.replace(
    '''<PillarCard title="Portfolio Optimization" icon={<Settings className="w-5 h-5 text-info" />} score={data.pillars.portfolio.score} description="Concentration, tax impact, benchmark comparison" ticker={ticker} pillarKey="Portfolio">
            <PillarPortfolio data={data.pillars.portfolio.data} />
          </PillarCard>''',
    '''<PillarCard title="Market Sentiment & Flow" icon={<Settings className="w-5 h-5 text-info" />} score={data.pillars.sentiment.score} description="Insider selling, short interest spikes, institutional flow" ticker={ticker} pillarKey="Sentiment">
            <PillarSentiment data={data.pillars.sentiment.data} />
          </PillarCard>'''
)

# In TechnicalSignals, remove costBasis prop
content = content.replace('costBasis={effectiveCostBasis}', 'costBasis={undefined}')

with open("frontend/src/components/ExitTab.tsx", "w") as f:
    f.write(content)
