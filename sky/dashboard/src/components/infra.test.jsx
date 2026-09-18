import { render, screen } from '@testing-library/react';
import {
  aggregateGPUsForContexts,
  buildNodeJobRows,
  GpuTypeSummaryStrip,
  InfrastructureSection,
  NodeJobHistory,
  paginateNodeJobRows,
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

describe('node job history', () => {
  const context = 'cks-use06a';
  const nodes = [
    { node_name: 'g123422', ip_address: '10.192.206.243' },
    { node_name: 'gd8c0da', ip_address: '10.192.207.5' },
  ];
  const jobs = [
    {
      id: 42,
      name: 'training-current',
      status: 'RUNNING',
      cloud: 'Kubernetes',
      region: context,
      node_names: 'g123422',
      requested_resources: 'H100:8',
      submitted_at: new Date('2026-09-18T12:00:00Z'),
    },
    {
      id: 41,
      name: 'training-finished',
      status: 'SUCCEEDED',
      cloud: 'Kubernetes',
      region: context,
      node_names: 'g-autoscaled-away',
      node_name_lineage: '[["g123422", "g-autoscaled-away"]]',
      submitted_at: new Date('2026-09-17T12:00:00Z'),
    },
    {
      id: 40,
      name: 'other-context',
      status: 'RUNNING',
      cloud: 'Kubernetes',
      region: 'other-context',
      node_names: 'g123422',
    },
  ];

  it('groups current and terminal jobs and retains removed node IDs', () => {
    expect(buildNodeJobRows(jobs, context, nodes)).toEqual([
      {
        node_name: 'g123422',
        ip_address: '10.192.206.243',
        is_present: true,
        current_jobs: [expect.objectContaining({ id: 42 })],
        job_history: [expect.objectContaining({ id: 41 })],
      },
      {
        node_name: 'gd8c0da',
        ip_address: '10.192.207.5',
        is_present: true,
        current_jobs: [],
        job_history: [],
      },
      {
        node_name: 'g-autoscaled-away',
        ip_address: null,
        is_present: false,
        current_jobs: [],
        job_history: [expect.objectContaining({ id: 41 })],
      },
    ]);
  });

  it('paginates history while keeping current jobs visible', () => {
    const rows = buildNodeJobRows(
      [
        ...jobs,
        {
          id: 39,
          name: 'older-job',
          status: 'FAILED',
          cloud: 'Kubernetes',
          region: context,
          node_names: 'gd8c0da',
          submitted_at: new Date('2026-09-16T12:00:00Z'),
        },
      ],
      context,
      nodes
    );

    const firstPage = paginateNodeJobRows(rows, 1, 1);
    expect(firstPage).toMatchObject({
      currentPage: 1,
      totalPages: 2,
      totalCount: 2,
      startIndex: 0,
      endIndex: 1,
    });
    expect(firstPage.rows).toHaveLength(2);
    expect(firstPage.rows[0].current_jobs).toEqual([
      expect.objectContaining({ id: 42 }),
    ]);
    expect(firstPage.rows[0].job_history).toEqual([
      expect.objectContaining({ id: 41 }),
    ]);

    const secondPage = paginateNodeJobRows(rows, 2, 1);
    expect(secondPage.rows).toHaveLength(2);
    expect(secondPage.rows[0].current_jobs).toEqual([
      expect.objectContaining({ id: 42 }),
    ]);
    expect(secondPage.rows[0].job_history).toEqual([]);
    expect(secondPage.rows[1]).toMatchObject({
      node_name: 'gd8c0da',
      is_present: true,
      job_history: [expect.objectContaining({ id: 39 })],
    });
  });

  it('aggregates pipeline tasks before classifying the job', () => {
    const pipelineTasks = [
      {
        id: 50,
        name: 'pipeline',
        status: 'SUCCEEDED',
        cloud: 'Kubernetes',
        region: context,
        node_names: 'g123422',
      },
      {
        id: 50,
        name: 'pipeline',
        status: 'RUNNING',
        cloud: 'Kubernetes',
        region: context,
        node_names: 'g123422',
      },
    ];

    const [node] = buildNodeJobRows(pipelineTasks, context, nodes);
    expect(node.current_jobs).toEqual([
      expect.objectContaining({ id: 50, status: 'RUNNING' }),
    ]);
    expect(node.job_history).toEqual([]);
  });

  it('does not show a recovering job as a current GPU consumer', () => {
    const recovering = [{ ...jobs[0], status: 'RECOVERING' }];

    const [node] = buildNodeJobRows(recovering, context, nodes);
    expect(node.current_jobs).toEqual([]);
    expect(node.job_history).toEqual([
      expect.objectContaining({ id: 42, status: 'RECOVERING' }),
    ]);
  });

  it('renders the node identifier, current IP, current job, and history', () => {
    render(<NodeJobHistory contextName={context} nodes={nodes} jobs={jobs} />);

    expect(screen.getByText('g123422')).toBeInTheDocument();
    expect(screen.getByText('10.192.206.243')).toBeInTheDocument();
    expect(screen.getByText('training-current')).toBeInTheDocument();
    expect(screen.getByText('H100:8')).toBeInTheDocument();
    expect(screen.getAllByText('training-finished')).toHaveLength(2);
    expect(screen.getByText('g-autoscaled-away')).toBeInTheDocument();
    expect(screen.getByText('removed')).toBeInTheDocument();
    expect(screen.getByText('History jobs per page:')).toBeInTheDocument();
    expect(screen.getByRole('combobox')).toHaveValue('10');
    expect(
      screen.getByRole('combobox').querySelectorAll('option')
    ).toHaveLength(4);
    expect(screen.queryByText('other-context')).not.toBeInTheDocument();
  });

  it('uses the server total for history pagination', () => {
    render(
      <NodeJobHistory
        contextName={context}
        nodes={nodes}
        jobs={jobs}
        historyPage={2}
        historyPageSize={10}
        historyTotal={21}
      />
    );

    expect(screen.getByText('11 – 20 of 21')).toBeInTheDocument();
    expect(screen.getByText('training-current')).toBeInTheDocument();
    expect(screen.getAllByText('training-finished')).toHaveLength(2);
  });
});
