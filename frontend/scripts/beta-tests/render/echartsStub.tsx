// jsdom has no canvas, so ECharts cannot draw. The render tests swap it for this stub, which exposes what the chart WAS GIVEN
// (candle count, y-range, number of drawn lines/bands) so the tests can assert on the real option the Beta builds.
import React from 'react';
export default function ReactECharts({ option }: { option: any }) {
  const s0 = option?.series?.[0];
  return (
    <div data-testid="echart" data-candles={s0?.data?.length ?? 0} data-ymin={option?.yAxis?.[0]?.min} data-ymax={option?.yAxis?.[0]?.max}
      data-lines={s0?.markLine?.data?.length ?? 0} data-bands={s0?.markArea?.data?.length ?? 0} />
  );
}
