function requestedResourceString(job) {
  return job?.requested_resources || job?.resources_str || '';
}

function getNodeCount(job) {
  const match = requestedResourceString(job).match(/^(\d+)x[\[(]/);
  return match ? Number(match[1]) : null;
}

function getAcceleratorsPerNode(job) {
  const structured = job?.accelerators;
  if (structured && typeof structured === 'object') {
    const counts = Object.values(structured).map(Number);
    if (counts.length > 0 && counts.every(Number.isFinite)) {
      return counts.reduce((sum, count) => sum + count, 0);
    }
  }

  const resourceMatch = requestedResourceString(job).match(/^\d+x\[([^\]]+)\]/);
  if (!resourceMatch) return null;
  const acceleratorCounts = resourceMatch[1]
    .split(',')
    .map((part) => part.trim().match(/^([^:]+):(\d+(?:\.\d+)?)$/))
    .filter((match) => match && match[1].toUpperCase() !== 'CPU')
    .map((match) => Number(match[2]));
  if (acceleratorCounts.length === 0) return 0;
  // A requested-resources string may contain heterogeneous alternatives.
  // Without the structured launched value, do not add unlike accelerators.
  if (acceleratorCounts.length > 1) return null;
  return acceleratorCounts[0];
}

export function getGpuCount(job) {
  const acceleratorsPerNode = getAcceleratorsPerNode(job);
  if (acceleratorsPerNode === 0) return 0;
  const nodeCount = getNodeCount(job);
  if (acceleratorsPerNode == null || nodeCount == null) return null;
  return acceleratorsPerNode * nodeCount;
}

export function getSubmittedBy(job) {
  if (job?.submitted_by) return job.submitted_by;
  if (job?.metadata?.submitted_by) return job.metadata.submitted_by;

  const name = job?.name || job?.job_name || '';
  const current = name.match(/-a[1-9]\d*\.([a-z0-9][a-z0-9-]*)$/i);
  if (current) return current[1];
  const transitional = name.match(/-a[1-9]\d*\.by\.([a-z0-9][a-z0-9-]*)$/i);
  if (transitional) return transitional[1];
  return job?.user || job?.user_name || 'N/A';
}

export function getSmIdleSummary(gpuHours, smActiveMean) {
  const activePercent = Number(smActiveMean);
  if (smActiveMean == null || !Number.isFinite(activePercent)) {
    return { percent: null, gpuHours: null };
  }

  const percent = 100 - Math.min(100, Math.max(0, activePercent));
  return {
    percent,
    gpuHours:
      gpuHours == null || !Number.isFinite(Number(gpuHours))
        ? null
        : Number(gpuHours) * (percent / 100),
  };
}

export function getJobEfficiencySummary(job, tasks = []) {
  const records = tasks.length > 0 ? tasks : [job];
  let gpuHours = 0;
  let gpuHoursAvailable = true;
  let estimatedCost = 0;
  let estimatedCostAvailable = true;

  for (const record of records) {
    const durationSeconds = Number(record?.job_duration);
    const gpuCount = getGpuCount(record);
    if (!Number.isFinite(durationSeconds) || gpuCount == null) {
      gpuHoursAvailable = false;
    } else {
      gpuHours += (durationSeconds / 3600) * gpuCount;
    }

    const hourlyCost = Number(record?.estimated_hourly_cost);
    if (
      record?.estimated_hourly_cost == null ||
      !Number.isFinite(hourlyCost) ||
      !Number.isFinite(durationSeconds)
    ) {
      estimatedCostAvailable = false;
    } else {
      estimatedCost += (durationSeconds / 3600) * hourlyCost;
    }
  }

  return {
    gpuHours: gpuHoursAvailable ? gpuHours : null,
    estimatedCost: estimatedCostAvailable ? estimatedCost : null,
    restarts: records.reduce(
      (sum, record) => sum + (Number(record?.recoveries) || 0),
      0
    ),
    submittedBy: getSubmittedBy(job),
  };
}
