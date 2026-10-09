import {
  getGpuCount,
  getJobEfficiencySummary,
  getSmIdleSummary,
  getSubmittedBy,
} from '@/utils/jobEfficiency';

describe('job efficiency summary', () => {
  it('computes GPU-hours, allocation cost, and restarts across tasks', () => {
    const job = { name: 'training-a1.alex', user: 'root' };
    const tasks = [
      {
        requested_resources: '2x[A100:4]',
        accelerators: { A100: 4 },
        job_duration: 1800,
        estimated_hourly_cost: 29.36,
        recoveries: 1,
      },
      {
        requested_resources: '1x[A100:1]',
        accelerators: { A100: 1 },
        job_duration: 3600,
        estimated_hourly_cost: 3.67,
        recoveries: 2,
      },
    ];

    expect(getJobEfficiencySummary(job, tasks)).toEqual({
      gpuHours: 5,
      estimatedCost: 18.35,
      restarts: 3,
      submittedBy: 'alex',
    });
  });

  it('uses persisted submitter metadata before the system Sky user', () => {
    expect(
      getSubmittedBy({
        metadata: { submitted_by: 'alex@flourishlabs.ai' },
        name: 'training-a1.alex',
        user: 'root',
      })
    ).toBe('alex@flourishlabs.ai');
  });

  it('keeps legacy and non-Flourish jobs usable', () => {
    expect(getSubmittedBy({ name: 'ordinary-job', user: 'jane' })).toBe('jane');
    expect(getGpuCount({ requested_resources: '1x[CPU:4]' })).toBe(0);
    expect(
      getJobEfficiencySummary({
        name: 'ordinary-job',
        user: 'jane',
        requested_resources: '1x[CPU:4]',
        job_duration: 30,
        recoveries: null,
      })
    ).toEqual({
      gpuHours: 0,
      estimatedCost: null,
      restarts: 0,
      submittedBy: 'jane',
    });
  });

  it('computes estimated SM-idle percent and GPU-hours from mean activity', () => {
    expect(getSmIdleSummary(5, 25)).toEqual({
      percent: 75,
      gpuHours: 3.75,
    });
    expect(getSmIdleSummary(5, null)).toEqual({
      percent: null,
      gpuHours: null,
    });
    expect(getSmIdleSummary(5, 101)).toEqual({
      percent: 0,
      gpuHours: 0,
    });
  });
});
