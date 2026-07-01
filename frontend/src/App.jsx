import { useState } from 'react'

function App() {
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [expandedRowId, setExpandedRowId] = useState(null)
  const [streamStatus, setStreamStatus] = useState('')
  const [streamDetails, setStreamDetails] = useState({})
  const [filters, setFilters] = useState({
    verdict: 'all',
    confidence: 'all',
    type: 'all',
  })
  const [sortOrder, setSortOrder] = useState('desc')

  const runAnalysis = async () => {
    setLoading(true)
    setError(null)
    setReport(null)
    setExpandedRowId(null)
    setStreamStatus('Initializing...')
    setStreamDetails({})

    try {
      const response = await fetch('http://localhost:8002/analyze', {
        method: 'POST',
      })

      if (!response.ok) {
        throw new Error(`Server responded with ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines[lines.length - 1]

        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i]
          if (line.startsWith('data: ')) {
            try {
              const eventData = JSON.parse(line.substring(6))
              setStreamStatus(eventData.message || `Stage: ${eventData.stage}`)
              setStreamDetails(eventData)

              if (eventData.stage === 'complete') {
                setReport(eventData.report)
                setLoading(false)
                setStreamStatus('')
              }
            } catch (e) {
              console.error('Failed to parse event:', e)
            }
          }
        }
      }
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  const getCitationFindings = () => {
    if (!report?.citations) return []
    return report.citations.map((f, idx) => ({
      id: `citation-${idx}`,
      type: 'Citation',
      description: f.citation.case_name,
      verdict: f.verdict,
      confidence: f.confidence,
      reasoning: f.reasoning,
      data: f,
    }))
  }

  const getFactFindings = () => {
    if (!report?.facts) return []
    return report.facts.map((f, idx) => ({
      id: `fact-${idx}`,
      type: 'Fact',
      description: f.claim,
      verdict: f.verdict,
      confidence: f.confidence,
      reasoning: f.confidence_reasoning,
      data: f,
    }))
  }

  const allFindings = [...getCitationFindings(), ...getFactFindings()]

  const filteredFindings = allFindings.filter((f) => {
    if (filters.verdict !== 'all' && f.verdict !== filters.verdict) return false

    if (filters.confidence !== 'all') {
      if (filters.confidence === 'low' && f.confidence >= 0.5) return false
      if (filters.confidence === 'medium' && (f.confidence < 0.5 || f.confidence >= 0.85)) return false
      if (filters.confidence === 'high' && f.confidence < 0.85) return false
      if (filters.confidence === '100' && f.confidence < 1.0) return false
    }

    if (filters.type !== 'all' && f.type !== filters.type) return false
    return true
  })

  const sortedFindings = filteredFindings.sort((a, b) => {
    if (a.type !== b.type) {
      return a.type === 'Citation' ? -1 : 1
    }
    const confDiff = b.confidence - a.confidence
    return sortOrder === 'desc' ? confDiff : -confDiff
  })

  const getVerdictColor = (verdict) => {
    switch (verdict) {
      case 'likely_fabricated':
        return '#dc2626'
      case 'does_not_support':
        return '#ea580c'
      case 'contradicts':
        return '#16a34a'
      case 'supports':
        return '#2563eb'
      default:
        return '#6b7280'
    }
  }

  const getVerdictLabel = (verdict) => {
    return verdict.toUpperCase().replace(/_/g, ' ')
  }

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '20px', fontFamily: 'system-ui, sans-serif' }}>
      <h1>VeriBrief</h1>
      <p style={{ color: '#666', marginBottom: '24px' }}>Legal brief verification pipeline</p>

      <button
        onClick={runAnalysis}
        disabled={loading}
        style={{
          padding: '10px 24px',
          fontSize: '16px',
          cursor: loading ? 'not-allowed' : 'pointer',
          backgroundColor: '#2563eb',
          color: 'white',
          border: 'none',
          borderRadius: '4px',
        }}
      >
        {loading ? 'Analyzing...' : 'Run Analysis'}
      </button>

      {loading && streamStatus && (
        <div style={{ marginTop: '20px', padding: '20px', backgroundColor: '#dbeafe', color: '#1e40af', borderRadius: '6px', border: '1px solid #93c5fd' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{
              width: '20px',
              height: '20px',
              border: '3px solid #93c5fd',
              borderTopColor: '#2563eb',
              borderRadius: '50%',
              animation: 'spin 1s linear infinite',
            }} />
            <div>
              <p style={{ margin: '0 0 4px 0', fontWeight: 'bold' }}>{streamStatus}</p>
              {streamDetails.count && (
                <p style={{ margin: 0, fontSize: '13px' }}>
                  {streamDetails.problematic !== undefined
                    ? `${streamDetails.count} checked, ${streamDetails.problematic} issues`
                    : `${streamDetails.count} found`}
                </p>
              )}
            </div>
          </div>
          <style>{`
            @keyframes spin {
              to { transform: rotate(360deg); }
            }
          `}</style>
        </div>
      )}

      {error && (
        <div style={{ marginTop: '20px', padding: '12px', backgroundColor: '#fee2e2', color: '#991b1b', borderRadius: '4px' }}>
          <strong>Error:</strong> {error}
        </div>
      )}

      {report && (
        <div style={{ marginTop: '32px' }}>
          {report.judicial_memo && (
            <div style={{
              backgroundColor: '#f3f4f6',
              padding: '20px',
              borderRadius: '6px',
              marginBottom: '32px',
              borderLeft: '4px solid #2563eb',
            }}>
              <h3 style={{ marginTop: 0, marginBottom: '12px' }}>Judicial Summary</h3>
              <p style={{ marginTop: 0, lineHeight: '1.6', color: '#374151' }}>
                {report.judicial_memo}
              </p>
            </div>
          )}

          <div style={{ marginBottom: '32px', paddingBottom: '24px', borderBottom: '1px solid #e5e7eb' }}>
            <div style={{ marginBottom: '20px' }}>
              <p style={{ margin: '0 0 16px 0', fontSize: '13px', fontWeight: '500', color: '#666', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Filters</p>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '20px' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '12px', marginBottom: '8px', fontWeight: '500', color: '#374151' }}>Verdict</label>
                  <select
                    value={filters.verdict}
                    onChange={(e) => setFilters({ ...filters, verdict: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      fontSize: '14px',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      backgroundColor: 'white',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    <option value="all">All verdicts</option>
                    <option value="likely_fabricated">Likely Fabricated</option>
                    <option value="does_not_support">Does Not Support</option>
                    <option value="contradicts">Contradicts</option>
                    <option value="supports">Supports</option>
                    <option value="error">Error</option>
                  </select>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', marginBottom: '8px', fontWeight: '500', color: '#374151' }}>Confidence</label>
                  <select
                    value={filters.confidence}
                    onChange={(e) => setFilters({ ...filters, confidence: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      fontSize: '14px',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      backgroundColor: 'white',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    <option value="all">All levels</option>
                    <option value="low">Low (&lt;50%)</option>
                    <option value="medium">Medium (50–85%)</option>
                    <option value="high">High (&gt;85%)</option>
                    <option value="100">100%</option>
                  </select>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', marginBottom: '8px', fontWeight: '500', color: '#374151' }}>Type</label>
                  <select
                    value={filters.type}
                    onChange={(e) => setFilters({ ...filters, type: e.target.value })}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      fontSize: '14px',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      backgroundColor: 'white',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                    }}
                  >
                    <option value="all">All types</option>
                    <option value="Citation">Citation</option>
                    <option value="Fact">Fact</option>
                  </select>
                </div>

                <div>
                  <label style={{ display: 'block', fontSize: '12px', marginBottom: '8px', fontWeight: '500', color: '#374151' }}>Sort</label>
                  <button
                    onClick={() => setSortOrder(sortOrder === 'desc' ? 'asc' : 'desc')}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      fontSize: '14px',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      backgroundColor: '#f9fafb',
                      cursor: 'pointer',
                      fontFamily: 'inherit',
                      fontWeight: '500',
                      color: '#374151',
                      transition: 'all 0.2s',
                    }}
                    onMouseOver={(e) => e.target.style.backgroundColor = '#f3f4f6'}
                    onMouseOut={(e) => e.target.style.backgroundColor = '#f9fafb'}
                  >
                    {sortOrder === 'desc' ? '↓ High to Low' : '↑ Low to High'}
                  </button>
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <p style={{ margin: 0, fontSize: '13px', color: '#666' }}>
                Showing <span style={{ fontWeight: '600', color: '#374151' }}>{sortedFindings.length}</span> result{sortedFindings.length !== 1 ? 's' : ''}
              </p>
            </div>
          </div>

          <div>
            {sortedFindings.length === 0 ? (
              <p style={{ color: '#666' }}>No findings match the current filters.</p>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                {sortedFindings.map((finding) => (
                  <div
                    key={finding.id}
                    onClick={() => setExpandedRowId(expandedRowId === finding.id ? null : finding.id)}
                    style={{
                      cursor: 'pointer',
                      border: '1px solid #e5e7eb',
                      borderRadius: '6px',
                      backgroundColor: expandedRowId === finding.id ? '#f9fafb' : 'white',
                      transition: 'background-color 0.2s',
                    }}
                  >
                    <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '16px',
                      padding: '16px',
                    }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', marginBottom: '8px' }}>
                          <span style={{
                            display: 'inline-block',
                            padding: '4px 8px',
                            backgroundColor: '#e5e7eb',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: 'bold',
                            minWidth: '80px',
                            textAlign: 'center',
                          }}>
                            {finding.type}
                          </span>
                          <span style={{
                            display: 'inline-block',
                            padding: '4px 12px',
                            backgroundColor: getVerdictColor(finding.verdict),
                            color: 'white',
                            borderRadius: '4px',
                            fontSize: '12px',
                            fontWeight: 'bold',
                          }}>
                            {getVerdictLabel(finding.verdict)}
                          </span>
                        </div>
                        <p style={{ margin: '0 0 4px 0', color: '#374151', lineHeight: '1.5' }}>
                          {finding.description}
                        </p>
                      </div>

                      <div style={{ display: 'flex', alignItems: 'center', gap: '16px', whiteSpace: 'nowrap' }}>
                        <div style={{ textAlign: 'right' }}>
                          <div style={{ fontSize: '12px', color: '#666', marginBottom: '4px' }}>Confidence</div>
                          <div style={{
                            fontSize: '18px',
                            fontWeight: 'bold',
                            color: finding.confidence > 0.9 ? '#16a34a' : finding.confidence > 0.7 ? '#ea580c' : '#6b7280',
                          }}>
                            {(finding.confidence * 100).toFixed(0)}%
                          </div>
                        </div>
                        <div style={{ fontSize: '20px', color: '#9ca3af' }}>
                          {expandedRowId === finding.id ? '▼' : '▶'}
                        </div>
                      </div>
                    </div>

                    {expandedRowId === finding.id && (
                      <div style={{
                        borderTop: '1px solid #e5e7eb',
                        padding: '16px',
                        backgroundColor: '#f9fafb',
                        color: '#374151',
                        lineHeight: '1.6',
                      }}>
                        <p style={{ marginTop: 0 }}><strong>Reasoning:</strong></p>
                        <p style={{ marginBottom: '16px' }}>{finding.reasoning}</p>

                        {finding.data.source_quote && (
                          <>
                            <p style={{ marginTop: 0, marginBottom: '8px' }}><strong>Source Quote:</strong></p>
                            <div style={{
                              backgroundColor: 'white',
                              padding: '12px',
                              borderRadius: '4px',
                              borderLeft: '3px solid #2563eb',
                              fontStyle: 'italic',
                              marginBottom: '16px',
                              color: '#555',
                            }}>
                              {finding.data.source_quote}
                            </div>
                          </>
                        )}

                        {finding.data.source_doc && (
                          <p><strong>Source Document:</strong> {finding.data.source_doc}</p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {report === null && !loading && !error && (
        <p style={{ marginTop: '20px', color: '#888' }}>
          Click "Run Analysis" to analyze the case documents.
        </p>
      )}
    </div>
  )
}

export default App
