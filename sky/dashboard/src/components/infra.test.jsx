import { render, screen } from '@testing-library/react';
import {
  aggregateGPUsForContexts,
  GpuTypeSummaryStrip,
  InfrastructureSection,
} from '@/components/infra';

describe('aggregateGPUsForContexts', () => {
  const gpus = [
    {
      context: 'ssh-lambda',
      gpu_name: 'NVIDIA H100',
      gpu_total: 20,
      gpu_free: 4,
      gpu_not_ready: 1,
    },
    {
      context: 'gke-west',
      gpu_name: 'H100',
      gpu_total: 48,
      gpu_free: 8,
      gpu_not_ready: 2,
    },
    {
      context: 'gke-east',
      gpu_name: 'H100',
      gpu_total: 8,
      gpu_free: 3,
      gpu_not_ready: 0,
    },
    {
      context: 'gke-west',
      gpu_name: 'A100',
      gpu_total: 4,
      gpu_free: 4,
      gpu_not_ready: 0,
    },
  ];

  it('keeps overlapping GPU types separate across context sets', () => {
    expect(aggregateGPUsForContexts(gpus, ['ssh-lambda'])).toEqual([
      {
        gpu_name: 'H100',
        gpu_total: 20,
        gpu_free: 4,
        gpu_not_ready: 1,
      },
    ]);
    expect(aggregateGPUsForContexts(gpus, ['gke-west', 'gke-east'])).toEqual([
      {
        gpu_name: 'H100',
        gpu_total: 56,
        gpu_free: 11,
        gpu_not_ready: 2,
      },
      {
        gpu_name: 'A100',
        gpu_total: 4,
        gpu_free: 4,
        gpu_not_ready: 0,
      },
    ]);
  });

  it('combines section summaries including Slurm without requiring contexts', () => {
    const sections = [
      { gpus: aggregateGPUsForContexts(gpus, ['ssh-lambda']) },
      { gpus: aggregateGPUsForContexts(gpus, ['gke-west']) },
      { gpus: [{ gpu_name: 'NVIDIA H100', gpu_total: 8, gpu_free: 2 }] },
      {}, // Cloud currently has no GPU inventory.
    ];
    expect(
      aggregateGPUsForContexts(
        sections.flatMap((section) => section.gpus || [])
      )
    ).toEqual([
      { gpu_name: 'H100', gpu_total: 76, gpu_free: 14, gpu_not_ready: 3 },
      { gpu_name: 'A100', gpu_total: 4, gpu_free: 4, gpu_not_ready: 0 },
    ]);
    expect(aggregateGPUsForContexts([])).toEqual([]);
  });

  it('excludes contexts removed by workspace filtering', () => {
    expect(aggregateGPUsForContexts(gpus, ['gke-west'])).toEqual([
      {
        gpu_name: 'H100',
        gpu_total: 48,
        gpu_free: 8,
        gpu_not_ready: 2,
      },
      {
        gpu_name: 'A100',
        gpu_total: 4,
        gpu_free: 4,
        gpu_not_ready: 0,
      },
    ]);
  });
});

it('renders GPU counts with free green, allocated yellow, and not ready red', () => {
  render(
    <GpuTypeSummaryStrip
      gpus={[
        { gpu_name: 'H100', gpu_total: 72, gpu_free: 24, gpu_not_ready: 8 },
      ]}
    />
  );
  expect(screen.getByText('H100')).toBeInTheDocument();
  expect(screen.getByText(/of 72 free/)).toHaveTextContent('24 of 72 free');
  expect(screen.getByTitle('24 free')).toHaveClass('bg-green-600');
  expect(screen.getByTitle('40 allocated')).toHaveClass('bg-yellow-500');
  expect(screen.getByTitle('8 not ready')).toHaveClass('bg-red-600');
});

it('renders one unified per-context table without a Requestable column', () => {
  const context = 'ssh-deploy-lambda-1';
  const gpu = {
    context,
    gpu_name: 'H100',
    gpu_requestable_qty_per_node: 8,
    gpu_total: 8,
    gpu_free: 0,
    gpu_not_ready: 0,
  };
  const { container } = render(
    <InfrastructureSection
      title="SSH Node Pool"
      isLoading={false}
      isDataLoaded={true}
      contexts={[context]}
      gpus={[gpu]}
      groupedPerContextGPUs={{ [context]: [gpu] }}
      groupedPerNodeGPUs={{
        [context]: [{ cpu_count: 208, memory_gb: 1772 }],
      }}
      handleContextClick={() => {}}
      isSSH
      isInitialLoad={false}
    />
  );

  const table = container.querySelector('table');
  expect(table).toHaveTextContent('deploy-lambda-1');
  expect(table).toHaveTextContent('GPU Type');
  expect(table).toHaveTextContent('0 of 8 free');
  expect(table).not.toHaveTextContent('Requestable');
  expect(container.querySelectorAll('table')).toHaveLength(1);
});
