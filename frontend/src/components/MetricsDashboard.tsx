import React, { useEffect, useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler,
} from "chart.js";
import { Line, Bar } from "react-chartjs-2";
import { Activity, Clock, AlertTriangle, Hash, RefreshCcw } from "lucide-react";

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
  Filler
);

interface ProviderSummary {
  provider: string;
  total_24h: number;
  avg_latency_ms: number;
  rpm_24h: number;
  error_count_24h: number;
  error_rate_24h: number;
}

interface TimeseriesData {
  timestamp: string;
  provider: string;
  requests: number;
  avg_latency_ms: number;
  errors: number;
}

interface EndpointData {
  endpoint: string;
  provider: string;
  requests: number;
  avg_latency_ms: number;
  errors: number;
}

export function MetricsDashboard() {
  const [category, setCategory] = useState<"quote" | "llm">("quote");
  const [subProvider, setSubProvider] = useState<string>("all");
  const [days, setDays] = useState<number>(7);
  const [interval, setIntervalVal] = useState<string>("day");
  const [selectedEndpoint, setSelectedEndpoint] = useState<string | null>(null);

  const [summary, setSummary] = useState<ProviderSummary[]>([]);
  const [timeseries, setTimeseries] = useState<TimeseriesData[]>([]);
  const [endpoints, setEndpoints] = useState<EndpointData[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    setLoading(true);
    try {
      const authHeader: Record<string, string> = localStorage.getItem("token")
        ? { Authorization: `Bearer ${localStorage.getItem("token")}` }
        : {};

      const [sumRes, timeRes, endRes] = await Promise.all([
        fetch(`/api/metrics/summary`, { headers: authHeader }),
        fetch(`/api/metrics/timeseries?provider=all&days=${days}&interval=${interval}${selectedEndpoint ? `&endpoint_name=${selectedEndpoint}` : ""}`, { headers: authHeader }),
        fetch(`/api/metrics/endpoints?provider=all`, { headers: authHeader }),
      ]);

      if (sumRes.ok) {
        const sumData = await sumRes.json();
        setSummary(sumData.providers || []);
      }
      if (timeRes.ok) {
        const timeData = await timeRes.json();
        setTimeseries(timeData.timeseries || []);
      }
      if (endRes.ok) {
        const endData = await endRes.json();
        setEndpoints(endData.endpoints || []);
      }
    } catch (err) {
      console.error("Failed to fetch metrics", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, [days, interval, selectedEndpoint]);

  // Derived metrics for summary cards based on selected provider category
  const allowedProviders = category === "quote" ? ["ibkr", "yfinance"] : ["openai"];
  const activeProviders = subProvider === "all" ? allowedProviders : [subProvider];

  const filteredSummary = summary.filter((s) => activeProviders.includes(s.provider));
  const filteredTimeseries = timeseries.filter((t) => activeProviders.includes(t.provider));
  const filteredEndpoints = endpoints.filter((e) => activeProviders.includes(e.provider));

  const totalRequests = filteredSummary.reduce((acc, curr) => acc + curr.total_24h, 0);
  const totalErrors = filteredSummary.reduce((acc, curr) => acc + curr.error_count_24h, 0);
  const avgLatency = filteredSummary.length > 0 
    ? filteredSummary.reduce((acc, curr) => acc + curr.avg_latency_ms * curr.total_24h, 0) / (totalRequests || 1)
    : 0;
  
  const errorRate = totalRequests > 0 ? (totalErrors / totalRequests) * 100 : 0;
  const rpm = filteredSummary.reduce((acc, curr) => acc + curr.rpm_24h, 0);

  // Group timeseries by date regardless of provider to stack lines if "all" is selected
  const dates = Array.from(new Set(filteredTimeseries.map((t) => t.timestamp))).sort();
  
  const lineChartData = {
    labels: dates,
    datasets: [
      {
        label: "Requests",
        data: dates.map(dt => {
          return filteredTimeseries.filter(t => t.timestamp === dt).reduce((acc, curr) => acc + curr.requests, 0);
        }),
        borderColor: "rgba(99, 102, 241, 1)",
        backgroundColor: "rgba(99, 102, 241, 0.1)",
        fill: true,
        tension: 0.4,
      },
      {
        label: "Errors",
        data: dates.map(dt => {
          return filteredTimeseries.filter(t => t.timestamp === dt).reduce((acc, curr) => acc + curr.errors, 0);
        }),
        borderColor: "rgba(239, 68, 68, 1)",
        backgroundColor: "rgba(239, 68, 68, 0.1)",
        fill: true,
        tension: 0.4,
      }
    ],
  };

  const lineChartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: "top" as const },
      title: { display: false },
    },
    scales: {
      y: { beginAtZero: true }
    }
  };

  return (
    <div className="w-full max-w-7xl mx-auto space-y-8 p-4">
      {/* Header and Controls */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
        <div>
          <h2 className="text-3xl font-bold bg-gradient-to-r from-blue-400 to-indigo-500 bg-clip-text text-transparent">
            API Observability
          </h2>
          <p className="text-base-content/70 mt-1">Monitor latencies, request volume, and limits.</p>
        </div>
        
        <div className="flex items-center gap-4">
          <div className="tabs tabs-boxed bg-base-200/50 p-1 mr-4">
            <button
              className={`tab tab-sm ${category === "quote" ? "tab-active bg-primary text-primary-content" : ""}`}
              onClick={() => { setCategory("quote"); setSubProvider("all"); }}
            >
              Quote Providers
            </button>
            <button
              className={`tab tab-sm ${category === "llm" ? "tab-active bg-primary text-primary-content" : ""}`}
              onClick={() => { setCategory("llm"); setSubProvider("all"); }}
            >
              LLMs
            </button>
          </div>

          {category === "quote" && (
            <select
              className="select select-bordered select-sm w-40"
              value={subProvider}
              onChange={(e) => setSubProvider(e.target.value)}
            >
              <option value="all">All Quote APIs</option>
              <option value="ibkr">Interactive Brokers</option>
              <option value="yfinance">Yahoo Finance</option>
            </select>
          )}

          <select
            className="select select-bordered select-sm w-32"
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          >
            <option value={1}>Last 24 Hours</option>
            <option value={7}>Last 7 Days</option>
          </select>

          <select
            className="select select-bordered select-sm w-32"
            value={interval}
            onChange={(e) => setIntervalVal(e.target.value)}
          >
            <option value="day">Daily</option>
            <option value="hour">Hourly (RPH)</option>
            <option value="minute">Per Minute (RPM)</option>
            <option value="second">Per Second (RPS)</option>
          </select>

          <button onClick={fetchData} className="btn btn-sm btn-ghost btn-square" title="Refresh">
            <RefreshCcw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="card bg-base-200/50 backdrop-blur border border-base-content/10">
          <div className="card-body p-5 flex flex-row items-center gap-4">
            <div className="p-3 rounded-xl bg-blue-500/20 text-blue-400">
              <Hash className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs text-base-content/60 font-semibold uppercase tracking-wider">Requests (24h)</p>
              <h3 className="text-2xl font-bold">{totalRequests.toLocaleString()}</h3>
            </div>
          </div>
        </div>

        <div className="card bg-base-200/50 backdrop-blur border border-base-content/10">
          <div className="card-body p-5 flex flex-row items-center gap-4">
            <div className="p-3 rounded-xl bg-indigo-500/20 text-indigo-400">
              <Activity className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs text-base-content/60 font-semibold uppercase tracking-wider">RPM (24h)</p>
              <h3 className="text-2xl font-bold">{rpm.toFixed(2)}</h3>
            </div>
          </div>
        </div>

        <div className="card bg-base-200/50 backdrop-blur border border-base-content/10">
          <div className="card-body p-5 flex flex-row items-center gap-4">
            <div className="p-3 rounded-xl bg-green-500/20 text-green-400">
              <Clock className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs text-base-content/60 font-semibold uppercase tracking-wider">Avg Latency</p>
              <h3 className="text-2xl font-bold">{avgLatency.toFixed(0)} ms</h3>
            </div>
          </div>
        </div>

        <div className="card bg-base-200/50 backdrop-blur border border-base-content/10">
          <div className="card-body p-5 flex flex-row items-center gap-4">
            <div className="p-3 rounded-xl bg-red-500/20 text-red-400">
              <AlertTriangle className="w-6 h-6" />
            </div>
            <div>
              <p className="text-xs text-base-content/60 font-semibold uppercase tracking-wider">Error Rate</p>
              <h3 className="text-2xl font-bold">{errorRate.toFixed(2)}%</h3>
            </div>
          </div>
        </div>
      </div>

      {/* Main Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="card bg-base-200/30 border border-base-content/10 lg:col-span-2 shadow-sm">
          <div className="card-body p-6">
            <h3 className="card-title text-lg mb-4">
              {selectedEndpoint ? `Volume: ${selectedEndpoint}` : `Request Volume`} ({days} Days)
            </h3>
            <div className="w-full h-72">
              <Line data={lineChartData} options={lineChartOptions} />
            </div>
          </div>
        </div>

        <div className="card bg-base-200/30 border border-base-content/10 shadow-sm relative overflow-hidden">
           <div className="card-body p-6 relative z-10 flex flex-col h-full">
            <h3 className="card-title text-lg mb-4">Endpoint Performance</h3>
            <div className="overflow-auto flex-grow pr-2">
              <table className="table table-sm table-pin-rows w-full">
                <thead>
                  <tr className="bg-transparent border-b border-base-content/10">
                    <th className="text-base-content/70 font-medium pb-2 text-left pl-0">Endpoint</th>
                    <th className="text-base-content/70 font-medium pb-2 text-right">Reqs</th>
                    <th className="text-base-content/70 font-medium pb-2 text-right pr-0">MS</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredEndpoints.map((ep, idx) => (
                    <tr 
                      key={idx} 
                      className={`border-b border-base-content/5 hover:bg-base-content/10 transition-colors cursor-pointer ${selectedEndpoint === ep.endpoint ? 'bg-base-content/10' : ''}`}
                      onClick={() => setSelectedEndpoint(ep.endpoint === selectedEndpoint ? null : ep.endpoint)}
                    >
                      <td className="py-3 pl-2">
                        <div className="text-sm font-medium break-all" title={ep.endpoint}>
                          {ep.endpoint}
                        </div>
                        <div className="text-[10px] text-base-content/50 uppercase tracking-wider mt-0.5">
                          {ep.provider}
                        </div>
                      </td>
                      <td className="text-right py-3 text-sm font-medium">{ep.requests.toLocaleString()}</td>
                      <td className="text-right py-3 pr-0 text-sm">
                        <span className={`px-2 py-0.5 rounded-full ${ep.avg_latency_ms > 1000 ? 'bg-orange-500/20 text-orange-400' : 'bg-base-content/10'}`}>
                           {Math.round(ep.avg_latency_ms)}
                        </span>
                      </td>
                    </tr>
                  ))}
                  {filteredEndpoints.length === 0 && (
                     <tr>
                        <td colSpan={3} className="text-center py-8 text-base-content/50">No data available</td>
                     </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
