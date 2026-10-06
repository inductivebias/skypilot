import { useState } from 'react';
import { ChevronDownIcon, ChevronRightIcon } from 'lucide-react';
import PropTypes from 'prop-types';

import { useJobEfficiencyMetrics } from '@/data/connectors/jobs';
import {
  getGpuCount,
  getJobEfficiencySummary,
  getSmIdleSummary,
} from '@/utils/jobEfficiency';

const HARDWARE_ROWS = [
  ['SM active', 'sm_active_percent', 'percent'],
  ['VRAM used', 'vram_used_percent', 'percent'],
  ['HBM active', 'dram_active_percent', 'percent'],
  ['PCIe receive', 'pcie_rx_bytes_per_second', 'bytes'],
  ['PCIe transmit', 'pcie_tx_bytes_per_second', 'bytes'],
  ['NVLink receive', 'nvlink_rx_bytes_per_second', 'bytes'],
  ['NVLink transmit', 'nvlink_tx_bytes_per_second', 'bytes'],
];
const PERCENTILES = ['p10', 'p25', 'p50', 'p75', 'p99'];

function formatGpuHours(value) {
  if (value == null) return 'N/A';
  return value.toLocaleString(undefined, {
    minimumFractionDigits: value < 0.01 ? 3 : 2,
    maximumFractionDigits: value < 0.01 ? 3 : 2,
  });
}

function formatUsd(value) {
  if (value == null) return 'N/A';
  return value.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function formatNumber(value, maximumFractionDigits = 2) {
  if (value == null || !Number.isFinite(Number(value))) return 'N/A';
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits,
  });
}

function formatHardware(value, unit) {
  if (unit === 'percent') return `${formatNumber(value, 1)}%`;
  if (unit === 'bytes') return `${formatNumber(value / 1e9, 2)} GB/s`;
  return formatNumber(value);
}

function formatSmIdle(idle) {
  if (idle.percent == null) return 'N/A';
  const percent = `${formatNumber(idle.percent, 1)}%`;
  return idle.gpuHours == null
    ? percent
    : `${percent} \u00b7 ${formatGpuHours(idle.gpuHours)} GPU-h`;
}

function Metric({ label, value, title }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white px-4 py-3">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div
        className="mt-1 truncate text-2xl font-semibold text-slate-900"
        title={title}
      >
        {value}
      </div>
    </div>
  );
}

Metric.propTypes = {
  label: PropTypes.string.isRequired,
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.number]).isRequired,
  title: PropTypes.string,
};

export function JobEfficiencySummary({ job, tasks = [] }) {
  const [isExpanded, setIsExpanded] = useState(true);
  const summary = getJobEfficiencySummary(job, tasks);
  const telemetry = useJobEfficiencyMetrics(job, tasks);
  const progress = telemetry.progress || {};
  const smIdle = getSmIdleSummary(
    summary.gpuHours,
    telemetry.hardware?.sm_active_percent?.mean
  );
  const records = tasks.length > 0 ? tasks : [job];
  const isGpuJob = records.some((record) => getGpuCount(record) > 0);
  return (
    <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <button
        type="button"
        aria-controls="job-efficiency-summary-content"
        aria-expanded={isExpanded}
        className="flex w-full items-start justify-between gap-4 text-left"
        onClick={() => setIsExpanded((expanded) => !expanded)}
      >
        <div>
          <div className="font-semibold text-slate-900">Efficiency summary</div>
          <div className="text-xs text-slate-500">
            Allocation usage; estimated cost excludes storage, networking, and
            shared overhead.
          </div>
        </div>
        {isExpanded ? (
          <ChevronDownIcon className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
        ) : (
          <ChevronRightIcon className="mt-0.5 h-5 w-5 shrink-0 text-slate-500" />
        )}
      </button>
      {isExpanded && (
        <div id="job-efficiency-summary-content" className="mt-3">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Metric
              label="Estimated cost"
              value={formatUsd(summary.estimatedCost)}
              title="Runtime multiplied by configured allocation price"
            />
            <Metric
              label="GPU-hours"
              value={formatGpuHours(summary.gpuHours)}
              title="Allocated GPUs multiplied by active job duration"
            />
            <Metric label="Restarts" value={summary.restarts} />
            <Metric
              label="Submitted by"
              value={summary.submittedBy}
              title={summary.submittedBy}
            />
          </div>
          <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-4">
            <Metric
              label="Estimated SM idle"
              value={formatSmIdle(smIdle)}
              title="One minus mean SM-active utilization; GPU-hours are estimated across the allocated GPUs"
            />
            <Metric
              label="Steps/second"
              value={formatNumber(progress.steps_per_second)}
            />
            <Metric
              label="FLOPs"
              value={
                progress.flops_per_second == null
                  ? 'N/A'
                  : `${formatNumber(progress.flops_per_second / 1e12)} TFLOP/s`
              }
              title="Reported steps/second multiplied by explicit model FLOPs per global step"
            />
            <Metric
              label="MFU"
              value={
                progress.mfu_percent == null
                  ? 'N/A'
                  : `${formatNumber(progress.mfu_percent, 1)}%`
              }
              title="FLOP/s divided by explicit per-GPU peak FLOP/s and allocated GPU count"
            />
          </div>
          {isGpuJob ? (
            <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200 bg-white">
              <table className="min-w-full text-sm">
                <thead className="bg-slate-100 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-3 py-2 text-left font-medium">
                      Hardware
                    </th>
                    {PERCENTILES.map((percentile) => (
                      <th
                        key={percentile}
                        className="px-3 py-2 text-right font-medium"
                      >
                        {percentile.toUpperCase()}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {HARDWARE_ROWS.map(([label, key, unit]) => (
                    <tr key={key}>
                      <td className="whitespace-nowrap px-3 py-2 font-medium text-slate-700">
                        {label}
                      </td>
                      {PERCENTILES.map((percentile) => (
                        <td
                          key={percentile}
                          className="whitespace-nowrap px-3 py-2 text-right tabular-nums text-slate-700"
                        >
                          {telemetry.hardware?.[key]?.[percentile] == null
                            ? 'N/A'
                            : formatHardware(
                                telemetry.hardware[key][percentile],
                                unit
                              )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="mt-4 rounded-lg border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
              GPU hardware metrics are not applicable to CPU-only jobs.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

JobEfficiencySummary.propTypes = {
  job: PropTypes.object.isRequired,
  tasks: PropTypes.arrayOf(PropTypes.object),
};
