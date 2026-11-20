import { apiClient } from '@/data/connectors/client';
import {
  getContextGPUData,
  summarizeKubernetesDemand,
} from '@/data/connectors/infra';

jest.mock('@/data/connectors/client', () => ({
  apiClient: {
    post: jest.fn(),
    get: jest.fn(),
  },
}));

describe('getContextGPUData', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('uses provider-discovered CKS capacity without a pool selector', async () => {
    apiClient.post.mockResolvedValue({
      ok: true,
      headers: { get: () => 'request-id' },
    });
    apiClient.get.mockResolvedValue({
      ok: true,
      json: async () => ({
        return_value: JSON.stringify({
          node_info_dict: Object.fromEntries(
            Array.from({ length: 8 }, (_, index) => [
              `node-${index}`,
              {
                name: `node-${index}`,
                accelerator_type: 'H100',
                total: { accelerator_count: 8 },
                free: { accelerators_available: 0 },
              },
            ])
          ),
          autoscaling_capacity: [
            {
              node_pool: 'flourish-h100-spot',
              accelerator_type: 'H100',
              accelerators_per_node: 8,
              current_nodes: 8,
              min_nodes: 8,
              max_nodes: 20,
            },
          ],
        }),
      }),
    });

    const result = await getContextGPUData('cks-use06a');

    expect(apiClient.post).toHaveBeenCalledWith('/kubernetes_node_info', {
      context: 'cks-use06a',
    });
    expect(result.perContextGPUs).toEqual([
      expect.objectContaining({
        gpu_name: 'H100',
        gpu_total: 64,
        gpu_min: 64,
        gpu_max: 160,
      }),
    ]);
  });

  it('derives heterogeneous GKE headroom from every autoscaled GPU pool', async () => {
    apiClient.post.mockResolvedValue({
      ok: true,
      headers: { get: () => 'request-id' },
    });
    apiClient.get.mockResolvedValue({
      ok: true,
      json: async () => ({
        return_value: JSON.stringify({
          node_info_dict: Object.fromEntries([
            ...Array.from({ length: 1 }, (_, index) => [
              `one-gpu-${index}`,
              {
                name: `one-gpu-${index}`,
                accelerator_type: 'A100',
                total: { accelerator_count: 1 },
                free: { accelerators_available: 0 },
              },
            ]),
            ...Array.from({ length: 4 }, (_, index) => [
              `two-gpu-${index}`,
              {
                name: `two-gpu-${index}`,
                accelerator_type: 'A100',
                total: { accelerator_count: 2 },
                free: { accelerators_available: 0 },
              },
            ]),
            ...Array.from({ length: 3 }, (_, index) => [
              `four-gpu-${index}`,
              {
                name: `four-gpu-${index}`,
                accelerator_type: 'A100',
                total: { accelerator_count: 4 },
                free: { accelerators_available: 1 },
              },
            ]),
          ]),
          autoscaling_capacity: [
            {
              node_pool: 'a100-1',
              accelerator_type: 'A100',
              accelerators_per_node: 1,
              current_nodes: 1,
              min_nodes: 0,
              max_nodes: 10,
            },
            {
              node_pool: 'a100-2',
              accelerator_type: 'A100',
              accelerators_per_node: 2,
              current_nodes: 4,
              min_nodes: 0,
              max_nodes: 10,
            },
            {
              node_pool: 'a100-4',
              accelerator_type: 'A100',
              accelerators_per_node: 4,
              current_nodes: 3,
              min_nodes: 1,
              max_nodes: 3,
            },
          ],
        }),
      }),
    });

    const result = await getContextGPUData('gke_project_us-central1-a_cluster');

    expect(apiClient.post).toHaveBeenCalledWith('/kubernetes_node_info', {
      context: 'gke_project_us-central1-a_cluster',
    });
    expect(result.perContextGPUs).toEqual([
      expect.objectContaining({
        gpu_name: 'A100',
        gpu_total: 21,
        gpu_free: 3,
        gpu_min: 4,
        gpu_max: 42,
      }),
    ]);
  });

  it('summarizes pending pods and exact provider capacity evidence', () => {
    expect(
      summarizeKubernetesDemand(
        [
          { phase: 'Pending', accelerators: 8 },
          { phase: 'Running', accelerators: 8 },
          { phase: 'Pending', accelerators: 0 },
        ],
        [
          {
            node_pool: 'h100ib-rno2a-spot',
            target_nodes: 1,
            queued_nodes: 1,
            in_progress_nodes: 0,
            capacity_condition: {
              status: 'False',
              reason: 'CWInsufficientCapacity',
              message: '1 pending node awaits spot capacity.',
              lastTransitionTime: '2026-10-25T03:00:05Z',
            },
          },
        ]
      )
    ).toEqual({
      pendingPods: 1,
      pendingGpus: 8,
      runningPods: 1,
      targetNodes: 1,
      queuedNodes: 1,
      inProgressNodes: 0,
      capacityIssues: [
        {
          nodePool: 'h100ib-rno2a-spot',
          reason: 'CWInsufficientCapacity',
          message: '1 pending node awaits spot capacity.',
          lastTransitionTime: '2026-10-25T03:00:05Z',
        },
      ],
    });
  });
});
