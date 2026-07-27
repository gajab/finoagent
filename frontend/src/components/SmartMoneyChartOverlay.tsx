import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { TechnicalData } from '../types';

interface Props {
  technical: TechnicalData;
  timeframeShortLabel: string;
}



export const SmartMoneyChartOverlay: React.FC<Props> = ({ technical, timeframeShortLabel }) => {
  const option = useMemo(() => {
    if (!technical.timestamps?.length || !technical.prices?.length) return {};

    const categoryData = technical.timestamps;
    
    // Construct candlestick data [open, close, low, high]
    const values = technical.prices.map((close, i) => {
      const open = i === 0 ? close : technical.prices[i - 1];
      const low = technical.lows?.[i] ?? Math.min(open, close);
      const high = technical.highs?.[i] ?? Math.max(open, close);
      return [open, close, low, high];
    });

    const dailyVolume = technical.volumes || [];
    const currentPrice = technical.prices[technical.prices.length - 1];

    // Smart Money Zones
    const markAreaZones: any[] = [];
    technical.institutional?.order_blocks?.forEach(ob => {
      markAreaZones.push([
        { 
          name: `${ob.type === 'bullish' ? 'Demand' : 'Supply'} OB ($${ob.price.toFixed(2)})`, 
          yAxis: ob.bottom, 
          itemStyle: { 
            color: ob.type === 'bullish' ? 'rgba(16, 185, 129, 0.15)' : 'rgba(244, 63, 94, 0.1)', 
            borderWidth: 1, 
            borderColor: ob.type === 'bullish' ? 'rgba(16, 185, 129, 0.5)' : 'rgba(244, 63, 94, 0.3)' 
          },
          label: { position: 'insideLeft', color: '#ccc', fontSize: 10, offset: [10, 0] }
        },
        { yAxis: ob.top }
      ]);
    });

    technical.institutional?.fair_value_gaps?.forEach(fvg => {
      markAreaZones.push([
        { 
          name: `${fvg.type === 'bullish' ? 'Bullish' : 'Bearish'} FVG ($${fvg.mid.toFixed(2)})`, 
          yAxis: fvg.bottom, 
          itemStyle: { 
            color: fvg.type === 'bullish' ? 'rgba(99, 102, 241, 0.15)' : 'rgba(168, 85, 247, 0.15)', 
            borderWidth: 1, 
            borderType: 'dashed', 
            borderColor: fvg.type === 'bullish' ? 'rgba(99, 102, 241, 0.5)' : 'rgba(168, 85, 247, 0.5)' 
          },
          label: { position: 'insideRight', color: '#ccc', fontSize: 10, offset: [-10, 0] }
        },
        { yAxis: fvg.top }
      ]);
    });

    // Mark Lines (POC, Support, Resistance)
    const markLineData: any[] = [
      {
        name: 'Current Price',
        yAxis: currentPrice,
        lineStyle: { color: '#f59e0b', type: 'dashed', width: 1 },
        label: { formatter: `NOW $${currentPrice.toFixed(2)}`, position: 'insideEndTop', color: '#fbbf24', fontSize: 11, backgroundColor: '#111', padding: 2 }
      }
    ];

    if (technical.institutional?.volume_profile?.poc) {
      markLineData.push({
        name: 'POC',
        yAxis: technical.institutional.volume_profile.poc,
        lineStyle: { color: '#8b5cf6', type: 'solid', width: 2 },
        label: { formatter: `POC $${technical.institutional.volume_profile.poc.toFixed(2)}`, position: 'insideStartTop', color: '#a78bfa', fontSize: 11 }
      });
    }

    if (technical.supportLevel) {
      markLineData.push({
        name: 'Major Support',
        yAxis: technical.supportLevel,
        lineStyle: { color: '#10b981', type: 'solid', width: 2 },
        label: { formatter: `Support $${technical.supportLevel.toFixed(2)}`, position: 'insideEndBottom', color: '#6ee7b7', fontSize: 11 }
      });
    }
    
    if (technical.resistanceLevel) {
      markLineData.push({
        name: 'Major Resistance',
        yAxis: technical.resistanceLevel,
        lineStyle: { color: '#ef4444', type: 'solid', width: 2 },
        label: { formatter: `Resistance $${technical.resistanceLevel.toFixed(2)}`, position: 'insideEndTop', color: '#fca5a5', fontSize: 11 }
      });
    }

    // Volume Profile Data
    const priceBins = technical.institutional?.volume_profile?.bins?.map(b => b.price) || [];
    const volumeBins = technical.institutional?.volume_profile?.bins?.map(b => b.volume) || [];

    return {
      animation: false,
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'cross' },
        backgroundColor: '#1e2230',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0' }
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      
      grid: [
        { left: '6%', right: '6%', bottom: '25%', top: '5%' },             // Main candlestick chart
        { left: '6%', width: '20%', bottom: '25%', top: '5%' },            // Volume Profile Grid
        { left: '6%', right: '6%', bottom: '5%', height: '15%' }           // Daily Volume Grid
      ],
      
      xAxis: [
        {
          type: 'category',
          data: categoryData,
          gridIndex: 0,
          scale: true,
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#334155' } },
          splitLine: { show: false },
          axisLabel: { show: false } 
        },
        {
          type: 'value',
          gridIndex: 1,
          show: false,
          max: 'dataMax'
        },
        {
          type: 'category',
          data: categoryData,
          gridIndex: 2,
          scale: true,
          boundaryGap: false,
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { color: '#94a3b8' }
        }
      ],
      
      yAxis: [
        {
          scale: true,
          gridIndex: 0,
          position: 'right',
          splitArea: { show: false },
          splitLine: { lineStyle: { color: '#1e2230' } },
          axisLine: { lineStyle: { color: '#334155' } },
          axisLabel: { color: '#94a3b8', formatter: '${value}' }
        },
        {
          type: 'category',
          data: priceBins,
          gridIndex: 1,
          show: false
        },
        {
          type: 'value',
          gridIndex: 2,
          show: false
        }
      ],
      
      series: [
        {
          name: 'Price',
          type: 'candlestick',
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: values,
          itemStyle: {
            color: '#10b981',      
            color0: '#ef4444',     
            borderColor: '#10b981',
            borderColor0: '#ef4444'
          },
          markArea: {
            silent: true, 
            data: markAreaZones
          },
          markLine: {
            symbol: ['none', 'none'],
            data: markLineData
          }
        },
        // Volume Profile Series
        {
          name: 'Volume Profile',
          type: 'bar',
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: volumeBins,
          itemStyle: { color: 'rgba(99, 102, 241, 0.2)' },
          barWidth: '95%',
          silent: true
        },
        // Timeline Volume Series
        {
          name: 'Volume',
          type: 'bar',
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: dailyVolume,
          itemStyle: {
            color: (params: any) => {
              const idx = params.dataIndex;
              return values[idx][1] >= values[idx][0] ? '#10b981' : '#ef4444';
            }
          }
        }
      ]
    };
  }, [technical]);

  return (
    <div className="w-full h-[600px]">
      <ReactECharts option={option} style={{ height: '100%', width: '100%' }} theme="dark" />
    </div>
  );
};
