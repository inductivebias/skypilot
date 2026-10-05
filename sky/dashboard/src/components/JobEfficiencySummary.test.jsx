import { render, screen } from '@testing-library/react';

import { JobEfficiencySummary } from '@/components/JobEfficiencySummary';
import { useJobEfficiencyMetrics } from '@/data/connectors/jobs';

jest.mock('@/data/connectors/jobs', () => ({
  useJobEfficiencyMetrics: jest.fn(),
}));

describe('JobEfficiencySummary', () => {
  it('puts workload and hardware efficiency metrics in the summary', () => {
    useJobEfficiencyMetrics.mockReturnValue({
      available: true,
      progress: {
        steps_per_second: 3.5,
        flops_per_second: 2.5e14,
        mfu_percent: 47.5,
      },
      hardware: {
        sm_active_percent: {
          p10: 10,
          p25: 25,
          p50: 50,
          p75: 75,
          p99: 99,
        },
      },
    });

    render(
      <JobEfficiencySummary
        job={{
          name: 'training-a1.alex',
          user: 'root',
          requested_resources: '1x[A100:1]',
          job_duration: 3600,
          estimated_hourly_cost: 3.67,
        }}
        tasks={[]}
      />
    );

    expect(screen.getByText('3.5')).toBeInTheDocument();
    expect(screen.getByText('250 TFLOP/s')).toBeInTheDocument();
    expect(screen.getByText('47.5%')).toBeInTheDocument();
    expect(screen.getByText('SM active')).toBeInTheDocument();
    expect(screen.getByText('50%')).toBeInTheDocument();
    expect(screen.getAllByText('N/A').length).toBeGreaterThan(0);
  });
});
