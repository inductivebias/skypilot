import PropTypes from 'prop-types';

import { getJobEfficiencySummary } from '@/utils/jobEfficiency';

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

export function JobEfficiencySummary({ job, tasks }) {
  const summary = getJobEfficiencySummary(job, tasks);
  return (
    <div className="col-span-2 rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="mb-3">
        <div className="font-semibold text-slate-900">Efficiency summary</div>
        <div className="text-xs text-slate-500">
          Allocation usage; estimated cost excludes storage, networking, and
          shared overhead.
        </div>
      </div>
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
    </div>
  );
}

JobEfficiencySummary.propTypes = {
  job: PropTypes.object.isRequired,
  tasks: PropTypes.arrayOf(PropTypes.object),
};

JobEfficiencySummary.defaultProps = {
  tasks: [],
};
